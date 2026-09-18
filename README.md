# wattif

*Watt if you'd built it there?*

Pick a point on a map. See what a solar or wind farm there would have generated,
hour by hour, over ten years of real weather — and how reliable it would
actually have been.

**Status: slice 3 of 6.** Six South African sites, ten years each, fully
backfilled: **526,032 hourly rows**. Resumable and idempotent — the real run
crashed twice on transient upstream timeouts and picked up cleanly both
times, no duplicates, no manual cleanup. See [PLAN.md](PLAN.md) for the build
order and [specs/slice-3.md](specs/slice-3.md) for this slice's plan.

## The six sites

All `Africa/Johannesburg` — one shared timezone, deliberately: it keeps
slice 4's continuous aggregate to a single constant timezone literal
(see [`docs/adr/0001`](docs/adr/0001-cagg-cannot-group-on-local-date.md)).

| Site | Lat | Lon | Profile |
|---|---|---|---|
| Karoo | -32.25 | 22.55 | solar |
| Upington | -28.45 | 21.26 | solar++ |
| Cape West Coast | -32.80 | 18.15 | wind |
| Port Elizabeth | -33.96 | 25.60 | wind |
| Free State | -28.50 | 26.80 | both |
| Limpopo | -23.90 | 29.45 | solar |

## Running it

```sh
docker compose up -d
.venv/bin/python -m ingest.load                 # all 6 sites, 2016-2025
.venv/bin/python -m ingest.load --year 2024      # one year, all sites
.venv/bin/python -m ingest.load --site Karoo     # one site, all years
docker compose exec -T db psql -U postgres -d resource -f /dev/stdin < db/verify.sql
.venv/bin/python -m pytest   # tests, against the running DB
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
