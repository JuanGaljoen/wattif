# wattif

*Watt if you'd built it there?*

Pick a point on a map. See what a solar or wind farm there would have generated,
hour by hour, over ten years of real weather — and how reliable it would
actually have been.

**Status: slice 6 of 6 — complete.** Six South African sites, ten years
each: **526,032 hourly rows**, rolled up into a daily continuous aggregate
(**21,918 rows**) and an hourly one over the generation expression, with the
hypertable compressed **74 MB → 23 MB (3.2×)** — under a map, a generation
chart and a reliability panel. See [PLAN.md](PLAN.md) for the build order
and [specs/slice-6.md](specs/slice-6.md) for this slice's plan.

```sh
git clone … && cd wattif
docker compose up            # first run takes ~90 s -- watch it, see below
open http://localhost:8000
```

**That is the whole setup.** Ten years of weather for all six sites ships in
the repo (`data/seed/`, 8.5 MB gzipped) and restores itself on first start —
no API keys, no backfill, no Open-Meteo quota. See
[the seed corpus](#the-seed-corpus) below.

**Run the first start in the foreground**, without `-d`. Restoring the corpus
happens in the API's startup hook, and uvicorn binds its port only after that
finishes. Docker publishes 8000 straight away regardless, so for ~90 s the
connection is accepted and then answered with nothing — a browser shows a
connection-reset error, which looks exactly like a broken build. `-d` sends
the only evidence to the contrary to a log nobody is watching:

```
[seed] empty database: restoring the bundled corpus
[seed]   526,032 rows
[seed]   materialising daily_cf
[seed]   materialising hourly_cf
[seed]   compressed 122 chunks, 74 MB -> 23 MB
[seed] ready
```

`[seed] ready` is the cue to open the page. Every start after that is
immediate, and `-d` is the right flag from then on.

Two containers, no Caddy and no Node: FastAPI serves the JSON and the
frontend on one origin, and the frontend has no build step at all
([`docs/adr/0006`](docs/adr/0006-no-reverse-proxy-until-there-is-a-domain.md)).

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

## The seed corpus

`data/seed/` holds the ten years, as two CSV files: `site.csv` (6 rows) and
`weather_hour.csv.gz` (526,032 rows, 8.5 MB gzipped). On first start the API
notices `weather_hour` is empty, restores them, and materialises both
continuous aggregates. It is guarded on that emptiness, so it happens exactly
once per volume and never touches a database you have filled yourself.

On the same pass it compresses every chunk (**74 MB → 23 MB**, ~1.3 s), so a
fresh clone matches the figure quoted at the top of this file rather than
waiting up to 12 hours for the compression policy to catch up.

The aggregates are **not** in the corpus — they are derived, and rebuild in
about 45 s. Dumping a continuous aggregate means
`timescaledb_pre_restore()` / `post_restore()`, which pins the file to a
TimescaleDB version; CSV restores with `COPY` against any of them.
See [`docs/adr/0011`](docs/adr/0011-the-seed-corpus-ships-in-the-repo.md) for
why this is in the repo rather than a GitHub Release.

```sh
python -m ingest.seed restore            # into an existing empty database
python -m ingest.seed dump               # re-dump after a schema change
```

Re-dump after **any change to `db/schema.sql`'s column order** — the CSVs are
positional. A test pins both column lists against `information_schema` so
that failure is loud rather than a silent misalignment.

## Running it

```sh
docker compose up -d        # everything: schema, functions, aggregates, data
docker compose exec -T db psql -U postgres -d resource -f /dev/stdin < db/verify.sql
docker compose exec -T db psql -U postgres -d resource -f /dev/stdin < db/reliability.sql
.venv/bin/python -m pytest  # tests, against the running DB
```

**Refetching from Open-Meteo** is optional — the corpus already holds
everything these commands would produce. It exists for adding a site, or
extending the years:

```sh
.venv/bin/python -m ingest.load                  # all 6 sites, 2016-2025
.venv/bin/python -m ingest.load --year 2024      # one year, all sites
.venv/bin/python -m ingest.load --site Karoo     # one site, all years
```

Sites are named after the nearest town, and `site.name` is the `ON CONFLICT`
key — renaming one means an `UPDATE` against the database as well as an edit
to `ingest/sites.py`, or the next backfill inserts a new empty site and
orphans the weather rows.

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

`data/seed/` **redistributes** that data as a derived corpus, which CC BY 4.0
permits with attribution and a modification notice — both given here. The
vendored turbine curve (`data/IEA_Reference_3.4MW_130.csv`) is NREL's IEA
3.4 MW/130 RWT, BSD-3-Clause, licence at `data/LICENSE-NREL`.

**Backups:** all 150 MB of the database is reconstructible — from
`data/seed/` in about a minute, or from Open-Meteo in ~1,570 API calls, well
inside a day's quota. That is a property of the architecture, not a corner
cut: there is nothing here that only exists in one place.

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
