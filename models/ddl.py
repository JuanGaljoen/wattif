"""Generates the SQL function DDL for each model, from the Python constants in
models/constants.py. Applied at runtime (idempotent CREATE OR REPLACE) so the
database can never hold a definition that disagrees with the constants --
see specs/slice-2.md, Approach.
"""
from __future__ import annotations

from . import constants as c


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
