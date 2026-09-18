"""Public interface: apply_models(cur) installs every generation model as a
SQL function. Everything else in this package is private -- callers never
import constants or ddl directly (specs/slice-2.md, Approach).
"""
from __future__ import annotations

from .ddl import (
    PV_NAME,
    PV_PARAMS,
    WIND_NAME,
    WIND_PARAMS,
    drop_stale_overloads_ddl,
    pv_function_ddl,
    wind_function_ddl,
)

__all__ = ["apply_models"]

_MODELS = (
    (PV_NAME, PV_PARAMS, pv_function_ddl),
    (WIND_NAME, WIND_PARAMS, wind_function_ddl),
)


def apply_models(cur) -> None:
    """Install each model function, then remove any stale overload of it.

    Create first, drop second: the canonical function is replaced in place
    (never absent, and safe for a dependent continuous aggregate), and only
    then does anything left over from an older signature get cleaned up.
    See docs/adr/0004 -- CREATE OR REPLACE alone silently leaves the old
    overload behind, and `real` columns bind to it in preference.
    """
    for name, params, build_ddl in _MODELS:
        cur.execute(build_ddl())
        cur.execute(drop_stale_overloads_ddl(name, params))
