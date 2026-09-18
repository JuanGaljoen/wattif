# 1. A continuous aggregate cannot group on `local_date`

Date: 2026-09-18
Status: accepted (as a correction — supersedes PLAN.md's original design)

## Context

PLAN.md's schema section, written at slice 1, states as one of three "already
made, don't relitigate" decisions:

> **The daily cagg groups on `local_date`.** UTC daily buckets split the solar
> day for any site off Greenwich, and `time_bucket`'s timezone must be
> constant inside a continuous aggregate.

Verified live against TimescaleDB 2.17.2 while designing slice 2:

```sql
CREATE MATERIALIZED VIEW cagg_c WITH (timescaledb.continuous) AS
SELECT site_id, local_date, avg(global_tilted_irradiance) AS g
FROM weather_hour GROUP BY site_id, local_date WITH NO DATA;
-- ERROR:  continuous aggregate view must include a valid time bucket function
```

A continuous aggregate must group on `time_bucket(...)` applied to the
hypertable's time column — grouping on any other column, however carefully
derived at ingest, is rejected outright.

The obvious repair — bucket on `ts` with a per-site timezone — also fails:

```sql
SELECT time_bucket('1 day', w.ts, timezone => s.timezone) ...
-- ERROR:  only immutable expressions allowed in time bucket function
```

A **constant** timezone literal works fine (`time_bucket('1 day', ts,
timezone => 'Africa/Johannesburg')`); a per-row value from a join does not,
because `time_bucket`'s timezone argument must be immutable at plan time.

## Decision

Record this as a known-invalid design rather than let it stand uncorrected in
PLAN.md. The constraint PLAN.md identified (UTC buckets split the solar day)
is real; the fix it chose isn't available.

**Not solved here** — slice 4 needs one of:
- One cagg per distinct site timezone (a small, known set for this project).
- Bucket on `ts` in UTC and accept a UTC daily boundary (loses the "true local
  day" property `local_date` was written for).
- Materialize the local shift into `ts` itself somehow, so a single UTC
  `time_bucket` reproduces local days — not investigated.

## Consequence

Anyone building the daily cagg (slice 4) must re-decide this; PLAN.md's
schema section should be read as **superseded** on this one point, the other
two decisions in that section (cagg stores capacity factor, cagg is over the
generation expression) still hold.
