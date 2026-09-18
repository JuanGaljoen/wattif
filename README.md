# wattif

*Watt if you'd built it there?*

Pick a point on a map. See what a solar or wind farm there would have generated,
hour by hour, over ten years of real weather — and how reliable it would
actually have been.

**Status: slice 1 of 6.** The spine works — TimescaleDB up, a site-year
ingested, hypertable chunked, `time_bucket` rolling up. See [PLAN.md](PLAN.md)
for the build order and the decisions behind it.

## Running it

```sh
docker compose up -d
python -m ingest.load --year 2024
docker compose exec -T db psql -U postgres -d resource -f /dev/stdin < db/verify.sql
```

## Data & attribution

Weather data from [Open-Meteo.com](https://open-meteo.com/), ERA5 hourly
archive, licensed **CC BY 4.0**. Generation figures in this project are
*modifications*: derived from that data via the PV and wind models described in
PLAN.md. Open-Meteo's free tier is non-commercial and offers no uptime
guarantee.

Every physical coefficient used here is cited, and the README will keep what is
verified separate from what is assumed.
