"""The daily continuous aggregate -- the seam is apply_timescale(cur) and the
resulting daily_cf, queried through psycopg.

Row-count and bucket tests read the REAL materialised cagg, the same way the
model tests read real ingested weather (tests/test_pv.py). A fresh database
has to be backfilled and refreshed before they mean anything.
"""
from __future__ import annotations

import pytest
from psycopg import sql

from timescale import apply_timescale
from timescale.cagg import CAGG_NAME, CAGG_VERSION, version_marker

# 6 sites x 3,653 local days (2016-2025; leap years 2016, 2020, 2024).
EXPECTED_ROWS = 21_918
EXPECTED_DAYS = 3_653


def test_cagg_has_expected_columns(db):
    with db.cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = %s ORDER BY ordinal_position",
            (CAGG_NAME,),
        )
        cols = [r[0] for r in cur.fetchall()]
    assert cols == ["site_id", "day", "pv_cf", "wind_cf", "hours"]


def test_cagg_row_count(db):
    with db.cursor() as cur:
        cur.execute(f"SELECT count(*), count(DISTINCT day) FROM {CAGG_NAME}")
        rows, days = cur.fetchone()
    assert (rows, days) == (EXPECTED_ROWS, EXPECTED_DAYS)


def test_every_bucket_has_24_hours(db):
    """Timezone-aware bucketing aligns local days with the data span, so a
    bucket with anything but 24 hours means the bucketing or the span is not
    what we think.

    This asserts a property of the CURRENT dataset (complete local years
    2016-2025), not a permanent invariant. Backfilling a partial year will
    fail it with a legitimately partial final day -- that's information
    worth surfacing, not necessarily a fault. Check what changed before
    assuming the bucketing broke.
    """
    with db.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM {CAGG_NAME} WHERE hours <> 24")
        (bad,) = cur.fetchone()
    assert bad == 0


def test_cagg_matches_direct_computation(db):
    """Sampled site-month: the materialised value must equal the same
    aggregation computed straight off weather_hour."""
    with db.cursor() as cur:
        cur.execute(
            f"""
            SELECT c.pv_cf, c.wind_cf, d.pv_cf, d.wind_cf
            FROM (SELECT * FROM {CAGG_NAME}
                  WHERE site_id = 1 AND day = '2024-06-15 00:00:00+02') c
            JOIN LATERAL (
                SELECT avg(pv_capacity_factor(global_tilted_irradiance,
                                              temperature_2m)) AS pv_cf,
                       avg(wind_capacity_factor(wind_speed_100m, surface_pressure,
                                                temperature_2m)) AS wind_cf
                FROM weather_hour
                WHERE site_id = 1
                  AND ts >= '2024-06-15 00:00:00+02'
                  AND ts <  '2024-06-16 00:00:00+02'
            ) d ON true
            """
        )
        row = cur.fetchone()
    assert row is not None, "sampled day missing from the cagg"
    cagg_pv, cagg_wind, direct_pv, direct_wind = row
    assert cagg_pv == pytest.approx(direct_pv, abs=1e-6)
    assert cagg_wind == pytest.approx(direct_wind, abs=1e-6)


def test_apply_is_idempotent(tx):
    """Applying twice must not error and must leave exactly one cagg."""
    apply_timescale(tx)
    apply_timescale(tx)
    tx.execute(
        "SELECT count(*) FROM timescaledb_information.continuous_aggregates "
        "WHERE view_name = %s",
        (CAGG_NAME,),
    )
    assert tx.fetchone()[0] == 1


def test_version_mismatch_raises(tx):
    """A definition change without a matching marker must refuse to proceed
    silently -- the ADR 0004 failure mode, guarded (specs/slice-4.md)."""
    tx.execute(
        sql.SQL("COMMENT ON VIEW {} IS {}").format(
            sql.Identifier(CAGG_NAME),
            sql.Literal(version_marker("v-something-older")),
        )
    )
    with pytest.raises(RuntimeError, match="version"):
        apply_timescale(tx)


def test_version_marker_is_set(db):
    with db.cursor() as cur:
        cur.execute(
            "SELECT obj_description(%s::regclass, 'pg_class')", (CAGG_NAME,)
        )
        assert cur.fetchone()[0] == version_marker(CAGG_VERSION)
