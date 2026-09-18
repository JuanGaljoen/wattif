# SLICE-4 — Daily continuous aggregate + compression

Classification: **feature** (three checkpoints)
Status: Design frozen 2026-09-18 · not yet built
No Jira. This file is the source of truth; the branch's commits are the rest.

## Why

526,032 hourly rows and two model functions exist, but nothing aggregates
them. This slice builds the daily layer slice 5's reliability view reads,
and compresses the hypertable underneath it. PLAN.md build order item 4.

Reliability was decided at Understand: **longest lull, hourly-led** — worst
rolling 24h, worst rolling week, hours/year below 10% output, with P50/P90
annual yield alongside. Hourly-resolution metrics query `weather_hour`
directly; the cagg serves the daily-and-coarser ones.

## Success criteria (the bar Verify checks)

- [ ] `daily_cf` exists with columns (site_id, day, pv_cf, wind_cf, hours)
- [ ] It materialises **exactly 21,918 rows** (6 sites x 3,653 local days:
      2016-2025, leap years 2016/2020/2024)
- [ ] **Every bucket has hours = 24** — timezone-aware bucketing aligns local
      days with the data span, so there are no partial buckets
- [ ] Cagg values match a direct computation over `weather_hour` for a
      sampled site-month (to float tolerance)
- [ ] Compression enabled on `weather_hour`; ratio measured and recorded
- [ ] **Backfill idempotency still holds after compression** — a re-run
      inserts 0 rows and does not error
- [ ] A compression policy and a cagg refresh policy both exist
- [ ] One representative query of each reliability kind runs and returns
      sensible results (worst rolling 24h, hours below 10% — from
      weather_hour; worst rolling 7d, P50/P90 annual — from the cagg)
- [ ] All 26 existing tests stay green

## Approach

**A new `timescale/` package**, mirroring `models/`: the TimescaleDB
structures that must be applied at runtime because `db/schema.sql` only runs
on an empty volume (`docker-entrypoint-initdb.d`), and the database holds
526,032 rows we are not dropping.

```
timescale/__init__.py     apply_timescale(cur)  -- the whole public interface
timescale/cagg.py         daily_cf DDL, version marker, refresh policy
timescale/compression.py  compress settings + compression policy
```

### The aggregate

```sql
CREATE MATERIALIZED VIEW IF NOT EXISTS daily_cf
WITH (timescaledb.continuous) AS
SELECT site_id,
       time_bucket('1 day', ts, timezone => 'Africa/Johannesburg') AS day,
       avg(pv_capacity_factor(global_tilted_irradiance, temperature_2m))   AS pv_cf,
       avg(wind_capacity_factor(wind_speed_100m, surface_pressure,
                                temperature_2m))                           AS wind_cf,
       count(*)                                                            AS hours
FROM weather_hour
GROUP BY site_id, day;
```

The constant timezone literal is ADR 0001's repair, verified live. It is
constant precisely because all six sites share one timezone — a deliberate
slice-3 choice. **Adding a site in another timezone breaks this design**, not
just this query; see Risks.

One cagg, not two: solar-vs-wind comparison at a site is the product's core
question, so both belong in one row. A PV coefficient change re-materialises
wind too — seconds at ~22k rows.

`hours` earns its place as a partial-bucket guard: any value other than 24
means the bucketing or the data span is not what we think.

### Idempotent creation, and the ADR 0004 trap

A cagg has **no `OR REPLACE`**. `CREATE MATERIALIZED VIEW IF NOT EXISTS`
works (verified — second run emits a notice and skips), but that is exactly
the shape ADR 0004 just bit us with: an idempotent create that silently
keeps an older definition.

Guard: the view carries a **version marker** as a comment.

```sql
COMMENT ON MATERIALIZED VIEW daily_cf IS 'wattif:daily_cf:v1';
```

`apply_timescale` compares the stored marker against `CAGG_VERSION` in the
code. On mismatch it **raises**, naming the drop-and-recreate command, rather
than proceeding against a stale definition. Bump the version whenever the
SELECT changes.

This is a tripwire, not a proof: it catches a changed definition whose
version was bumped, and cannot catch an edit made without bumping it.

