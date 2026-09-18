# 5. Evaluate the physics once per row, never per window frame

Date: 2026-09-18
Status: accepted

## Context

Slice 4's reliability foundation needed "the worst rolling 24 hours" — a
moving-window average of capacity factor over hourly data. The obvious
phrasing calls the model function inside the window:

```sql
avg(pv_capacity_factor(global_tilted_irradiance, temperature_2m))
    OVER (PARTITION BY site_id ORDER BY ts
          ROWS BETWEEN 23 PRECEDING AND CURRENT ROW)
```

That ran for **8 minutes without finishing a single query**, against the same
526,032 rows the continuous aggregate had just chewed through in 60 seconds.

Two things compound:

1. **A moving-window `avg()` over floating point cannot use an inverse
   transition function.** PostgreSQL deliberately refuses to, because
   subtracting a value back out of a float accumulator is not numerically
   safe. So instead of sliding the frame, it recomputes the whole 24-row
   frame for every row: 526,032 x 24 ≈ **12.6 million evaluations** rather
   than 526,032.
2. **`wind_capacity_factor` is expensive per call.** It has to be: the
   turbine curve is embedded as constant arrays inside the function body
   (there is no alternative — an `IMMUTABLE` function cannot query a table,
   and a cagg demands immutability, see specs/slice-4.md). Each call
   `unnest`es a 50-element array *twice*, in correlated subqueries with
   `ORDER BY ... LIMIT`.

Neither is a defect on its own. The per-call cost is fine at 526k calls —
the cagg proves it. It is the 24x multiplier that turns fine into ruinous.

## Decision

**Any query that windows over the generation models computes each hour's
capacity factor once, in a CTE marked `MATERIALIZED`, and windows over the
result.**

```sql
WITH hourly AS MATERIALIZED (
    SELECT site_id, ts,
           pv_capacity_factor(...)   AS pv,
           wind_capacity_factor(...) AS wind
    FROM weather_hour
)
SELECT ... avg(pv) OVER (...) FROM hourly
```

All four reliability queries then complete in **2m21s total**.

`MATERIALIZED` is load-bearing, not decoration. Since PostgreSQL 12, a CTE
is inlined by default — drop the keyword and the optimiser folds the function
calls straight back inside the window frame, restoring the original
behaviour with no visible change to the query text.

## Consequence

- Slice 5's reliability view must follow this shape. A live view that calls
  the models inside a window will appear to hang.
- The rule generalises past window functions: **the physics is cheap once per
  row and ruinous per frame.** Anything that would evaluate a model function
  more than once per source row should compute it once and reuse it.
- This is also the argument for the daily cagg pulling its weight beyond
  pedagogy: the two coarser reliability metrics (worst rolling 7 days,
  P50/P90 annual) read pre-evaluated capacity factors from `daily_cf` and are
  near-instant, while the two hourly ones pay the 526k evaluation cost every
  time. If hourly metrics ever need to be interactive, the answer is to
  materialise hourly capacity factor the same way — not to optimise the
  query further.
- 2m21s is acceptable for a foundation proof run from a script. It is not
  acceptable behind an HTTP request, which is a slice-5 problem with a known
  shape rather than an open question.
