"""CLI: backfill every pending (site, year), or a subset via flags.

    python -m ingest.load                  # all 6 sites, 2016-2025
    python -m ingest.load --year 2024      # one year, all sites
    python -m ingest.load --site Karoo     # one site, all years

A thin wrapper over ingest.backfill -- plan_jobs/backfill_one carry the
actual logic and are tested without the network (tests/test_backfill.py).
Commits per site-year, not per run: a crash costs at most one year
(db/schema.sql, ingest_job).
"""
from __future__ import annotations

import argparse
import os

import psycopg

# Re-exported for backward compatibility: upsert_site, load and COPY_COLUMNS
# used to live here (slice 1); they moved to backfill.py to break a circular
# import once the CLI itself needed to depend on backfill (slice 3, CP3).
from .backfill import (  # noqa: F401 -- some re-exported, see module docstring
    COPY_COLUMNS,
    backfill_one,
    load,
    plan_jobs,
    run_backfill,
    upsert_site,
)
from .sites import SITES, YEARS

DSN = os.environ.get(
    "DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/resource"
)

# Backward-compat alias -- tests/test_pv.py pins the azimuth trap against
# this name (slice 2, before the multi-site registry existed).
KAROO = SITES[0]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, help="restrict to one year")
    ap.add_argument("--site", help="restrict to one site by name")
    args = ap.parse_args()

    sites = [s for s in SITES if s.name == args.site] if args.site else SITES
    if args.site and not sites:
        raise SystemExit(f"no such site: {args.site!r}")
    years = [args.year] if args.year else YEARS

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        pending = len(plan_jobs(cur, sites, years))
        print(f"{pending} site-year(s) pending", flush=True)
        if pending == 0:
            print("nothing pending")
            return

        def on_done(site, year, inserted):
            print(f"  {site.name} {year} ... inserted {inserted} rows")
            conn.commit()

        run_backfill(cur, sites, years, on_done=on_done)


if __name__ == "__main__":
    main()
