"""TimescaleDB structures applied at runtime.

These can't live in db/schema.sql: that runs only via
docker-entrypoint-initdb.d, i.e. once, on an empty volume -- and the database
holds 526,032 rows we are not dropping to add an aggregate.

Public interface:
    apply_timescale(cur)       -- create what's missing. Transactional.
    refresh_daily_cf(conn)     -- materialise. NOT transactional; see below.
"""
from __future__ import annotations

from .cagg import CAGG_NAME, ensure_cagg

__all__ = ["apply_timescale", "refresh_daily_cf"]


def apply_timescale(cur) -> None:
    """Install every runtime TimescaleDB structure. Idempotent.

    Never commits -- the caller owns the transaction, matching
    ingest/backfill.py (specs/slice-3.md) so tests can roll back.
    """
    ensure_cagg(cur)


def refresh_daily_cf(conn, start=None, end=None) -> None:
    """Materialise the aggregate over a range (None, None = everything).

    Separate from apply_timescale because refresh_continuous_aggregate
    cannot run inside a transaction block -- it needs its own autocommit
    connection.

    This is also the operation a model coefficient change requires: the
    refresh POLICY only covers a moving recent window, so after changing a
    constant in models/constants.py, refresh the full range explicitly or
    the cagg keeps serving values computed by the old function
    (specs/slice-4.md).
    """
    previous = conn.autocommit
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            # Explicit casts: a NULL bound parameter has no inferable type,
            # and NULL/NULL is how you ask for the whole range.
            cur.execute(
                f"CALL refresh_continuous_aggregate('{CAGG_NAME}', "
                f"%s::timestamptz, %s::timestamptz)",
                (start, end),
            )
    finally:
        conn.autocommit = previous
