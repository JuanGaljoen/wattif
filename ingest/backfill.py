"""Resumable multi-site backfill.

Library functions here never commit -- the caller (the CLI's connection, or
a test's rollback fixture) owns the transaction. This is what makes the
tests provable without the network and without touching the dev database
(specs/slice-3.md, Approach).
"""
from __future__ import annotations

from typing import Callable, Optional

from .load import load, upsert_site
from .openmeteo import SiteSpec, fetch_csv, parse, rows_for_copy

Fetcher = Callable[[SiteSpec, int], str]


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

    Never commits. Reuses load.upsert_site / load.load verbatim -- the
    COPY-to-stage + ON CONFLICT DO NOTHING idempotency proven in slice 1.
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
    cur.execute(
        "UPDATE ingest_job SET status='done', rows=%s, fetched_at=now() "
        "WHERE site_id=%s AND year=%s",
        (inserted, site_id, year),
    )
    return inserted


def run_backfill(
    cur,
    sites: list[SiteSpec],
    years: list[int],
    fetch: Fetcher = fetch_csv,
    commit: Optional[Callable[[], None]] = None,
) -> int:
    """Backfill every pending (site, year) and return how many jobs ran.

    If `commit` is given, it's called after each site-year lands -- so a
    crash costs at most one year (db/schema.sql). Tests omit it and rely on
    the caller's rollback for isolation instead.
    """
    jobs = plan_jobs(cur, sites, years)
    for site, year in jobs:
        backfill_one(cur, site, year, fetch=fetch)
        if commit is not None:
            commit()
    return len(jobs)
