"""Loads the vendored IEA 3.4MW/130 power curve and renders it as the two
constant SQL arrays the wind function interpolates between.

The curve must be embedded in the function body, not held in a lookup table:
an IMMUTABLE function cannot query a table, and a continuous aggregate demands
immutability -- verified during Design. See data/LICENSE-NREL (BSD-3-Clause,
Alliance for Sustainable Energy, LLC) and
docs/research/2026-09-18-model-coefficients.md.
"""
from __future__ import annotations

import csv
from pathlib import Path

CSV_PATH = Path(__file__).parent.parent / "data" / "IEA_Reference_3.4MW_130.csv"


def load_curve() -> list[tuple[float, float]]:
    """[(wind speed m/s, power kW), ...] straight from the vendored CSV."""
    with CSV_PATH.open() as f:
        reader = csv.DictReader(f)
        return [
            (float(row["Wind Speed [m/s]"]), float(row["Power [kW]"]))
            for row in reader
        ]


def curve_arrays_sql() -> tuple[str, str]:
    """The curve as two Postgres ARRAY[...] literals: (speeds, powers)."""
    curve = load_curve()
    speeds = ",".join(str(s) for s, _ in curve)
    powers = ",".join(str(p) for _, p in curve)
    return f"ARRAY[{speeds}]", f"ARRAY[{powers}]"
