# wattif — working notes

Ten years of real weather → what a solar or wind farm at a point on the map
would have generated. Python · TimescaleDB · Postgres.

**[PLAN.md](PLAN.md) owns the reasoning**: build order, the models and their
coefficients, hosting, what's still open. This file holds only what must never
be got wrong, and the words the code assumes you know.

## The traps — all four verified live, all four silent

- **Azimuth is 0 = SOUTH, 180 = NORTH.** Southern-hemisphere sites face north.
  Karoo midwinter midday: 89.6 W/m² at 0 vs 841.9 at 180 — a **9.4× error that
  looks plausible**. (PLAN.md § The two traps)
- **`wind_speed_100m` is km/h, not m/s.** Divide by 3.6 before any power curve.
- **`timezone=auto`, never `timezone=UTC`.** UTC makes the API report
  `utc_offset_seconds = 0` for every site, which silently collapses `local_date`
  onto the UTC date. The trade: `time` comes back as local wall time.
- **The archive returns HTTP 200 with a plain-text error body**
  (`...timeoutReached`). `raise_for_status()` does not catch it. Validate the
  body starts with `latitude,` and retry — it is transient.

## Physics, not to be re-derived

- **GTI is the POA figure.** Never sum `direct + diffuse` — those are horizontal
  and 10–25% wrong.
- **Nine variables is a cost ceiling**, not a preference: `max(1, vars/10)`
  keeps the call weight at 1.0. A tenth pushes it to 1.1×.
- **Every coefficient gets a citation**, and the README splits verified-and-how
  from assumed-and-why.

## Vocabulary

- **GTI / POA** — global tilted irradiance, plane-of-array. W/m² on the panel.
- **site-year** — one (site, year) fetch. The unit of ingest, retry and resume.
- **`ts` vs `local_date`** — `ts` is UTC instant; `local_date` is the site's own
  calendar day, written at ingest because UTC buckets split the solar day.
- **capacity factor** — dimensionless output ÷ rated. What the cagg stores;
  `P_rated` multiplies in at read time.
- **chunk / cagg** — Timescale's time partition (1 month here) and continuous
  aggregate.

## Conventions the schema encodes

- Weather columns are **`real` (float4)**, not double — precision is far below
  float8 and it halves the hypertable.
- A **hypertable PK must include the time column**: `(site_id, ts)`.
- **`tilt_deg` / `azimuth_deg` are fetch params frozen on `site`** — they shape
  the GTI the API returns, so changing one means re-fetching that site.
- **One `ingest_job` row per (site, year)**, so a crash costs one year.
- Ingest is **idempotent**: COPY to a temp stage, then
  `INSERT … ON CONFLICT (site_id, ts) DO NOTHING`. A re-run must insert 0.

## Proving a slice

```sh
docker compose up -d
.venv/bin/python -m ingest.load --year 2024
docker compose exec -T db psql -U postgres -d resource -f /dev/stdin < db/verify.sql
```

`db/verify.sql` **prints** numbers, it does not assert them — read them. The
seasonality in the monthly `time_bucket` rollup is the real check: southern
sites must peak Dec–Feb and bottom out Jun–Jul. An inverted curve means the
azimuth trap bit.

## Looking at the database

**TablePlus is the default way in** — browsing 526k rows is what a GUI is
for. Any Postgres client works; the port is published in
`docker-compose.yml`, so nothing needs configuring on the container:

```
Host 127.0.0.1 · Port 5432 · User postgres · Password postgres · DB resource
```

Its schema dropdown also lists `_timescaledb_internal`, where the 122
`_hyper_1_*_chunk` tables and their compressed twins live. That is the
partitioning made literal — worth looking at once.

For a shell instead, **from the repo root** (`docker compose` resolves the
compose file from the working directory) — or `docker exec -it wattif-db-1
psql -U postgres -d resource` from anywhere:

```sh
docker compose up -d
docker compose exec db psql -U postgres -d resource
```

**An agent cannot run that one**: Claude Code's Bash has no TTY, so
interactive psql hangs. Agents use the one-shot form —
`docker compose exec -T db psql -U postgres -d resource -c "<sql>"`.

Useful `psql` meta-commands: `\dt` (tables), `\d+ weather_hour` (columns),
`\dv` (views — `daily_cf` shows here), `\x` (expanded rows), `\timing`,
`\q`. In psql, `\e` opens the last query in `$EDITOR`.

**Three tables and one aggregate**: `site` (6 rows — ids are 1, 75, 85, 95,
105, 115, *not* 1–6), `weather_hour` (526,032 rows), `ingest_job` (one row
per site-year), and the `daily_cf` cagg (21,918 rows).

