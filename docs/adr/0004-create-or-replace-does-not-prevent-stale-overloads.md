# 4. `CREATE OR REPLACE FUNCTION` does not prevent stale overloads

Date: 2026-09-18
Status: accepted — supersedes ADR 0002 on one claim

## Context

ADR 0002 established that the generation models live as SQL functions
generated from Python constants, and claimed:

> `CREATE OR REPLACE` is idempotent, so it's safe to call on every connect
> (ingest, tests, later the API) and the database can never hold a
> definition that disagrees with the constants — there's nothing to keep in
> sync by hand.

**That claim is false.** `CREATE OR REPLACE FUNCTION` is scoped to an exact
signature. Replace a function whose argument types have changed and the old
overload is not replaced — it simply survives alongside the new one.

This had already happened, unnoticed, since slice 2. During slice 2 CP1 the
PV function was first written as `pv_capacity_factor(real, real)`; psycopg
sends Python floats as `double precision`, the tests failed to resolve it,
and the signature was changed to `(double precision, double precision)`.
The `(real, real)` version stayed in the database for the rest of slices 2
and 3.

Three facts make this worse than an untidy leftover, all verified live on
TimescaleDB 2.17.2 / PG17:

1. **`weather_hour`'s columns are `real`** (float4, chosen in slice 1 to
   halve the hypertable). So `pv_capacity_factor(global_tilted_irradiance,
   temperature_2m)` is a `(real, real)` call, and PostgreSQL prefers an
   exact type match over implicit widening — **the stale overload wins.**
   Confirmed by a probe cagg, whose dependency error named it outright:
   `_partial_view_37 depends on function pv_capacity_factor(real,real)`.
2. **The test suite cannot see it.** Every model test calls through psycopg
   with Python floats — `double precision` — so it exercises the canonical
   function while the column-based production path uses the stale one.
   Green tests, wrong query path.
3. **A dependent continuous aggregate blocks the cleanup.** Once a cagg
   references a function, `DROP FUNCTION` fails without CASCADE. Slice 4's
   cagg would have cemented the stale overload in place.

No wrong results had been produced: both bodies carried identical constants,
because the signature change happened before any coefficient changed. The
defect was a latent one — the first edit to `NOCT` or `GAMMA` would have
updated only the `double precision` function while every cagg and
column-based query silently kept the old coefficients.

## Decision

**`apply_models` creates the canonical function, then drops every other
overload of that name.**

- Each function's parameters are declared once in `models/ddl.py` as
  `(name, sql_type)` pairs. The `CREATE` signature and the argument-type
  list used to identify the canonical overload are both derived from them,
  so they cannot drift.
- `drop_stale_overloads_ddl(name, params)` emits a `DO` block that drops
  every `public` function of that name except the canonical signature.
- **Create first, drop second.** The canonical function is replaced in
  place rather than dropped and recreated, so it stays valid for a
  dependent continuous aggregate; only genuine leftovers are removed.
- A stale overload that a cagg depends on will **refuse to drop**. That
  error is correct and must surface — it means a cagg was built against the
  wrong function, which is precisely the condition this ADR exists to
  prevent.

## Consequence

- ADR 0002's design stands; only its "can never disagree" claim is
  replaced by this one. The guarantee now comes from the drop, not from
  `CREATE OR REPLACE`.
- Any future change to a model function's *signature* is now safe. A change
  to its *body or constants* was always safe, and still is.
- Slice 4 can build its cagg knowing exactly one function of each name
  exists.
- Regression coverage lives in `tests/test_models_ddl.py`, including a test
  that calls with explicit `::real` arguments — the only kind of call that
  can catch this class of bug, since float-passing tests cannot.
