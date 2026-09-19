"""One connection pool, opened and closed on the app's lifespan.

Routes are sync (`def`, not `async def`), so Starlette runs them in a
threadpool and a sync pool is the right shape -- no async driver, no event
loop to reason about (specs/slice-5a.md, Approach).
"""
from __future__ import annotations

from contextlib import contextmanager

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from config import DSN

_pool: ConnectionPool | None = None


def open_pool() -> None:
    global _pool
    if _pool is None:
        # Six sites and a handful of readers: a small pool is plenty, and
        # `open=True` fails fast at startup rather than on first request.
        _pool = ConnectionPool(DSN, min_size=1, max_size=4, open=True)


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


@contextmanager
def ddl_cursor():
    """A DEFAULT (tuple-row) cursor, for the runtime DDL at startup.

    models.apply_models and timescale.apply_timescale index rows
    positionally (`row[0]`), so they must not be handed the dict_row cursor
    the read endpoints use -- that raises KeyError: 0.
    """
    if _pool is None:
        raise RuntimeError("connection pool is not open")
    with _pool.connection() as conn, conn.cursor() as cur:
        yield cur


@contextmanager
def cursor():
    """A dict cursor from the pool. Read-only callers; nothing here commits."""
    if _pool is None:
        raise RuntimeError("connection pool is not open")
    with _pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        yield cur
