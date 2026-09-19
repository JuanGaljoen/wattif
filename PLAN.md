# wattif

Pick a point on a map. See what a solar or wind farm there would have generated,
hour by hour, over ten years of real weather — and how reliable it would have been.

*Watt if you'd built it there?*

**Goal.** A small, finished, good-looking product on a real time-series stack:
Python, TimescaleDB, Postgres. One question, answered well.

**Zero budget.** Nothing in this plan costs money to build or to keep. That is a
constraint on hosting, not on ambition.

**What makes it good** is the physics being right and cited, and TimescaleDB
visibly doing real work. Not the infrastructure. Scope accordingly.

> TimescaleDB is the *subject* here, not a requirement the data imposed — plain
> Postgres would handle this row count. Building something to learn a technology
> is a fine reason. Say so in the README rather than pretending otherwise.

---

## The data — Open-Meteo archive (verified 2026-09-17)

`https://archive-api.open-meteo.com/v1/archive` · no auth · history to 1940 ·
CC BY 4.0, **attribution required in the UI and README**.

Free tier: 600/min · 5,000/hour · **10,000/day**. Weighted:
`calls = (days / 14) * max(1, vars / 10)`.

**Target: 6 sites × 10 years ≈ 525k rows ≈ 1,570 calls (16% of a day).** Enough
to demonstrate hypertables, aggregates and compression, small enough that the
backfill is one relaxed run with room to retry.

**Fetch as CSV** — feeds `COPY` directly, and `elevation` arrives free in the
metadata header:

```sh
VARS="global_tilted_irradiance,shortwave_radiation,direct_radiation,\
diffuse_radiation,direct_normal_irradiance,temperature_2m,\
wind_speed_100m,wind_direction_100m,surface_pressure"

curl -sS -G "https://archive-api.open-meteo.com/v1/archive" \
  --data-urlencode "latitude=-32.25"      --data-urlencode "longitude=22.55" \
  --data-urlencode "start_date=2015-01-01" --data-urlencode "end_date=2024-12-31" \
  --data-urlencode "hourly=$VARS"         --data-urlencode "timezone=auto" \
  --data-urlencode "tilt=32"              --data-urlencode "azimuth=180" \
  --data-urlencode "format=csv" -o site.csv
```

CSV shape: lines 1–2 metadata, line 3 blank, line 4 header (units in
parentheses), line 5+ data. Parse defensively rather than hardcoding `SKIP 4`.

Nine variables keeps the weighting at 1.0×. **A tenth pushes it over** — note it.

### Two things the live API taught us (slice 1, 2026-09-18)

**`timezone=auto`, never `timezone=UTC`.** Asked for UTC, the API dutifully
reports `utc_offset_seconds = 0` for every site on earth — which would make
`local_date` identical to the UTC date and silently defeat the column's whole
purpose. `auto` returns the real offset *and* the IANA zone name
(`Africa/Johannesburg`, `7200`). The trade: the `time` column then comes back as
local wall time, so ingest stores `ts = local − offset` and takes `local_date`
straight from the date part. Year boundaries become local years, which is what
we want — local years tile with no gap or overlap.

**The archive returns HTTP 200 with a plain-text error body.** Observed live:
`Unexpected error while streaming data: timeoutReached`, 53 bytes, status 200.
`raise_for_status()` does not catch it. Validate that the body starts with
`latitude,` and retry with a backoff — it is transient, and the retry succeeded
on the first attempt.

---

## The two traps (both verified, both silent)

**Azimuth is 0 = south, 180 = north.** Karoo (−32.25), midwinter midday, GHI 534:

| azimuth | GTI |
|---|---|
| 0 (south) | 89.6 W/m² |
| **180 (north)** | **841.9 W/m²** |

A **9.4× error**, and 89.6 doesn't look absurd. Southern-hemisphere sites face
north. **Assert this in a test.**

**Wind arrives in km/h.** `v_ms = wind_speed_100m / 3.6` before the power curve.
Classic silent 3.6× error. **Assert this too.**

---

## The models

**PV** — `global_tilted_irradiance` is the POA figure; do *not* sum
`direct + diffuse` (those are horizontal, ~10–25% wrong). Then derate for cell
temperature:

