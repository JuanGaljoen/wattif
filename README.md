# wattif

*Watt if you'd built it there?*

Pick a point on a map. See what a solar or wind farm there would have generated,
hour by hour, over ten years of real weather — and how reliable it would
actually have been.

**Status: slice 5b of 6.** Six South African sites, ten years each:
**526,032 hourly rows**, rolled up into a daily continuous aggregate
(**21,918 rows**) over the generation expression, with the hypertable
compressed **74 MB → 23 MB (3.2×)** — and now a map and a generation chart
over the top of it. See [PLAN.md](PLAN.md) for the build order and
[specs/slice-5b.md](specs/slice-5b.md) for this slice's plan.

```sh
docker compose up -d          # db + api
open http://localhost:8000
```

Two containers, no Caddy and no Node: FastAPI serves the JSON and the
frontend on one origin, and the frontend has no build step at all
([`docs/adr/0006`](docs/adr/0006-no-reverse-proxy-until-there-is-a-domain.md)).

**A fresh clone shows an empty map.** The structures install themselves at
startup, but nobody has the weather — run the backfill below, or wait for
slice 6's seed dump.

**Optional:** set `CARTO_KEY` (free, [carto.com/basemaps/apikey](https://carto.com/basemaps/apikey/))
for the Dark Matter basemap. Without it the map falls back to OpenStreetMap
tiles inverted in CSS — still dark, still correct.

## The six sites

All `Africa/Johannesburg` — one shared timezone, deliberately: it keeps
slice 4's continuous aggregate to a single constant timezone literal
(see [`docs/adr/0001`](docs/adr/0001-cagg-cannot-group-on-local-date.md)).

| Site | Lat | Lon | Profile |
|---|---|---|---|
| Karoo | -32.25 | 22.55 | solar |
| Upington | -28.45 | 21.26 | solar++ |
| Cape West Coast | -32.80 | 18.15 | wind |
| Gqeberha | -33.96 | 25.60 | wind |
| Theunissen | -28.50 | 26.80 | both |
| Polokwane | -23.90 | 29.45 | solar |

## Running it

```sh
docker compose up -d
.venv/bin/python -m ingest.load                 # all 6 sites, 2016-2025
.venv/bin/python -m ingest.load --year 2024      # one year, all sites
.venv/bin/python -m ingest.load --site Karoo     # one site, all years
docker compose exec -T db psql -U postgres -d resource -f /dev/stdin < db/verify.sql
docker compose exec -T db psql -U postgres -d resource -f /dev/stdin < db/reliability.sql
.venv/bin/python -m pytest   # tests, against the running DB
```

## Reliability — what it means here

"How reliable would it actually have been" is answered as **longest lull**,
not just annual averages. Ten years of hourly data supports that far better
than it supports annual percentiles: P50/P90 at n=10 years is a distribution
of ten numbers, and the solar spread turns out to be ~2% — barely above
sampling noise. The lull metrics use all 87,672 hours per site.

Measured over 2016–2025 ([`db/reliability.sql`](db/reliability.sql)):

| Metric | Solar | Wind |
|---|---|---|
| Worst rolling 24 h | 0.014 – 0.046 | **0.000** at four sites |
| Worst rolling 7 days | 0.087 – 0.136 | 0.007 – 0.080 |
| Hours/year below 10% output | 750 – 912 *(daylight only)* | 2,065 – 4,617 |

The story those numbers tell: **wind has far deeper sustained lulls than
solar.** Solar is reliably cyclical — it comes back every morning. Wind can
be becalmed for a week.

### What a 50/50 farm would do

Wind peaks in July and solar in December at these sites, so the two are
seasonally complementary. Splitting the rated capacity evenly between them
turns out to change the picture — worst rolling 7 days, 2016–2025:

| Site | Solar | Wind | 50/50 | vs wind alone |
|---|---|---|---|---|
| Karoo | 0.095 | 0.014 | **0.101** | 7.1× |
| Upington | 0.123 | 0.041 | **0.136** | 3.4× |
| Cape West Coast | 0.087 | 0.080 | **0.135** | 1.7× |
| Gqeberha | 0.094 | 0.045 | **0.126** | 2.8× |
| Theunissen | 0.136 | 0.007 | 0.106 | 14.3× |
| Polokwane | 0.119 | 0.011 | 0.102 | 9.0× |

**A 50/50 split beats wind alone at every site, but beats *both* at only
four.** At Theunissen and Polokwane the wind resource is weak enough
(0.007 and 0.011) that mixing it in mostly dilutes a strong solar site.
Complementarity pays where the two resources are comparable; where one
dominates, it costs.

The 50/50 ratio is a **stated choice, not an optimum** — see the assumed
column below.

The PV "hours below 10%" figure counts **daylight hours only**. Counting all
hours would include every night hour and measure darkness rather than
reliability.

Note on P90: in energy it means the yield *exceeded* in 90% of years — the
10th percentile of the distribution, `percentile_cont(0.1)`, not `0.9`.

## Operational note

The cagg's refresh policy keeps a moving recent window current. It does
**not** re-materialise history, so after changing a coefficient in
[`models/constants.py`](models/constants.py) the aggregate keeps serving
values computed by the old function until a deliberate full-range refresh:

```python
from timescale import refresh_daily_cf
refresh_daily_cf(conn)          # NULL, NULL = everything
```

## Data & attribution

Weather data from [Open-Meteo.com](https://open-meteo.com/), ERA5 hourly
archive, licensed **CC BY 4.0**. Generation figures in this project are
*modifications*: derived from that data via the PV and wind models described in
PLAN.md. Open-Meteo's free tier is non-commercial and offers no uptime
guarantee.

## The models — verified vs. assumed

PV and wind generation are each a single SQL function, generated from the
constants in [`models/constants.py`](models/constants.py). Full citations and
confidence ratings: [docs/research/2026-09-18-model-coefficients.md](docs/research/2026-09-18-model-coefficients.md).

| Constant | Value | Status | Source |
|---|---|---|---|
| `R_SPECIFIC` (dry air) | 287.058 J/(kg·K) | **verified** | ICAO/ISO 2533 Standard Atmosphere |
| `RHO_STANDARD` | 1.2250 kg/m³ | **verified** | ICAO/ISO 2533 Standard Atmosphere |
| Turbine rated power, curve | 3,370 kW, 50-point curve | **verified** | IEA 3.4 MW/130 RWT, NREL/TP-5000-73492 (BSD-3-Clause) |
| `NOCT` | 45.0 °C | **assumed** | typical crystalline-silicon value; real modules run 42–48 °C, per-datasheet |
| `GAMMA` (temp. coefficient) | −0.004 /°C | **assumed** | common datasheet convention (−0.3 to −0.5%/°C range); no single primary table |
| Hybrid mix | 50/50 by rated capacity | **assumed** | a stated product choice, not an optimised or sourced ratio; `(pv_cf + wind_cf) / 2` |
| Low-output threshold | 10% of rated | **assumed** | PLAN.md's own definition of "hours below 10% output" |

Known limitations, stated rather than fixed:

- The cell-temperature formula (`T_air + (NOCT−20)/800·POA`) is NREL's full SAM
  model with the wind-speed and efficiency terms dropped, so it's less accurate
  than that model's ±2–3 °C, especially in windy conditions.
- `surface_pressure` is used for air density at 100 m hub height; true density
  there is ~1.2% lower.
- Density correction scales *power* by ρ/ρ₀ (as PLAN.md specifies). IEC
  61400-12-1 instead normalises *wind speed* by (ρ/ρ₀)^(1/3) for pitch-regulated
  turbines — not yet reconciled with the standard; see specs/slice-2.md.
- The published turbine curve reports power below its own stated 4 m/s cut-in
  (from 3 m/s) and stops abruptly at 25 m/s cut-out. We use it verbatim rather
  than override NREL's own numbers.