**Reading raw rows, without the two traps biting:** `ts` is UTC, so a local
day starts at `22:00` on the *previous* calendar date — that is why
`local_date` is its own column, and why `daily_cf.day` reads as `…22:00`.
Filter on `local_date`, not on `date(ts)`.

### Seeing Timescale actually do its job

These are the introspection views worth knowing; they are where the
hypertable stops being "a table" and starts being visible machinery.

```sql
-- the hypertable and its partitions
SELECT hypertable_name, num_chunks, compression_enabled
  FROM timescaledb_information.hypertables;
SELECT chunk_name, range_start, range_end, is_compressed
  FROM timescaledb_information.chunks
 WHERE hypertable_name = 'weather_hour' ORDER BY range_start;

-- what compression bought (README quotes the measured figure)
SELECT * FROM hypertable_compression_stats('weather_hour');
SELECT pg_size_pretty(hypertable_size('weather_hour'));

-- the cagg, and the background jobs that maintain both
SELECT view_name, materialized_only, finalized
  FROM timescaledb_information.continuous_aggregates;
SELECT job_id, application_name, schedule_interval, config
  FROM timescaledb_information.jobs WHERE job_id >= 1000;
```

**The single best demonstration is `EXPLAIN`.** A time-bounded query names
only the chunks it touches — 2 of 122 for one month — and on compressed
chunks the plan reads `Custom Scan (DecompressChunk)` with a `Vectorized
Filter`:

```sql
EXPLAIN (COSTS OFF) SELECT * FROM weather_hour
 WHERE ts >= '2024-06-01' AND ts < '2024-07-01' AND site_id = 1;
```

Drop the `ts` bounds and all 122 chunks appear in the plan. That difference
*is* chunk exclusion, and it is the reason the time column is in the PK.

## Testing convention

**A write-test's fixtures are synthetic (`"__test_only__"`), never "a real
entity that happens to have no data yet."** The `tx` fixture only isolates
what a test *writes*, not what already exists — and any table this project
seeds with real data (sites, and whatever comes later) can't be assumed
empty; slice 3 hit this twice. See [`docs/adr/0003`](docs/adr/0003-test-isolation-uses-synthetic-fixtures.md).

## Generation models (slice 2)

PV and wind are each **one SQL function, generated from Python-owned
constants and applied idempotently at runtime** — never edited by hand, never
duplicated. See [`docs/adr/0002`](docs/adr/0002-generation-models-as-generated-sql-functions.md)
for why, and `models/` for the pattern to extend when a third model is added.

**`apply_models` creates the canonical function, then drops every other
overload of that name** — `CREATE OR REPLACE` alone is signature-scoped and
silently leaves an old signature behind, which `real` columns then bind to in
preference. Declare each function's params once in `models/ddl.py`; never
hand-write a signature twice. See [`docs/adr/0004`](docs/adr/0004-create-or-replace-does-not-prevent-stale-overloads.md).

`docs/adr/` now holds this project's dated decisions and lessons — check it
during Recall alongside PLAN.md.

## The aggregate layer (slice 4)

`daily_cf(site_id, day, pv_cf, wind_cf, hours)` is a continuous aggregate over
the **generation expression**, bucketed on a **constant** timezone literal
(`Africa/Johannesburg`). Constant only because all six sites share one
timezone — **a site in another timezone invalidates this design**, not just
this query (see [`docs/adr/0001`](docs/adr/0001-cagg-cannot-group-on-local-date.md)).

**Querying rule: evaluate the physics once per row, never per window frame.**
A moving-window `avg()` over floats can't use inverse transitions, so calling
a model function inside the frame recomputes it 24× per row — 8 minutes
versus seconds. Window over a CTE marked `MATERIALIZED` (the keyword is
load-bearing; without it PG inlines it straight back).
See [`docs/adr/0005`](docs/adr/0005-evaluate-the-physics-once-per-row-never-per-window-frame.md).

A cagg has **no `OR REPLACE`**, so `daily_cf` carries a version marker as a
view comment and `apply_timescale` refuses to run against a mismatch. Bump
`CAGG_VERSION` whenever the SELECT changes.

**After changing a model coefficient**, the cagg serves stale values until an
explicit full-range `refresh_daily_cf(conn)` — the refresh policy only covers
a moving recent window.

## Known gaps

- [x] ~~PLAN.md's "cagg groups on `local_date`" design is invalid~~ — resolved
      in slice 4 by the constant timezone literal, available because all six
      sites share one timezone. PLAN.md:129-131 remains formally superseded by
      [`docs/adr/0001`](docs/adr/0001-cagg-cannot-group-on-local-date.md).
- [x] ~~`ingest_job.rows` records rows inserted *this run*, not rows held~~ —
      fixed in slice 3 (`ingest/backfill.py`, `backfill_one`).
