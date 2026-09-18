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

## Generation models (slice 2)

PV and wind are each **one SQL function, generated from Python-owned
constants and applied idempotently at runtime** — never edited by hand, never
duplicated. See [`docs/adr/0002`](docs/adr/0002-generation-models-as-generated-sql-functions.md)
for why, and `models/` for the pattern to extend when a third model is added.

`docs/adr/` now holds this project's dated decisions and lessons — check it
during Recall alongside PLAN.md.

## Known gaps

- [ ] **PLAN.md's "cagg groups on `local_date`" design is invalid** — verified
      live, blocks slice 4. See [`docs/adr/0001`](docs/adr/0001-cagg-cannot-group-on-local-date.md).
- [ ] **`ingest_job.rows` records rows inserted *this run*, not rows held for
      the site-year** — so a safe re-run overwrites 8784 with 0. Misleading the
      moment it's used to audit a backfill. Fix with a `count(*)` in slice 3.