```
T_cell ≈ T_air + (NOCT - 20)/800 * POA
P      = P_rated * (POA/1000) * (1 + gamma * (T_cell - 25))    gamma ≈ -0.004/°C
```

**Wind** — density correction, then one published power curve for all sites:

```
rho = P_surface / (R_specific * T)     R_specific = 287.05 J/(kg·K)
P_wind scales with rho / 1.225
```

> **Every coefficient gets a citation in the README**, and the README splits
> what is verified and how from what is assumed and why. A model whose constants
> came from nowhere looks authoritative and is quietly wrong. This is the part
> that makes the project worth showing.
>
> Known assumption to state: `surface_pressure` is at the surface, not 100 m hub
> height — density there is ~1% lower.

---

## Schema

```sql
site(id, name, latitude, longitude, elevation_m, timezone,
     tilt_deg, azimuth_deg)          -- tilt/azimuth are FETCH params, frozen at ingest

weather_hour(site_id, ts, local_date, <the nine variables>)
  PRIMARY KEY (site_id, ts)          -- hypertable PK must include the time column
```

**Three decisions, already made — don't relitigate:**

- **`local_date` written at ingest.** UTC daily buckets split the solar day for
  any site off Greenwich, and `time_bucket`'s timezone must be constant inside a
  continuous aggregate. The daily cagg groups on `local_date`.
- **The cagg stores dimensionless capacity factor**, not megawatts. Keeps the
  expression dependent on `weather_hour` alone, and makes sites comparable on the
  map for free. `P_rated` multiplies in at read time.
- **The cagg is defined over the generation expression**, not raw weather —
  the temperature derate is nonlinear, so `f(avg(x)) ≠ avg(f(x))`. Model changes
  mean re-materialising; that's the accepted cost.

Percentiles are computed exactly over the daily cagg rows (n ≈ 3,650/site), so
no Timescale Toolkit — plain `timescale/timescaledb:2.x-pg17` is enough.

---

## Build order

1. **DONE — Compose up, one site, one year.** Hypertable, a `time_bucket` query. Proves
   the spine.
2. **DONE — Models + tests.** Pure functions, coefficients cited, **azimuth and km/h
   assertions**.
3. **DONE — Backfill.** CSV → `COPY`, resumable per `(site, year)`, re-runnable safely
   (**idempotency is what will actually break — test it**). Seed 6 sites.
4. **DONE — Daily cagg + compression.** Over the capacity-factor expression.
5. **Split in two at slice 5a's Understand.**
   - **5a — DONE. Map + generation chart, end to end.** FastAPI, two
     endpoints, a no-build-step frontend. See `specs/slice-5a.md`.
   - **5b — DONE. The reliability view.** `hourly_cf`, the metrics endpoint
     and the panel. See `specs/slice-5b.md`.
6. **DONE — Package it.** The seed corpus ships **in the repo** as CSV, not in
   a GitHub Release — 8.5 MB measured against the ~30 MB this plan assumed, so
   `git clone && docker compose up` needs no download
   ([`docs/adr/0011`](docs/adr/0011-the-seed-corpus-ships-in-the-repo.md),
   which supersedes the Release bullet under Hosting). README carries the
   measured numbers and the verified-vs-assumed split. No Caddy — see Hosting.
   See `specs/slice-6.md`. **Still owed: the GIF and stills.**

---

## Hosting — decided: nothing hosted

**Budget is zero, so `docker compose up` is the deliverable.** Clone, one
command, real data in under a minute. That proves the thing runs, which a URL
doesn't.

**Managed is out regardless:** Tiger Cloud has no free tier ($30/month, 30-day
trial). Cloud SQL, AlloyDB and Neon don't allow the `timescaledb` extension at
all. Cloud Run is the wrong shape for a database anyway — stateless, scales to
zero, can run several instances.

**Free tiers that host a stateful container are thin and they expire.** Fly.io's
free allowance is gone. Railway and Render exclude persistent disks from free.
A dead link on a portfolio in eight months is worse than never claiming one.

