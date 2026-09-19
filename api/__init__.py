"""The app factory.

ROUTE ORDER IS LOAD-BEARING. Starlette matches routes in registration order,
first match wins, so a StaticFiles mount at "/" swallows everything
registered after it. The API router is included FIRST; the static mount goes
last and catches whatever is left (specs/slice-5a.md, Containers). A test
pins this, because the failure is a 404 on an endpoint that plainly exists.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from ingest.seed import WEATHER_GZ, is_empty, restore
from models import apply_models
from timescale import apply_timescale, compress_all, refresh_cagg
from timescale.cagg import CAGGS

from .db import close_pool, ddl_cursor, open_pool, pool_connection
from .main import router

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Open the pool, then install the runtime DDL the API reads.

    db/schema.sql runs once, on an empty volume, and creates tables only.
    The model functions and daily_cf are applied at runtime by design
    (timescale/__init__.py) -- and nothing outside the test suite used to
    call them, so a clean clone had no daily_cf and this API would 500.

    Both are idempotent, so this is a no-op on an established database. It
    installs structures, never data: six markers still need a seeded
    database (the backfill, or PLAN.md's slice-6 seed dump).

    Deliberately NOT defensive. ensure_cagg raises on a version-marker
    mismatch (timescale/cagg.py) and that exception is allowed to abort
    startup -- an API silently serving an aggregate built from a different
    SELECT is the failure this is meant to prevent.
    """
    open_pool()
    try:
        with ddl_cursor() as cur:
            apply_models(cur)
            apply_timescale(cur)
        _seed_if_empty()
        yield
    finally:
        close_pool()


def _seed_if_empty() -> None:
    """Restore the bundled corpus into a database that has none.

    This is what makes `docker compose up` the whole story. Structures
    install themselves (docs/adr/0008) but no code can conjure ten years of
    weather, so a clean clone rendered a correct and entirely empty map
    until this ran.

    Guarded on weather_hour being empty, so it fires exactly once per
    volume and never touches a database someone has backfilled. Synchronous
    and logged: it takes about a minute, and a server that looks hung is
    worse than one that says what it is doing.
    """
    with ddl_cursor() as cur:
        if not is_empty(cur):
            return
        if not WEATHER_GZ.exists():
            print(f"[seed] empty database and no seed at {WEATHER_GZ} -- "
                  f"run `python -m ingest.load` to backfill from Open-Meteo",
                  flush=True)
            return

    print("[seed] empty database: restoring the bundled corpus", flush=True)
    with pool_connection() as conn:
        inserted = restore(conn)
        conn.commit()
        print(f"[seed]   {inserted:,} rows", flush=True)
        for cagg in CAGGS:
            print(f"[seed]   materialising {cagg.name}", flush=True)
            refresh_cagg(conn, cagg.name)

        # The policy would get here on its own within 12 hours. That is 12
        # hours of a clone sitting at 74 MB while the README advertises 23,
        # and of tests/test_compression.py having no compressed chunk to
        # assert against. It costs ~1.3 s, so it is not worth deferring.
        chunks, before, after = compress_all(conn)
        print(f"[seed]   compressed {chunks} chunks, {before} -> {after}",
              flush=True)
    print("[seed] ready", flush=True)


def create_app() -> FastAPI:
    app = FastAPI(title="wattif", lifespan=lifespan)

    app.include_router(router)  # FIRST -- see module docstring

    # html=True serves index.html at "/". The directory is created in CP2;
    # guard so the API is runnable and testable without it.
    if WEB_DIR.is_dir():
        app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")

    return app


app = create_app()
