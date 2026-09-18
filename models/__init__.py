"""Public interface: apply_models(cur) installs every generation model as a
SQL function. Everything else in this package is private -- callers never
import constants or ddl directly (specs/slice-2.md, Approach).
"""
from __future__ import annotations

from .ddl import pv_function_ddl

__all__ = ["apply_models"]


def apply_models(cur) -> None:
    cur.execute(pv_function_ddl())