**Two containers, not three — Caddy is dropped.** Its one load-bearing job was
automatic TLS, which needs a public domain that "nothing hosted" rules out; on
localhost it would only proxy to a single service. FastAPI serves the frontend
itself. Superseded by
[`docs/adr/0006`](docs/adr/0006-no-reverse-proxy-until-there-is-a-domain.md),
which also records what keeps adding a proxy later free: same-origin relative
paths and env-driven config.

```
timescaledb   timescale/timescaledb:2.x-pg17   + named volume
api           FastAPI — the JSON and the frontend, one origin
```

There is still **no Node in production** — the frontend has no build step at
all, so there is no `dist/` to serve.

**What replaces the live URL, and must be as good:**
- ~~**A seed dump in a GitHub Release.**~~ **Superseded by
  [`docs/adr/0011`](docs/adr/0011-the-seed-corpus-ships-in-the-repo.md):** the
  corpus is two CSVs committed at `data/seed/`, restored by the API on first
  start. The ~30 MB estimate that kept it out of the repo was wrong — the
  measured gzip of `site` + `weather_hour` is 8.5 MB, and a Release asset adds
  a download and a token to the one command that is meant to be the whole
  story. CC BY 4.0 permits redistributing derived data with attribution and a
  modification notice, which is given in the README.
- **A recorded GIF plus stills in the README.** Reviewers who care will clone
  it; reviewers who don't will scroll. Both are served.

**Backups:** ~150 MB, all of it reconstructible by re-running the backfill from
Open-Meteo — about 1,570 API calls, well inside a day's quota. So a `pg_dump`
cron if you want one, and nothing if you don't. Worth a line in the README:
that's a real property of the architecture, not a corner cut.

**If a live URL is ever wanted,** Oracle Cloud Always Free (ARM, 24 GB RAM,
200 GB storage) is the one to try — genuinely free, subject to ARM capacity in
your region. Additive, never load-bearing.

---

## Still open

- [x] ~~**Reliability view design**~~ — resolved in slice 5b: metrics panel
      per site, with the worst window shaded on the chart and clickable to
      zoom to it. A 50/50 hybrid lull was added to the decided set once 5a
      made the seasonal complementarity visible.
- [x] ~~**Hourly metrics are too slow to serve live.**~~ — resolved in
      slice 5b, and the framing above was wrong. The 2m21s was ~41.6s of
      evaluating the model functions and under a second of windowing, so
      precomputing the physics into `hourly_cf` was the whole fix: per-site
      metrics now take ~126 ms and no summary table was needed. Measured
      before designing, see
      [`docs/adr/0009`](docs/adr/0009-measure-before-designing-for-performance.md).

## Settled since this plan was written

- **What does "reliable" mean?** — **longest lull, hourly-led**: worst
  rolling 24h, worst rolling week, hours/year below 10% output, with P50/P90
  annual alongside. Measured in `db/reliability.sql`; results in the README.
  PLAN's own objection was right — the solar P50/P90 spread is ~2%, barely
  above sampling noise at n=10.
- **Which 6 sites?** — six South African sites, all `Africa/Johannesburg`.
  One shared timezone was deliberate: it's what makes the cagg's constant
  timezone literal legal (`docs/adr/0001`). See `ingest/sites.py`.
- **Which published turbine curve?** — IEA 3.4 MW / 130 RWT, hub 110 m
  (closest match to our `wind_speed_100m`), NREL/TP-5000-73492, BSD-3-Clause.
  Vendored at `data/IEA_Reference_3.4MW_130.csv`.
- **Name the project.** — wattif.

## Superseded

- **PLAN's three-container topology, with Caddy.** Superseded by
  `docs/adr/0006` — no domain, no TLS, no job.
- **PLAN's schema section says "the daily cagg groups on `local_date`".** It
  cannot: a continuous aggregate must bucket on the hypertable's time column.
  Superseded by `docs/adr/0001`; resolved in slice 4 by a constant timezone
  literal. The section's other two decisions (cagg stores capacity factor,
  cagg is over the generation expression) still stand.
- **"`CREATE OR REPLACE` means the DB can never disagree with the
  constants."** False — it's signature-scoped. Superseded by `docs/adr/0004`.
