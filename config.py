"""Environment-driven configuration, in one place.

DSN was duplicated in ingest/load.py and tests/conftest.py, and the API was
about to add a third copy. Everything here is read from the environment with
a working local default, so nothing needs configuring to run the stack and
nothing needs editing to deploy it.
"""
from __future__ import annotations

import os

DSN = os.environ.get(
    "DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/resource"
)

# CARTO Basemaps. NOT a secret: it travels in the tile URL and is readable in
# devtools. It lives in the environment so it is not hardcoded, and so that a
# clone without one degrades to OSM tiles inverted in CSS rather than to a
# map watermarked "API KEY REQUIRED" (specs/slice-5a.md, Basemap).
# Free, 5M tile requests/month: https://carto.com/basemaps/apikey/
CARTO_KEY = os.environ.get("CARTO_KEY", "")
