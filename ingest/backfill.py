"""Resumable multi-site backfill.

Library functions here never commit -- the caller (the CLI's connection, or
a test's rollback fixture) owns the transaction. This is what makes the
tests provable without the network and without touching the dev database
(specs/slice-3.md, Approach).
"""
from __future__ import annotations

from datetime import date
from typing import Callable, Optional

from .openmeteo import VARIABLES, SiteSpec, fetch_csv, parse, rows_for_copy

Fetcher = Callable[[SiteSpec, int], str]

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
    # DROP IF EXISTS, not ON COMMIT DROP: a resumable backfill may call this
    # more than once per commit (run_backfill batches commits per site-year,
    # and tests run entirely inside one rolled-back transaction), so the
    # table can't rely on a commit happening between calls.
    cur.execute("DROP TABLE IF EXISTS stage")
    cur.execute("CREATE TEMP TABLE stage (LIKE weather_hour INCLUDING DEFAULTS)")
    cols = ", ".join(COPY_COLUMNS)
    with cur.copy(f"COPY stage ({cols}) FROM STDIN") as copy:
        for row in rows:
            copy.write_row(row)
    cur.execute(
        f"INSERT INTO weather_hour ({cols}) SELECT {cols} FROM stage "
        f"ON CONFLICT (site_id, ts) DO NOTHING"
    )
    return cur.rowcount


def plan_jobs(
    cur, sites: list[SiteSpec], years: list[int]
) -> list[tuple[SiteSpec, int]]:
    """Every (site, year) not yet done.

    Joins on site NAME, not site_id: a site that has never been fetched has
    no `site` row at all yet, so it trivially contributes no done-pairs and
    every one of its years is planned. A job stuck at 'running' or 'error'
    is not 'done' either, so it re-plans and re-runs -- resumability falls
    out of this one query.
    """
    cur.execute(
        "SELECT s.name, j.year FROM ingest_job j "
        "JOIN site s ON s.id = j.site_id WHERE j.status = 'done'"
    )
    done = set(cur.fetchall())
    return [
        (site, year)
        for site in sites
        for year in years
        if (site.name, year) not in done
    ]


def backfill_one(cur, site: SiteSpec, year: int, fetch: Fetcher = fetch_csv) -> int:
    """Fetch and ingest one site-year. Returns rows inserted this run.

    Never commits. Uses upsert_site / load above -- the COPY-to-stage +
    ON CONFLICT DO NOTHING idempotency proven in slice 1.
    """
    body = fetch(site, year)
    meta, data = parse(body)
    site_id = upsert_site(cur, site, meta)
    cur.execute(
        "INSERT INTO ingest_job (site_id, year, status) VALUES (%s, %s, 'running') "
        "ON CONFLICT (site_id, year) DO UPDATE SET status = 'running', error = NULL",
        (site_id, year),
    )
    inserted = load(cur, rows_for_copy(site_id, meta, data))
    # ingest_job.rows records rows HELD for this site-year, not rows inserted
    # this run -- a safe re-run (0 inserted) must not overwrite a real count
    # with 0 (CLAUDE.md, "Known gaps"; specs/slice-3.md CP2).
    cur.execute(
        "SELECT count(*) FROM weather_hour "
        "WHERE site_id = %s AND local_date >= %s AND local_date < %s",
        (site_id, date(year, 1, 1), date(year + 1, 1, 1)),
    )
    (held,) = cur.fetchone()
    cur.execute(
        "UPDATE ingest_job SET status='done', rows=%s, fetched_at=now() "
        "WHERE site_id=%s AND year=%s",
        (held, site_id, year),
    )
    return inserted


OnDone = Callable[[SiteSpec, int, int], None]


def run_backfill(
    cur,
    sites: list[SiteSpec],
    years: list[int],
    fetch: Fetcher = fetch_csv,
    on_done: Optional[OnDone] = None,
) -> int:
    """Backfill every pending (site, year) and return how many jobs ran.

    If `on_done(site, year, inserted)` is given, it's called after each
    site-year lands -- the CLI uses it to commit (so a crash costs at most
    one year, db/schema.sql) and report progress. Tests omit it and rely on
    the caller's rollback for isolation instead.
    """
    jobs = plan_jobs(cur, sites, years)
    for site, year in jobs:
        inserted = backfill_one(cur, site, year, fetch=fetch)
        if on_done is not None:
            on_done(site, year, inserted)
    return len(jobs)