### Compression

```sql
ALTER TABLE weather_hour SET (timescaledb.compress,
    timescaledb.compress_segmentby = 'site_id',
    timescaledb.compress_orderby   = 'ts DESC');
SELECT add_compression_policy('weather_hour', INTERVAL '30 days',
                              if_not_exists => true);
```

`segmentby = site_id` measured **3.4x** on a probe chunk (600 kB -> 176 kB).
`compress_after => 30 days` is a **judgement call, not a sourced constant**:
recent chunks stay uncompressed so writes are cheap, everything older is
read-only in practice. Every chunk here is historical, so effectively all of
it compresses.

Verified during Understand, so not a risk: `INSERT ... ON CONFLICT DO
NOTHING` works against compressed chunks, new rows insert into them, and a
cagg refreshes over them.

### Refresh policy

```sql
SELECT add_continuous_aggregate_policy('daily_cf',
    start_offset => INTERVAL '90 days', end_offset => INTERVAL '1 day',
    schedule_interval => INTERVAL '1 day', if_not_exists => true);
```

Both policies use `if_not_exists => true` so `apply_timescale` stays
idempotent.

**The policy does not re-materialise history.** After a model coefficient
changes, the cagg holds values computed by the old function until an explicit
full-range refresh:

```sql
CALL refresh_continuous_aggregate('daily_cf', NULL, NULL);
```

Verified: an explicit range refresh *does* pick up a replaced function; no
drop-and-recreate needed. This goes in the README as an operational rule.

## Checkpoints

- [x] **CP1 — The aggregate.** `daily_cf`, version marker, apply path,
      materialised and checked against direct computation.
      files: `timescale/__init__.py`, `timescale/cagg.py`,
      `tests/test_timescale.py`
- [x] **CP2 — Compression + policies.** Compression settings, both policies,
      and the regression that matters: backfill idempotency after compression.
      files: `timescale/compression.py`, `timescale/__init__.py`,
      `tests/test_timescale.py`
- [ ] **CP3 — Reliability foundation + docs.** Representative queries proving
      all four metric kinds work; verify.sql and README updated.
      files: `db/reliability.sql`, `db/verify.sql`, `README.md`

## Tests — seams and what each pins

Seam is `apply_timescale(cur)` and the resulting objects queried through
psycopg. Write-tests use the `tx` rollback fixture (docs/adr/0003).

| Test | Pins |
|---|---|
| `test_cagg_has_expected_columns` | the contract above, by name |
| `test_cagg_matches_direct_computation` | cagg avg == direct avg, sampled site-month |
| `test_every_bucket_has_24_hours` | no partial buckets across the whole cagg |
| `test_cagg_row_count_is_21918` | 6 sites x 3,653 local days |
| `test_apply_is_idempotent` | applying twice: no error, still one cagg |
| `test_version_mismatch_raises` | a bumped marker refuses to proceed silently |
| `test_compression_enabled_with_policy` | settings and policy both present |
| `test_backfill_idempotent_after_compression` | re-run inserts 0 post-compression |

## Risks

1. **The constant timezone literal is load-bearing.** It only works because
   all six sites share `Africa/Johannesburg`. A seventh site elsewhere
   invalidates the cagg design (ADR 0001's constraint returns in full). If
   the site list ever goes international, this is the thing that breaks —
   record it loudly rather than discovering it then.
2. **The version marker is a tripwire, not a guarantee.** It catches a bumped
   version, not a silent edit. Mitigation is the habit, not the mechanism.
3. **Compression makes the hypertable effectively read-optimised.** Inserts
   into compressed chunks work (verified) but are slower. Re-running a full
   backfill after compression will be noticeably slower than slice 3's run —
   expected, not a fault.

## Carried forward (no tracker; do not lose)

- [ ] PLAN.md "Still open" is stale on three of four items: sites, turbine
      curve and now "what does reliable mean" are all answered. Only the
      project name entry is obsolete (it's named). Worth a tidy pass.
- [ ] PLAN.md:129-131's `local_date` cagg decision remains formally
      superseded by ADR 0001 — `local_date` is now unused by the cagg,
      though ingest still writes it. Whether it stays is a slice-5 question.
