# SLICE-5B — The reliability view

Classification: **feature** (three checkpoints)
Status: Design frozen 2026-09-19 · not yet built
No Jira. This file is the source of truth; the branch's commits are the rest.

## Why

"How reliable would it actually have been" is the second half of the product's
question, and the first half shipped in 5a. The metrics were decided at slice
4's Understand — **longest lull, hourly-led** — and measured in
`db/reliability.sql`. They take ~2m21s and live in a script nobody will run.

5b puts them in the product, and makes the lull **visible** rather than merely
numeric.

## Success criteria (the bar Verify checks)

- [ ] `hourly_cf(site_id, hour, pv_cf, wind_cf, gti)` materialises exactly
      **526,032 rows**, with a version marker and a refresh policy, like
      `daily_cf`
- [ ] `GET /api/sites/{id}/reliability` responds warm in **under ~300 ms**
- [ ] Its values **match `db/reliability.sql`'s measured numbers** — that
      script is the independent oracle and stays the oracle
- [ ] Worst-24h and worst-7d each carry **the timestamp they occurred**, so
      the chart can shade them
- [ ] The panel shows all four metrics for **solar, wind, and the 50/50
      hybrid** where meaningful
- [ ] The chart draws **raw daily behind the smoothed mean**, and shades the
      worst week
- [ ] The shaded lull is **visibly a dip** — the criterion the smoothing
      currently defeats (CLAUDE.md, Known gaps)
- [ ] All 53 existing tests stay green, plus new ones

## Approach

### `hourly_cf` — a second cagg, and cagg.py generalised

Measured before choosing (runner, 2026-09-19, against the live 526,032 rows):

| | |
|---|---|
| Evaluating the physics for every hour | **41.6 s** |
| Worst rolling 24h, one site, once precomputed | **126 ms** |
| Worst rolling 24h, all six sites | 983 ms |
| Hours below 10%, all six | 21 ms |
| Storage, uncompressed | 42 MB |

So the ~2m21s was almost entirely the model functions — `wind_capacity_factor`
unnests a 50-point curve per call, 526k times — and **not** the windowing.
Precompute the physics and per-site metrics are servable live. That is exactly
the fix `docs/adr/0005` named.

```sql
CREATE MATERIALIZED VIEW hourly_cf
WITH (timescaledb.continuous) AS
SELECT site_id,
       time_bucket('1 hour', ts) AS hour,
       avg(pv_capacity_factor(global_tilted_irradiance, temperature_2m))  AS pv_cf,
       avg(wind_capacity_factor(wind_speed_100m, surface_pressure,
                                temperature_2m))                          AS wind_cf,
       avg(global_tilted_irradiance)                                      AS gti
FROM weather_hour
GROUP BY site_id, hour
WITH NO DATA;
```

**No timezone argument.** `daily_cf` needs one because a day boundary is
local; an hour boundary is not, and SAST has no DST. The timezone belongs at
*read* time, where ADR 0007 already requires it. Verified legal against
TimescaleDB 2.17.2: a 1-hour bucket over already-hourly data creates fine and
yields `(site_id, hour timestamptz, pv_cf, wind_cf, gti double precision)`.

**`gti` is stored deliberately.** The PV "hours below 10%" metric counts
**daylight hours only** — otherwise it measures darkness rather than
reliability. `pv_cf > 0` would be an exact proxy (the model returns 0 only when
POA is 0), but storing `gti` lets the API mirror `db/reliability.sql`'s
`gti > 0` filter *literally*, which is what makes "matches the oracle"
checkable rather than argued.

**`timescale/cagg.py` is generalised** to carry more than one aggregate,
following `models/ddl.py`'s pattern: declare each cagg once as
`(name, version, ddl_builder)`, and let `apply_timescale` loop. The
version-marker check, the `COMMENT ON VIEW` and the refuse-on-mismatch logic
stay in one place rather than being copied. `ensure_refresh_policy` already
takes a cagg name, so it needs no change.

`refresh_daily_cf` generalises to `refresh_cagg(conn, name, start, end)`, with
the existing name kept as a thin wrapper so slice 4's documented call in the
README keeps working.

### Which aggregate answers which metric

Deliberately the same split `db/reliability.sql` already uses, so the oracle
and the endpoint compute from the same sources:

| Metric | Source |
|---|---|
| Worst rolling 24h | `hourly_cf` |
| Hours/year below 10% | `hourly_cf` |
| Worst rolling 7 days | `daily_cf` |
| P50 / P90 annual | `daily_cf` |

### The hybrid

```
hybrid_cf = (pv_cf + wind_cf) / 2
```

Equal **rated capacity** of solar and wind. This is a **product choice, not a
sourced constant** — it goes in the README's *assumed* column beside `NOCT`
and `GAMMA`, and the panel labels it "50/50" rather than implying an optimum.

