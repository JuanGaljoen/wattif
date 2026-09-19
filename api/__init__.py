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

from models import apply_models
from timescale import apply_timescale

from .db import close_pool, ddl_cursor, open_pool
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
        yield
    finally:
        close_pool()


def create_app() -> FastAPI:
    app = FastAPI(title="wattif", lifespan=lifespan)

    app.include_router(router)  # FIRST -- see module docstring

    # html=True serves index.html at "/". The directory is created in CP2;
    # guard so the API is runnable and testable without it.
    if WEB_DIR.is_dir():
        app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")

    return app


app = create_app()
