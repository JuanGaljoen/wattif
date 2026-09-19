"""The seed corpus: dump it, and restore it into an empty database.

This is what closes the gap PLAN.md names -- a clean clone gets the schema,
the model functions and the aggregates, but no weather, so it renders a
correct and completely empty map. Restoring takes about a minute and costs
no Open-Meteo calls and no quota; the alternative is ~1,570 API calls and
roughly an hour of backfill.

WHAT IS DUMPED: `site` and `weather_hour`, and nothing else.

daily_cf and hourly_cf are derived, and regenerating them takes ~45s. A
continuous aggregate can be dumped, but only via
timescaledb_pre_restore()/post_restore(), which couples the file to an
extension version. That is a real fragility bought to save a minute of
compute, so the raw hours are the seed and everything else is rebuilt.

CSV rather than pg_dump, for the same reason: a CSV restores with COPY
against any TimescaleDB version, and the format is legible to anyone who
opens it.

Data: Open-Meteo.com, ERA5 archive, CC BY 4.0. Redistributed here as a
derived corpus with attribution and a modification notice (README).
"""
from __future__ import annotations

import gzip
import shutil
import urllib.request
from pathlib import Path

import psycopg

from config import DSN

from .backfill import COPY_COLUMNS

SEED_DIR = Path(__file__).resolve().parent.parent / "data" / "seed"
WEATHER_GZ = SEED_DIR / "weather_hour.csv.gz"
SITES_CSV = SEED_DIR / "site.csv"

# site ids are preserved through a dump/restore because weather_hour.site_id
# references them, and because a URL like /api/sites/1/daily is a thing
# people bookmark. The sequence is bumped past them on restore.
SITE_COLUMNS = [
    "id", "name", "latitude", "longitude", "elevation_m",
    "timezone", "utc_offset_seconds", "tilt_deg", "azimuth_deg",
]


def dump(conn) -> tuple[int, int]:
    """Write the seed files. Returns (sites, weather rows)."""
    SEED_DIR.mkdir(parents=True, exist_ok=True)

    with conn.cursor() as cur:
        cols = ", ".join(SITE_COLUMNS)
        with SITES_CSV.open("wb") as fh:
            with cur.copy(
                f"COPY (SELECT {cols} FROM site ORDER BY id) "
                f"TO STDOUT WITH (FORMAT csv)"
            ) as copy:
                for chunk in copy:
                    fh.write(chunk)

        wcols = ", ".join(COPY_COLUMNS)
        # gzip at the default level: -9 buys ~2% here and costs 3x the time.
        with gzip.open(WEATHER_GZ, "wb") as fh:
            with cur.copy(
                f"COPY (SELECT {wcols} FROM weather_hour ORDER BY site_id, ts) "
                f"TO STDOUT WITH (FORMAT csv)"
            ) as copy:
                for chunk in copy:
                    fh.write(chunk)

        cur.execute("SELECT count(*) FROM site")
        (sites,) = cur.fetchone()
        cur.execute("SELECT count(*) FROM weather_hour")
        (rows,) = cur.fetchone()
    return sites, rows


def is_empty(cur) -> bool:
    """True when the database has the schema but no weather in it."""
    cur.execute("SELECT NOT EXISTS (SELECT 1 FROM weather_hour LIMIT 1)")
    return cur.fetchone()[0]


def fetch_seed(url: str, into: Path = WEATHER_GZ) -> Path:
    """Download a seed file, for the case where it is not in the repo.

    The committed file is the default path; this exists so the corpus can
    live in a GitHub Release instead without any other change.
    """
    into.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url) as response, into.open("wb") as fh:
        shutil.copyfileobj(response, fh)
    return into


def restore(conn, weather_gz: Path = WEATHER_GZ,
            sites_csv: Path = SITES_CSV) -> int:
    """COPY the seed into the current database. Idempotent.

    Idempotent the same way the backfill is (ON CONFLICT DO NOTHING), so
    running it against a populated database inserts 0 and changes nothing.
    Does NOT create the aggregates or refresh them -- that is the caller's
    step, because a refresh cannot run inside a transaction.
    """
    if not weather_gz.exists():
        raise FileNotFoundError(
            f"no seed at {weather_gz}. Either fetch one (ingest.seed "
            f"--from-url ...) or run the backfill (python -m ingest.load)."
        )

    with conn.cursor() as cur:
        cols = ", ".join(SITE_COLUMNS)
        cur.execute("DROP TABLE IF EXISTS site_stage")
        cur.execute("CREATE TEMP TABLE site_stage (LIKE site INCLUDING DEFAULTS)")
        with sites_csv.open("rb") as fh:
            with cur.copy(f"COPY site_stage ({cols}) FROM STDIN WITH (FORMAT csv)") as copy:
                copy.write(fh.read())
        cur.execute(
            f"INSERT INTO site ({cols}) SELECT {cols} FROM site_stage "
            f"ON CONFLICT (name) DO NOTHING"
        )
        # Explicit ids were inserted, so the sequence is still at 1 and the
        # next real insert would collide.
        cur.execute(
            "SELECT setval(pg_get_serial_sequence('site', 'id'), "
            "GREATEST((SELECT max(id) FROM site), 1))"
        )

        wcols = ", ".join(COPY_COLUMNS)
        cur.execute("DROP TABLE IF EXISTS weather_stage")
        cur.execute(
            "CREATE TEMP TABLE weather_stage (LIKE weather_hour INCLUDING DEFAULTS)"
        )
        with gzip.open(weather_gz, "rb") as fh:
            with cur.copy(
                f"COPY weather_stage ({wcols}) FROM STDIN WITH (FORMAT csv)"
            ) as copy:
                while chunk := fh.read(1 << 20):
                    copy.write(chunk)
        cur.execute(
            f"INSERT INTO weather_hour ({wcols}) SELECT {wcols} FROM weather_stage "
            f"ON CONFLICT (site_id, ts) DO NOTHING"
        )
        inserted = cur.rowcount
    return inserted


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="dump or restore the seed corpus")
    ap.add_argument("action", choices=("dump", "restore"))
    ap.add_argument("--from-url", help="download the weather seed first")
    args = ap.parse_args()

    with psycopg.connect(DSN) as conn:
        if args.action == "dump":
            sites, rows = dump(conn)
            size = WEATHER_GZ.stat().st_size / 1e6
            print(f"wrote {SEED_DIR}: {sites} sites, {rows:,} rows, {size:.1f} MB")
            return

        if args.from_url:
            print(f"downloading {args.from_url} ...", flush=True)
            fetch_seed(args.from_url)

        print("restoring the seed ...", flush=True)
        inserted = restore(conn)
        conn.commit()
        print(f"  inserted {inserted:,} rows")

        # Structures and materialisation, the same steps the API performs at
        # startup -- done here too so the CLI path leaves a usable database.
        from models import apply_models
        from timescale import apply_timescale, compress_all, refresh_cagg
        from timescale.cagg import CAGGS

        with conn.cursor() as cur:
            apply_models(cur)
            apply_timescale(cur)
        conn.commit()
        for cagg in CAGGS:
            print(f"  materialising {cagg.name} ...", flush=True)
            refresh_cagg(conn, cagg.name)
        chunks, before, after = compress_all(conn)
        print(f"  compressed {chunks} chunks, {before} -> {after}")
        print("done")


if __name__ == "__main__":
    main()
