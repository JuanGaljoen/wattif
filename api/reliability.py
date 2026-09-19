"""The reliability metrics, as decided at slice 4's Understand.

Reliability here means **longest lull**, not annual averages: ten years of
hourly data supports the former far better than the latter, where P50/P90 is
a distribution of ten numbers and the solar spread is ~2% (README).

`db/reliability.sql` is the oracle for these numbers and stays the oracle.
The split of which aggregate answers which metric is the same one that
script uses, so the two are comparable:

    worst rolling 24h     hourly_cf
    hours/year below 10%  hourly_cf
    worst rolling 7 days  daily_cf
    P50 / P90 annual      daily_cf

The hybrid is (pv_cf + wind_cf) / 2 -- equal RATED CAPACITY of solar and
wind. That is a product choice, not a sourced constant; the README carries
it in the assumed column beside NOCT and GAMMA.
"""
from __future__ import annotations

from datetime import timedelta

from timescale import SITE_TIMEZONE

# PLAN.md's threshold, from "what does reliable mean": hours per year below
# 10% output. A stated product choice, not a derived figure.
LOW_OUTPUT = 0.10

# Each lull window, as (label, rows preceding). 24 hours over hourly_cf;
# 7 days over daily_cf.
_HOURS_24 = 23
_DAYS_7 = 6


# One pass over the site's hours, three argmins off it. The hybrid is
# DERIVED rather than windowed: averaging is linear, so
# avg((pv + wind) / 2) == (avg(pv) + avg(wind)) / 2 and a third window
# aggregate would recompute what the first two already hold. MATERIALIZED is
# load-bearing for the same reason it is in db/reliability.sql (docs/adr/
# 0005): without it PG inlines the CTE into each branch and recomputes the
# whole window three times.
WORST_24H = f"""
WITH win AS MATERIALIZED (
    SELECT hour,
           avg(pv_cf)   OVER w AS pv,
           avg(wind_cf) OVER w AS wind,
           count(*)     OVER w AS n
    FROM hourly_cf
    WHERE site_id = %(site_id)s
    WINDOW w AS (ORDER BY hour ROWS BETWEEN {_HOURS_24} PRECEDING AND CURRENT ROW)
)
(SELECT 'pv'     AS resource, pv     AS cf, hour FROM win WHERE n = {_HOURS_24 + 1}
   ORDER BY pv     LIMIT 1)
UNION ALL
(SELECT 'wind',   wind,   hour FROM win WHERE n = {_HOURS_24 + 1}
   ORDER BY wind   LIMIT 1)
UNION ALL
(SELECT 'hybrid', (pv + wind) / 2, hour FROM win WHERE n = {_HOURS_24 + 1}
   ORDER BY (pv + wind) / 2 LIMIT 1)
"""

WORST_7D = f"""
WITH win AS MATERIALIZED (
    SELECT day,
           avg(pv_cf)   OVER w AS pv,
           avg(wind_cf) OVER w AS wind,
           count(*)     OVER w AS n
    FROM daily_cf
    WHERE site_id = %(site_id)s
    WINDOW w AS (ORDER BY day ROWS BETWEEN {_DAYS_7} PRECEDING AND CURRENT ROW)
)
(SELECT 'pv'     AS resource, pv     AS cf, day FROM win WHERE n = {_DAYS_7 + 1}
   ORDER BY pv     LIMIT 1)
UNION ALL
(SELECT 'wind',   wind,   day FROM win WHERE n = {_DAYS_7 + 1}
   ORDER BY wind   LIMIT 1)
UNION ALL
(SELECT 'hybrid', (pv + wind) / 2, day FROM win WHERE n = {_DAYS_7 + 1}
   ORDER BY (pv + wind) / 2 LIMIT 1)
"""

# PV counts DAYLIGHT hours only -- gti > 0. Counting every hour would
# include every night hour and measure darkness rather than reliability
# (README). gti is in the cagg precisely so this filter is the same one
# db/reliability.sql applies, rather than a proxy.
#
# The year is the LOCAL year: `AT TIME ZONE` before `extract`, or the
# server's UTC pulls 1 January into the previous year (docs/adr/0007).
HOURS_BELOW = f"""
SELECT avg(pv_daylight)::float8 AS pv_daylight,
       avg(wind_hours)::float8  AS wind
FROM (
    SELECT extract(year FROM (hour AT TIME ZONE %(tz)s))              AS yr,
           count(*) FILTER (WHERE gti > 0 AND pv_cf < {LOW_OUTPUT})   AS pv_daylight,
           count(*) FILTER (WHERE wind_cf < {LOW_OUTPUT})             AS wind_hours
    FROM hourly_cf
    WHERE site_id = %(site_id)s
    GROUP BY yr
) yearly
"""

# P90 in energy is the yield EXCEEDED in 90% of years -- the 10th percentile
# of the distribution, percentile_cont(0.1), NOT 0.9 (README).
ANNUAL = """
SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY pv)   AS pv_p50,
       percentile_cont(0.1) WITHIN GROUP (ORDER BY pv)   AS pv_p90,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY wind) AS wind_p50,
       percentile_cont(0.1) WITHIN GROUP (ORDER BY wind) AS wind_p90,
       count(*)                                          AS years
FROM (
    SELECT extract(year FROM (day AT TIME ZONE %(tz)s)) AS yr,
           avg(pv_cf)   AS pv,
           avg(wind_cf) AS wind
    FROM daily_cf
    WHERE site_id = %(site_id)s
    GROUP BY yr
) a
"""


def _lull(cur, query: str, site_id: int, span_before) -> dict:
    """Run one worst-window query and shape it as {resource: {cf, start}}.

    The window runs from N rows BEFORE the returned row to that row, so the
    row's own timestamp is where the window ENDS. The chart shades from the
    start, so subtract the span.
    """
    cur.execute(query, {"site_id": site_id})
    out = {}
    for row in cur.fetchall():
        end = row["hour"] if "hour" in row else row["day"]
        out[row["resource"]] = {
            "cf": round(float(row["cf"]), 4),
            "start": (end - span_before).isoformat(),
            "end": end.isoformat(),
        }
    return out


def reliability(cur, site_id: int) -> dict:
    params = {"site_id": site_id, "tz": SITE_TIMEZONE}

    worst_24h = _lull(cur, WORST_24H, site_id, timedelta(hours=_HOURS_24))
    worst_7d = _lull(cur, WORST_7D, site_id, timedelta(days=_DAYS_7))

    cur.execute(HOURS_BELOW, params)
    hours = cur.fetchone()

    cur.execute(ANNUAL, params)
    annual = cur.fetchone()

    return {
        "site_id": site_id,
        "low_output_threshold": LOW_OUTPUT,
        "worst_24h": worst_24h,
        "worst_7d": worst_7d,
        "hours_below_10pct": {
            "pv_daylight": round(hours["pv_daylight"]),
            "wind": round(hours["wind"]),
        },
        "annual": {
            "pv": {"p50": round(annual["pv_p50"], 4),
                   "p90": round(annual["pv_p90"], 4)},
            "wind": {"p50": round(annual["wind_p50"], 4),
                     "p90": round(annual["wind_p90"], 4)},
            "years": annual["years"],
        },
    }
