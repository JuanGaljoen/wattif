"""Generates the SQL function DDL for each model, from the Python constants in
models/constants.py. Applied at runtime (idempotent CREATE OR REPLACE) so the
database can never hold a definition that disagrees with the constants --
see specs/slice-2.md, Approach.
"""
from __future__ import annotations

from . import constants as c
from .curve import curve_arrays_sql


def pv_function_ddl() -> str:
    """pv_capacity_factor(gti, t_air) -> dimensionless capacity factor.

    T_cell = t_air + (NOCT-20)/800 * gti      -- simplified NOCT model
    cf     = (gti/1000) * (1 + GAMMA*(T_cell-25))
    """
    return f"""
    CREATE OR REPLACE FUNCTION pv_capacity_factor(
        gti double precision, t_air double precision
    )
    RETURNS real LANGUAGE sql IMMUTABLE AS $$
        SELECT CASE WHEN gti IS NULL OR t_air IS NULL THEN NULL ELSE (
            (gti / 1000.0) * (1 + {c.GAMMA} * (
                (t_air + ({c.NOCT} - 20) / 800.0 * gti) - 25
            ))
        )::real END
    $$;
    """


def wind_function_ddl() -> str:
    """wind_capacity_factor(wind_kmh, pressure_hpa, t_air) -> capacity factor.

    v   = wind_kmh / KMH_TO_MS                        -- km/h, NOT m/s (trap)
    rho = (pressure_hpa*HPA_TO_PA) / (R_SPECIFIC * (t_air+CELSIUS_TO_KELVIN))
    cf  = interp(curve, v) * (rho/RHO_STANDARD) / RATED_KW
    zero outside [CUT_IN_MS, CUT_OUT_MS]

    The curve is embedded as two constant arrays (models/curve.py) -- an
    IMMUTABLE function cannot query a table, and a cagg demands immutability.
    `v` is computed once in a CTE rather than re-divided at each use site.
    """
    speeds_sql, powers_sql = curve_arrays_sql()
    return f"""
    CREATE OR REPLACE FUNCTION wind_capacity_factor(
        wind_kmh double precision, pressure_hpa double precision,
        t_air double precision
    )
    RETURNS real LANGUAGE sql IMMUTABLE AS $$
        SELECT CASE
            WHEN wind_kmh IS NULL OR pressure_hpa IS NULL OR t_air IS NULL THEN NULL
            ELSE (
                WITH v AS (SELECT wind_kmh / {c.KMH_TO_MS} AS ms),
                lo AS (
                    SELECT sp, pw FROM unnest({speeds_sql}, {powers_sql}) AS t(sp, pw)
                    WHERE sp <= (SELECT ms FROM v) ORDER BY sp DESC LIMIT 1
                ),
                hi AS (
                    SELECT sp, pw FROM unnest({speeds_sql}, {powers_sql}) AS t(sp, pw)
                    WHERE sp >= (SELECT ms FROM v) ORDER BY sp ASC LIMIT 1
                )
                SELECT CASE
                    WHEN (SELECT ms FROM v) < {c.CUT_IN_MS}
                      OR (SELECT ms FROM v) > {c.CUT_OUT_MS} THEN 0.0::real
                    ELSE (
                        (SELECT CASE WHEN hi.sp = lo.sp THEN lo.pw
                              ELSE lo.pw + (hi.pw - lo.pw) * ((SELECT ms FROM v) - lo.sp)
                                   / (hi.sp - lo.sp)
                         END FROM lo, hi)
                        * ((pressure_hpa * {c.HPA_TO_PA})
                           / ({c.R_SPECIFIC} * (t_air + {c.CELSIUS_TO_KELVIN}))
                           / {c.RHO_STANDARD})
                        / {c.RATED_KW}
                    )::real
                END
            )
        END
    $$;
    """
