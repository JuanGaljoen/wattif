# wattif

*Watt if you'd built it there?*

Pick a point on a map. See what a solar or wind farm there would have generated,
hour by hour, over ten years of real weather — and how reliable it would
actually have been.

**Status: slice 4 of 6.** Six South African sites, ten years each:
**526,032 hourly rows**, rolled up into a daily continuous aggregate
(**21,918 rows**) over the generation expression, with the hypertable
compressed **74 MB → 23 MB (3.2×)**. See [PLAN.md](PLAN.md) for the build
order and [specs/slice-4.md](specs/slice-4.md) for this slice's plan.

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
