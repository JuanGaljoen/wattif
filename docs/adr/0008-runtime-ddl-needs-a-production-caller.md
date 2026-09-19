# 8. Runtime DDL needs a caller that isn't the test suite

Date: 2026-09-19
Status: accepted

## Context

Two packages install structures into the database at runtime rather than in
`db/schema.sql`, for a good reason recorded in
[ADR 0002](0002-generation-models-as-generated-sql-functions.md) and
`timescale/__init__.py`: `schema.sql` runs once, via
`docker-entrypoint-initdb.d`, on an empty volume — and the database holds
half a million rows nobody is dropping to add an aggregate.

- `models.apply_models(cur)` — the PV and wind SQL functions
- `timescale.apply_timescale(cur)` — `daily_cf`, compression, the policies

Through slices 2, 3 and 4 that worked, and the design was sound. What went
unnoticed is that **nothing outside the test suite ever called either
function.** A grep at slice 5a's Verify found callers only in
`tests/conftest.py`, `tests/test_models_ddl.py` and `tests/test_timescale.py`.

The dev database had the functions and the aggregate because *running the
tests* put them there. That is invisible while the only consumers are the
tests and a developer who has run them. It becomes visible the moment
something reads those structures in production — which is what the API is.

Verified on a scratch database built from `db/schema.sql` alone:

```
daily_cf = 0    pv_capacity_factor = 0
GET /api/sites/1/daily  ->  500
```

Not a 404, not an empty series — a 500, because the relation does not exist.

The general shape: **a structure whose only installer is a test is a
structure that exists only where tests have run.** The test suite is not a
deployment mechanism, and it is easy to mistake one for the other when both
happen on your laptop.

## Decision

**Runtime DDL is applied on application startup**, in the FastAPI lifespan,
before the app serves anything:

```python
open_pool()
with ddl_cursor() as cur:
    apply_models(cur)
    apply_timescale(cur)
```

Both are idempotent by design, so this is a no-op against an established
database.

**Deliberately not defensive.** `ensure_cagg` raises on a version-marker
mismatch (ADR 0004's lesson applied to caggs), and that exception is allowed
to abort startup. An API that silently serves an aggregate built from a
different `SELECT` is exactly the failure the marker exists to prevent; a
refusal to boot is the correct louder outcome.

**The DDL gets its own cursor.** `apply_models` and `apply_timescale` index
rows positionally (`row[0]`), so handing them the `dict_row` cursor the read
endpoints use raises `KeyError: 0`. `api/db.py` exposes `ddl_cursor()`
(tuple rows) separately from `cursor()` (dict rows).

## Consequence

- **Structures are now self-installing; data is not.** A clean clone gets
  the functions, the aggregate and the policies, and answers `[]` and `404`
  instead of erroring. It still has no weather. Six markers need the
  backfill (~1,570 API calls) or PLAN.md's slice-6 seed dump. The slice-5a
  success criterion was amended to say "against a seeded database" rather
  than pretend otherwise.
- **The container image must carry `models/` and `data/`.** They were
  trimmed out as unused during the same Verify, minutes before the lifespan
  change put the dependency back — and local tests, which never run in the
  container, stayed green while the image would not boot. The image's import
  graph is not the test suite's import graph.
- The general rule, beyond this repo: **if the only thing that installs a
  structure is a test, the structure does not exist in production.** Grep
  for the callers of anything described as "applied at runtime".
