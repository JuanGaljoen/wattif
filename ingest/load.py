"""Slice 1: ingest one site-year into the hypertable, then prove it with a
time_bucket query.

    python -m ingest.load --year 2024
"""
from __future__ import annotations

import argparse
import os

import psycopg

from .openmeteo import VARIABLES, SiteSpec, fetch_csv, parse, rows_for_copy

DSN = os.environ.get(
    "DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/resource"
)

# Slice 1 seeds one site. Karoo: high irradiance, southern hemisphere, so it
# exercises the azimuth convention rather than hiding it.
KAROO = SiteSpec(
    name="Karoo", latitude=-32.25, longitude=22.55, tilt_deg=32.0, azimuth_deg=180.0
)

COPY_COLUMNS = ["site_id", "ts", "local_date", *VARIABLES]


def upsert_site(cur, site: SiteSpec, meta) -> int:
    cur.execute(
        """
        INSERT INTO site (name, latitude, longitude, elevation_m, timezone,
                          utc_offset_seconds, tilt_deg, azimuth_deg)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (name) DO UPDATE SET
            elevation_m        = EXCLUDED.elevation_m,
            utc_offset_seconds = EXCLUDED.utc_offset_seconds
        RETURNING id
        """,
        (site.name, site.latitude, site.longitude, meta.elevation_m,
         meta.timezone, meta.utc_offset_seconds, site.tilt_deg, site.azimuth_deg),
    )
    return cur.fetchone()[0]


def load(cur, rows: list[tuple]) -> int:
    """COPY into a temp table, then move across ignoring conflicts.

    COPY cannot do ON CONFLICT, and re-running a (site, year) must be safe --
    that is the whole idempotency story for the backfill.
    """
    cur.execute(
        "CREATE TEMP TABLE stage (LIKE weather_hour INCLUDING DEFAULTS) ON COMMIT DROP"
    )
    cols = ", ".join(COPY_COLUMNS)
    with cur.copy(f"COPY stage ({cols}) FROM STDIN") as copy:
        for row in rows:
            copy.write_row(row)
    cur.execute(
        f"INSERT INTO weather_hour ({cols}) SELECT {cols} FROM stage "
        f"ON CONFLICT (site_id, ts) DO NOTHING"
    )
    return cur.rowcount


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, default=2024)
    args = ap.parse_args()

    print(f"fetching {KAROO.name} {args.year} ...", flush=True)
    meta, data = parse(fetch_csv(KAROO, args.year))
    print(f"  {len(data)} hourly rows, elevation {meta.elevation_m} m, "
          f"utc_offset {meta.utc_offset_seconds}s")

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        site_id = upsert_site(cur, KAROO, meta)
        cur.execute(
            "INSERT INTO ingest_job (site_id, year, status) VALUES (%s, %s, 'running') "
            "ON CONFLICT (site_id, year) DO UPDATE SET status = 'running', error = NULL",
            (site_id, args.year),
        )
        inserted = load(cur, rows_for_copy(site_id, meta, data))
        cur.execute(
            "UPDATE ingest_job SET status='done', rows=%s, fetched_at=now() "
            "WHERE site_id=%s AND year=%s",
            (inserted, site_id, args.year),
        )
        conn.commit()
        print(f"  inserted {inserted} rows (re-runs insert 0 -- that is correct)")


if __name__ == "__main__":
    main()
