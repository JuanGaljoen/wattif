"""The read endpoints. Two of them; 5b adds its own.

Sync handlers by design -- see api/db.py.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from config import CARTO_KEY
from timescale import SITE_TIMEZONE

from .db import cursor

router = APIRouter(prefix="/api")


@router.get("/config")
def client_config() -> dict:
    """What the page needs from the environment, and cannot read itself.

    CARTO_KEY is not a secret: it travels in every tile URL and is visible
    in devtools. Serving it here keeps it out of a committed file and lets
    the page pick its basemap -- Dark Matter when a key is set, OSM
    inverted in CSS when it is not (specs/slice-5a.md, Basemap).
    """
    return {"carto_key": CARTO_KEY}


@router.get("/sites")
def list_sites() -> list[dict]:
    """The six seeded sites, for the map's markers.

    tilt/azimuth ride along because they are fetch parameters frozen on the
    row (db/schema.sql) -- the page shows them as the site's configuration,
    not as something it can change.
    """
    with cursor() as cur:
        cur.execute(
            """
            SELECT id, name, latitude, longitude, elevation_m,
                   tilt_deg, azimuth_deg
            FROM site
            ORDER BY id
            """
        )
        return cur.fetchall()


@router.get("/sites/{site_id}/daily")
def daily_series(site_id: int) -> dict:
    """Ten years of daily capacity factor, columnar.

    THE TIMEZONE CAST IS LOAD-BEARING. daily_cf.day is a timestamptz whose
    bucket starts at LOCAL midnight, so serialising it raw gives
    '2025-12-30T22:00:00Z' and every point lands on the previous day --
    silent, plausible, off by one. `AT TIME ZONE` recovers the calendar day
    the bucket actually represents.

    SITE_TIMEZONE is imported, never retyped: it is the same constant the
    continuous aggregate is defined with (docs/adr/0001), and a second copy
    is how the single-timezone constraint gets broken.

    Rounded to 4 dp -- weather_hour is `real` (float4) and the models'
    precision is far below what more digits would imply. It also halves the
    payload.
    """
    with cursor() as cur:
        cur.execute("SELECT 1 FROM site WHERE id = %s", (site_id,))
        if cur.fetchone() is None:
            raise HTTPException(status_code=404, detail=f"no such site: {site_id}")

        cur.execute(
            """
            SELECT (day AT TIME ZONE %(tz)s)::date       AS day,
                   round(pv_cf::numeric, 4)::float8      AS pv_cf,
                   round(wind_cf::numeric, 4)::float8    AS wind_cf
            FROM daily_cf
            WHERE site_id = %(id)s
            ORDER BY day
            """,
            {"tz": SITE_TIMEZONE, "id": site_id},
        )
        rows = cur.fetchall()

    return {
        "site_id": site_id,
        "timezone": SITE_TIMEZONE,
        "days": [r["day"].isoformat() for r in rows],
        "pv_cf": [r["pv_cf"] for r in rows],
        "wind_cf": [r["wind_cf"] for r in rows],
    }
