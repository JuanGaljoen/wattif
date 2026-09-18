"""The daily continuous aggregate, daily_cf.

Defined over the generation expression, not raw weather: the temperature
derate is nonlinear, so f(avg(x)) != avg(f(x)) and the physics has to be
evaluated per hour inside the aggregate (PLAN.md, and docs/adr/0002).

Buckets on a CONSTANT timezone literal -- the repair ADR 0001 named. It is
constant only because all six sites share Africa/Johannesburg, a deliberate
slice-3 choice. A site in another timezone invalidates this design, not just
this query (specs/slice-4.md, Risks).
"""
from __future__ import annotations

from psycopg import sql

CAGG_NAME = "daily_cf"

# Bump whenever the SELECT below changes. apply_timescale refuses to run
# against a cagg whose stored marker disagrees -- a cagg has no OR REPLACE,
# so CREATE ... IF NOT EXISTS would otherwise silently keep the old
# definition, which is exactly the trap docs/adr/0004 records.
CAGG_VERSION = "v1"

SITE_TIMEZONE = "Africa/Johannesburg"


def version_marker(version: str = CAGG_VERSION) -> str:
    return f"wattif:{CAGG_NAME}:{version}"


def cagg_ddl() -> str:
    return f"""
    CREATE MATERIALIZED VIEW IF NOT EXISTS {CAGG_NAME}
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


def stored_marker(cur) -> str | None:
    """The version marker on the existing cagg, or None if it has no comment.

    Returns None both when the cagg doesn't exist and when it carries no
    comment; the caller distinguishes those by whether it just created it.
    """
    cur.execute(
        "SELECT obj_description(c.oid, 'pg_class') FROM pg_class c "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE c.relname = %s AND n.nspname = 'public'",
        (CAGG_NAME,),
    )
    row = cur.fetchone()
    return row[0] if row else None


def ensure_cagg(cur) -> None:
    """Create daily_cf if absent; refuse to proceed if it's a stale version.

    Does NOT materialise -- refresh_continuous_aggregate cannot run inside a
    transaction block, so that's a separate, explicit step.
    """
    existing = stored_marker(cur)
    expected = version_marker()
    if existing is not None and existing != expected:
        raise RuntimeError(
            f"{CAGG_NAME} has version marker {existing!r}, expected {expected!r}. "
            f"A continuous aggregate has no OR REPLACE, so its definition cannot "
            f"be updated in place. Drop and rebuild it deliberately:\n"
            f"  DROP MATERIALIZED VIEW {CAGG_NAME};\n"
            f"then re-apply and refresh."
        )
    cur.execute(cagg_ddl())
    # COMMENT ON VIEW, not MATERIALIZED VIEW: TimescaleDB creates and drops a
    # cagg with MATERIALIZED VIEW syntax, but the user-facing object is a
    # plain view (relkind 'v') over an internal materialisation hypertable.
    # COMMENT ON takes no bound parameters, so compose the literal safely.
    cur.execute(
        sql.SQL("COMMENT ON VIEW {} IS {}").format(
            sql.Identifier(CAGG_NAME), sql.Literal(expected)
        )
    )
