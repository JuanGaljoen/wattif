# 2. Generation models are generated SQL functions, not Python functions

Date: 2026-09-18
Status: accepted

## Context

Slice 2 needed to decide where the PV and wind physics lives. Three shapes
were on the table:

1. Plain Python functions, with a hand-written SQL expression added later
   (slice 4) for the continuous aggregate.
2. The physics as hand-written SQL from the start, tested via the DB.
3. Both, hand-written independently, pinned together by a cross-check test.

Option 1 was the initial lean — PLAN.md:149 asks for "pure functions" — but
it doesn't survive contact with PLAN.md:138: `f(avg(x)) != avg(f(x))` for the
nonlinear temperature derate, so the aggregate **must** evaluate the
expression per hour, inside the cagg. Python-only defers that requirement to
slice 4 rather than removing it, and by then the two implementations (Python
for tests/API, SQL for the cagg) would already be drifting.

Option 3 duplicates every coefficient by construction and leans on a parity
test to catch drift after the fact — the same shape a prior project's ADR
(`ring-cad-app`, 2026-06-28) already rejected: constants belong in one place,
imported, never duplicated.

## Decision

**The physics exists exactly once, as a SQL `IMMUTABLE` function, generated
from Python-owned constants and applied at runtime.**

- `models/constants.py` is the single source for every coefficient.
- `models/ddl.py` builds `CREATE OR REPLACE FUNCTION ...` strings by
  interpolating those constants directly into the SQL text.
- `models/apply_models(cur)` executes the generated DDL. `CREATE OR REPLACE`
  is idempotent, so it's safe to call on every connect (ingest, tests, later
  the API) and the database can never hold a definition that disagrees with
  the constants — there's nothing to keep in sync by hand.
- Tests drive the functions through psycopg, against a session fixture that
  applies the DDL once. The seam is the SQL function; the Python constants
  are never asserted against directly.

Verified live before freezing: a user-defined `IMMUTABLE` SQL function *is*
accepted inside a `timescaledb.continuous` materialized view on 2.17.2 — the
option this design depends on being available.

**A consequence discovered during Forge, not anticipated at Design:** the
wind turbine power curve (50 points) cannot live in a lookup table either —
an `IMMUTABLE` function cannot query a table, and the cagg demands
immutability. So `models/curve.py` renders the curve as two constant
`ARRAY[...]` literals embedded directly in the generated function body.
Ugly to read as raw SQL; acceptable because it's generated, never
hand-maintained.

## Consequence

- Model tests need the database running — there is no fast, DB-free unit
  test tier for the physics. Accepted: `docker compose up -d` is already the
  first line of this project's own verify loop.
- Slice 4's cagg calls these functions directly; it does not need to
  reimplement or re-derive the expressions.
- Adding a third model (e.g. a different turbine class, or storage) follows
  the same shape: constants in `models/constants.py`, DDL generator in
  `models/ddl.py`, wired into `apply_models`.
