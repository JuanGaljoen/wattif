"""Model function lifecycle -- the seam is apply_models against a real
database, observed through pg_proc.

The bug this pins (docs/adr/0004): CREATE OR REPLACE FUNCTION only replaces
an *identical* signature, so changing a model function's argument types
leaves the old overload behind. Because weather_hour's columns are `real`,
an exact-match stale `(real, real)` overload wins resolution over the
canonical `(double precision, double precision)` one -- and tests that pass
Python floats never see it.

Tests that create a bogus overload use `tx`, not `db`: DDL is transactional
in Postgres, so a failure mid-test unwinds instead of leaving a landmine in
the dev database (docs/adr/0003).
"""
from __future__ import annotations

import pytest

from models import apply_models

MODEL_FUNCTIONS = ["pv_capacity_factor", "wind_capacity_factor"]

BOGUS_OVERLOAD = (
    "CREATE OR REPLACE FUNCTION pv_capacity_factor(gti real, t_air real) "
    "RETURNS real LANGUAGE sql IMMUTABLE AS $$ SELECT 0.123::real $$"
)


def overloads(cur, name: str) -> list[str]:
    cur.execute(
        "SELECT pg_get_function_identity_arguments(oid) FROM pg_proc "
        "WHERE proname = %s ORDER BY oid",
        (name,),
    )
    return [row[0] for row in cur.fetchall()]


@pytest.mark.parametrize("name", MODEL_FUNCTIONS)
def test_exactly_one_overload_after_apply(db, name):
    with db.cursor() as cur:
        assert len(overloads(cur, name)) == 1


def test_stale_overload_is_removed_by_apply(tx):
    """The actual shipped defect, reproduced then fixed.

    A `(real, real)` overload is what slice 2 left behind. It must not
    survive an apply -- and it's the one that would silently win when
    called with weather_hour's real columns.
    """
    tx.execute(BOGUS_OVERLOAD)
    assert len(overloads(tx, "pv_capacity_factor")) == 2  # bug reproduced

    apply_models(tx)

    assert overloads(tx, "pv_capacity_factor") == [
        "gti double precision, t_air double precision"
    ]


def test_real_columns_resolve_to_the_canonical_function(tx):
    """Resolution check with real (float4) values, as weather_hour stores.

    Guards the specific failure mode: a stale exact-match overload taking
    precedence. 800/25 through the canonical formula gives 0.72
    (models/constants.py: NOCT=45, GAMMA=-0.004), never the stub's 0.123.
    """
    tx.execute(BOGUS_OVERLOAD)
    apply_models(tx)
    tx.execute("SELECT pv_capacity_factor(800.0::real, 25.0::real)")
    assert tx.fetchone()[0] == pytest.approx(0.72, abs=1e-3)
