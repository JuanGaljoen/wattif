"""The continuous aggregates, both defined over the generation expression.

Over the expression, not raw weather: the temperature derate is nonlinear,
so f(avg(x)) != avg(f(x)) and the physics has to be evaluated per hour
inside the aggregate (PLAN.md, and docs/adr/0002).

    daily_cf   (site_id, day, pv_cf, wind_cf, hours)
    hourly_cf  (site_id, hour, pv_cf, wind_cf, gti)

Each is declared ONCE below as a Cagg record -- name, version and DDL
builder -- and everything that operates on them loops. The version-marker
check exists because a cagg has no OR REPLACE: CREATE ... IF NOT EXISTS
would silently keep an old definition, which is exactly the trap
docs/adr/0004 records. That logic must not be copied per aggregate.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from psycopg import sql

SITE_TIMEZONE = "Africa/Johannesburg"


@dataclass(frozen=True)
class Cagg:
    name: str
    version: str  # bump whenever the SELECT changes
    ddl: Callable[[], str]


def daily_cf_ddl() -> str:
    """Buckets on a CONSTANT timezone literal -- the repair ADR 0001 named.

    Constant only because all six sites share Africa/Johannesburg, a
    deliberate slice-3 choice. A site in another timezone invalidates this
    design, not just this query (specs/slice-4.md, Risks).
    """
    return f"""
    CREATE MATERIALIZED VIEW IF NOT EXISTS daily_cf
    WITH (timescaledb.continuous) AS
    SELECT site_id,
           time_bucket('1 day', ts, timezone => '{SITE_TIMEZONE}') AS day,
           avg(pv_capacity_factor(global_tilted_irradiance,
                                  temperature_2m))                 AS pv_cf,
           avg(wind_capacity_factor(wind_speed_100m, surface_pressure,
                                    temperature_2m))               AS wind_cf,
           count(*)                                                AS hours
    FROM weather_hour
    GROUP BY site_id, day
    WITH NO DATA;
    """


def hourly_cf_ddl() -> str:
    """No timezone argument, deliberately.

    daily_cf needs one because a day boundary is local. An hour boundary is
    not, and SAST has no DST, so the argument would be decoration. The
    timezone belongs at READ time, where docs/adr/0007 requires it -- "hours
    per year" needs the local year.

    gti is stored rather than inferred: the PV "hours below 10%" metric
    counts daylight hours only, and storing it lets the endpoint mirror
    db/reliability.sql's `gti > 0` filter literally instead of relying on
    `pv_cf > 0` being an exact proxy (specs/slice-5b.md).
    """
    return """
    CREATE MATERIALIZED VIEW IF NOT EXISTS hourly_cf
    WITH (timescaledb.continuous) AS
    SELECT site_id,
           time_bucket('1 hour', ts) AS hour,
           avg(pv_capacity_factor(global_tilted_irradiance,
                                  temperature_2m))                 AS pv_cf,
           avg(wind_capacity_factor(wind_speed_100m, surface_pressure,
                                    temperature_2m))               AS wind_cf,
           avg(global_tilted_irradiance)                           AS gti
    FROM weather_hour
    GROUP BY site_id, hour
    WITH NO DATA;
    """


DAILY = Cagg("daily_cf", "v1", daily_cf_ddl)
HOURLY = Cagg("hourly_cf", "v1", hourly_cf_ddl)

CAGGS = (DAILY, HOURLY)

# Kept as module constants because callers name daily_cf specifically --
# refresh_daily_cf, and slice 4's tests.
CAGG_NAME = DAILY.name
CAGG_VERSION = DAILY.version
HOURLY_NAME = HOURLY.name
HOURLY_VERSION = HOURLY.version


def version_marker(name: str, version: str) -> str:
    return f"wattif:{name}:{version}"


def stored_marker(cur, name: str) -> str | None:
    """The version marker on the existing cagg, or None if it has no comment.

    Returns None both when the cagg doesn't exist and when it carries no
    comment; the caller distinguishes those by whether it just created it.
    """
    cur.execute(
        "SELECT obj_description(c.oid, 'pg_class') FROM pg_class c "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE c.relname = %s AND n.nspname = 'public'",
        (name,),
    )
    row = cur.fetchone()
    return row[0] if row else None


def ensure_cagg(cur, cagg: Cagg) -> None:
    """Create the cagg if absent; refuse to proceed if it's a stale version.

    Does NOT materialise -- refresh_continuous_aggregate cannot run inside a
    transaction block, so that's a separate, explicit step (refresh_cagg).
    """
    existing = stored_marker(cur, cagg.name)
    expected = version_marker(cagg.name, cagg.version)
    if existing is not None and existing != expected:
        raise RuntimeError(
            f"{cagg.name} has version marker {existing!r}, expected {expected!r}. "
            f"A continuous aggregate has no OR REPLACE, so its definition cannot "
            f"be updated in place. Drop and rebuild it deliberately:\n"
            f"  DROP MATERIALIZED VIEW {cagg.name};\n"
            f"then re-apply and refresh."
        )
    cur.execute(cagg.ddl())
    # COMMENT ON VIEW, not MATERIALIZED VIEW: TimescaleDB creates and drops a
    # cagg with MATERIALIZED VIEW syntax, but the user-facing object is a
    # plain view (relkind 'v') over an internal materialisation hypertable.
    # COMMENT ON takes no bound parameters, so compose the literal safely.
    cur.execute(
        sql.SQL("COMMENT ON VIEW {} IS {}").format(
            sql.Identifier(cagg.name), sql.Literal(expected)
        )
    )
