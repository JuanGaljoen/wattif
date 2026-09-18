"""Fetch one site-year of hourly weather from the Open-Meteo archive as CSV.

Data: Open-Meteo.com, CC BY 4.0. Hourly ERA5 archive, unmodified on fetch;
derived generation figures elsewhere in this project are modifications.
"""
from __future__ import annotations

import csv
import io
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"

# Nine variables keeps the call weighting at max(1, vars/10) == 1.0.
# A TENTH PUSHES IT OVER, to 1.1x. Don't add one casually.
VARIABLES = [
    "global_tilted_irradiance",
    "shortwave_radiation",
    "direct_radiation",
    "diffuse_radiation",
    "direct_normal_irradiance",
    "temperature_2m",
    "wind_speed_100m",
    "wind_direction_100m",
    "surface_pressure",
]


@dataclass(frozen=True)
class SiteSpec:
    name: str
    latitude: float
    longitude: float
    tilt_deg: float
    # 0 = SOUTH, 180 = NORTH in Open-Meteo's convention. Southern-hemisphere
    # sites face north. Getting this backwards is a ~9x error that looks
    # plausible -- see PLAN, "The two traps".
    azimuth_deg: float


@dataclass(frozen=True)
class Metadata:
    latitude: float
    longitude: float
    elevation_m: float
    utc_offset_seconds: int
    timezone: str


def fetch_csv(
    site: SiteSpec, year: int, *, timeout: float = 120.0, attempts: int = 4
) -> str:
    """One site-year of hourly CSV.

    timezone=auto, not UTC: it is the only way to learn the site's real
    utc_offset_seconds. With timezone=UTC the API dutifully reports an offset of
    0, which would make local_date identical to the UTC date for every site --
    silently defeating the column's entire purpose. The trade is that the `time`
    column comes back as local wall time; parse() converts it.

    Note the year boundaries are then LOCAL years, which is what we want: local
    years tile with no gap or overlap in local_date.
    """
    params = {
        "latitude": site.latitude,
        "longitude": site.longitude,
        "start_date": f"{year}-01-01",
        "end_date": f"{year}-12-31",
        "hourly": ",".join(VARIABLES),
        "timezone": "auto",
        "tilt": site.tilt_deg,
        "azimuth": site.azimuth_deg,
        "format": "csv",
    }
    last = ""
    attempt = 0
    while attempt < attempts:
        r = httpx.get(ARCHIVE_URL, params=params, timeout=timeout)
        if r.status_code == 429:
            # A pause, not a failed attempt -- doesn't consume the attempts
            # budget. At 6-site x 10-year backfill scale this is expected,
            # not exceptional (specs/slice-3.md, Approach).
            time.sleep(int(r.headers.get("Retry-After", 60)))
            continue
        r.raise_for_status()
        # The archive returns HTTP 200 with a plain-text error body on a
        # server-side timeout ("Unexpected error while streaming data:
        # timeoutReached"). raise_for_status does NOT catch it. Observed live,
        # and transient -- so validate the body and retry.
        if r.text.lstrip().startswith("latitude,"):
            return r.text
        last = r.text.strip()[:200]
        attempt += 1
        if attempt < attempts:
            time.sleep(2 * attempt)
    raise RuntimeError(f"archive returned a non-CSV body {attempts}x: {last!r}")


def parse(body: str) -> tuple[Metadata, list[dict[str, str]]]:
    """Split the CSV into its metadata header and its hourly rows.

    Shape is: metadata header, metadata values, blank line, data header, data.
    We locate the blank line rather than hardcoding SKIP 4 -- a format shift of
    one line would otherwise load every column one position out, silently.
    """
    lines = body.splitlines()
    try:
        blank = next(i for i, ln in enumerate(lines) if not ln.strip())
    except StopIteration:  # pragma: no cover - only on an API format change
        raise ValueError("no blank separator line; CSV layout changed") from None
    if blank < 2:
        raise ValueError(f"metadata block too short ({blank} lines)")

    meta_rows = list(csv.DictReader(io.StringIO("\n".join(lines[:blank]))))
    if len(meta_rows) != 1:
        raise ValueError(f"expected 1 metadata row, got {len(meta_rows)}")
    m = meta_rows[0]
    meta = Metadata(
        latitude=float(m["latitude"]),
        longitude=float(m["longitude"]),
        elevation_m=float(m["elevation"]),
        utc_offset_seconds=int(m["utc_offset_seconds"]),
        timezone=m["timezone"],
    )

    data = list(csv.DictReader(io.StringIO("\n".join(lines[blank + 1 :]))))
    return meta, data


def column_for(header: list[str], variable: str) -> str:
    """Data headers carry units, e.g. 'temperature_2m (degC)'. Match the stem."""
    for h in header:
        if h == variable or h.startswith(variable + " ("):
            return h
    raise KeyError(f"{variable!r} not in CSV header: {header}")


def rows_for_copy(
    site_id: int, meta: Metadata, data: list[dict[str, str]]
) -> list[tuple]:
    """(site_id, ts, local_date, *nine values) tuples, ready for COPY."""
    if not data:
        return []
    header = list(data[0].keys())
    time_col = header[0]  # 'time'
    cols = [column_for(header, v) for v in VARIABLES]
    offset = timedelta(seconds=meta.utc_offset_seconds)

    out = []
    for row in data:
        # `time` is LOCAL wall time (timezone=auto). The local date is its date
        # part directly; UTC is that minus the offset.
        local = datetime.fromisoformat(row[time_col])
        ts = (local - offset).replace(tzinfo=timezone.utc)
        values = [float(row[c]) if row[c] not in ("", "NaN") else None for c in cols]
        out.append((site_id, ts, local.date(), *values))
    return out