It exists because 5a made the case visible: wind peaks every July and solar
every December, so at Karoo solar's worst week is 0.087 and wind's is 0.007 —
neither is reliable alone. The hybrid figure is the claim that follows.

### The endpoint

```
GET /api/sites/{id}/reliability
->
{
  "site_id": 1,
  "worst_24h": { "pv":    {"cf": 0.0176, "start": "2019-06-17T03:00:00Z"},
                 "wind":  {...}, "hybrid": {...} },
  "worst_7d":  { "pv":    {"cf": 0.0921, "start": "2019-06-14"},
                 "wind":  {...}, "hybrid": {...} },
  "hours_below_10pct": { "pv_daylight": 801, "wind": 4475 },
  "annual":   { "pv": {"p50": 0.24, "p90": 0.22}, "wind": {...} }
}
```

Worst windows carry their start so the chart can shade them. Hours are **per
year**, averaged over the ten local years — `AT TIME ZONE` before `extract`,
with `SITE_TIMEZONE` imported, per ADR 0007.

### The chart

Two changes to `web/app.js`, both using data the page **already fetches and
currently discards**:

1. **Raw daily behind the mean** — four uPlot series: raw pv, raw wind at low
   opacity, then the two smoothed lines on top. The mean stays the readable
   signal; the raw layer restores the variance that makes a lull look like one.
2. **The worst week shaded** — a uPlot `draw` hook painting a band across the
   worst-7d window for the selected resource.

## Files

| File | Change |
|---|---|
| `timescale/cagg.py` | generalise to N caggs; add `hourly_cf` + its version marker |
| `timescale/__init__.py` | `apply_timescale` loops; `refresh_cagg`, with `refresh_daily_cf` kept as a wrapper |
| `api/reliability.py` | **new** — the four metric queries and their row→dict mapping |
| `api/main.py` | the `/reliability` route |
| `web/app.js` | raw-daily series, the shading hook, the panel |
| `web/index.html`, `web/style.css` | the reliability panel |
| `db/reliability.sql` | unchanged — it is the oracle |
| `README.md` | the hybrid in the *assumed* column; reliability section refreshed |

## Tests

The seam is HTTP for the endpoint and `apply_timescale(tx)` for the cagg —
matching `tests/test_timescale.py` and `tests/test_api.py`.

**The oracle is `db/reliability.sql`'s measured values**, hardcoded as
literals. Those numbers came from a different path — raw `weather_hour` with
the model functions applied inline, not from `hourly_cf` — so a test asserting
them is not recomputing the answer the way the code computes it.

- [ ] `hourly_cf` materialises **526,032 rows**
- [ ] A sampled site-hour in `hourly_cf` equals the same hour computed directly
      from `weather_hour`
- [ ] Version-marker mismatch refuses, like `daily_cf` (reuse the existing test
      shape)
- [ ] `apply_timescale` is still idempotent — called twice, no error
- [ ] **Karoo worst-24h pv = 0.0176 and wind = 0.0000** — the measured oracle
- [ ] **Gqeberha worst-24h wind = 0.0012** — a second site, so the first isn't
      a coincidence
- [ ] `worst_7d.hybrid.cf > worst_7d.wind.cf` at Karoo — the product's claim,
      asserted rather than assumed
- [ ] Worst-window timestamps fall inside 2016-01-01 … 2025-12-31
- [ ] `/reliability` for an unknown site returns 404
- [ ] Hours below 10% are **per year** (order ~10² – 10³, not 10⁴ — the raw
      ten-year totals are ~44,753, and publishing that as an annual figure is
      the obvious off-by-ten-years mistake)

## Risks

1. **Generalising `cagg.py` touches working slice-4 code.** `daily_cf` is live
   and correct; the refactor could break it silently. `tests/test_timescale.py`
   covers it — run it before and after, and don't change `daily_cf`'s SELECT
   (which would force a version bump and a full re-materialisation).
2. **Materialising `hourly_cf` takes ~42 s** and cannot run in a transaction.
   It is an explicit step, like `refresh_daily_cf` — not something a test or a
   startup does implicitly. Startup creates the *structure*; filling it is
   deliberate.
3. **Compressing a cagg is new ground here.** `daily_cf` is not compressed
   (`compression_enabled = f`). 42 MB is affordable uncompressed — if cagg
   compression proves fiddly, drop it and say so rather than fighting it.
4. **Four series plus a shaded band may be visually busy.** The raw layer has
   to stay clearly subordinate to the mean, or 5b undoes CP3's readability.

## For Chronicle

- **The measurement that chose the design** — that the cost was the model
  functions, not the windowing, and the ~18× gap between assuming and
  measuring. Likely an ADR.
- **Whether the hybrid claim held.** If the 50/50 lull really is far shallower
  than either resource alone, that is a finding about the sites, not about the
  code — README material.
