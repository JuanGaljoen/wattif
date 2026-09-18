"""The six seed sites -- all Africa/Johannesburg (UTC+2, no DST), all
azimuth 180 (NORTH-facing -- southern hemisphere, the 9.4x trap, see
CLAUDE.md). One shared timezone is a deliberate choice (specs/slice-3.md):
it collapses ADR 0001's cagg-timezone blocker into a single constant.

Tilt ~= |latitude| is a rule of thumb, not a cited optimum -- an assumption,
same status as the PV/wind coefficients in models/constants.py.

Karoo's tilt is frozen at 32.0: it already has 2024 data ingested at that
value, and tilt/azimuth are fetch parameters frozen at ingest
(db/schema.sql) -- changing it means re-fetching the site.
"""
from __future__ import annotations

from .openmeteo import SiteSpec

SITES = [
    SiteSpec(name="Karoo", latitude=-32.25, longitude=22.55,
              tilt_deg=32.0, azimuth_deg=180.0),
    SiteSpec(name="Upington", latitude=-28.45, longitude=21.26,
              tilt_deg=28.0, azimuth_deg=180.0),
    SiteSpec(name="Cape West Coast", latitude=-32.80, longitude=18.15,
              tilt_deg=33.0, azimuth_deg=180.0),
    SiteSpec(name="Port Elizabeth", latitude=-33.96, longitude=25.60,
              tilt_deg=34.0, azimuth_deg=180.0),
    SiteSpec(name="Free State", latitude=-28.50, longitude=26.80,
              tilt_deg=29.0, azimuth_deg=180.0),
    SiteSpec(name="Limpopo", latitude=-23.90, longitude=29.45,
              tilt_deg=24.0, azimuth_deg=180.0),
]

# Ten most recent complete years (today: 2026-09-18).
YEARS = list(range(2016, 2026))
