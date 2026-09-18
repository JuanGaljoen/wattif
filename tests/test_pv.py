"""PV capacity-factor function — the seam is pv_capacity_factor(gti, t_air)
called through psycopg, never the Python constants directly.

Expected values are worked by hand from the frozen formula (specs/slice-2.md),
not recomputed the way the SQL computes them.
"""
from __future__ import annotations

import pytest

from ingest.load import KAROO


def pv_cf(cur, gti: float, t_air: float) -> float:
    cur.execute("SELECT pv_capacity_factor(%s, %s)", (gti, t_air))
    return cur.fetchone()[0]


def test_pv_zero_at_night(cur):
    assert pv_cf(cur, 0.0, 15.0) == 0.0


def test_pv_known_value_worked_by_hand(cur):
    # GTI=800, T_air=25. NOCT=45.0, GAMMA=-0.004 (specs/slice-2.md constants).
    # T_cell = 25 + (45-20)/800*800 = 25 + 25 = 50
    # cf     = (800/1000) * (1 + -0.004*(50-25)) = 0.8 * (1 - 0.1) = 0.72
    assert pv_cf(cur, 800.0, 25.0) == pytest.approx(0.72, abs=1e-3)


def test_pv_hot_cell_derates_below_cold_cell(cur):
    # Same GTI, hotter air -> hotter cell -> lower capacity factor.
    cold = pv_cf(cur, 900.0, 10.0)
    hot = pv_cf(cur, 900.0, 40.0)
    assert hot < cold


def test_azimuth_trap_guard():
    # The 9.4x trap (PLAN.md "The two traps") lives in the fetch parameters,
    # not the generation model: southern-hemisphere sites must face north
    # (azimuth 180), or GTI itself arrives wrong before any model runs.
    assert KAROO.latitude < 0
    assert KAROO.azimuth_deg == 180.0


def test_pv_usable_inside_a_continuous_aggregate(cur):
    # A cagg demands an IMMUTABLE expression bucketed on the hypertable's time
    # column -- verified live during Understand. This pins that the function
    # itself still qualifies, not just a scratch copy of it.
    cur.execute(
        """
        CREATE MATERIALIZED VIEW IF NOT EXISTS _test_pv_cagg
        WITH (timescaledb.continuous) AS
        SELECT site_id, time_bucket('1 day', ts) AS day,
               avg(pv_capacity_factor(global_tilted_irradiance, temperature_2m)) AS cf
        FROM weather_hour GROUP BY site_id, day WITH NO DATA
        """
    )
    cur.execute("DROP MATERIALIZED VIEW _test_pv_cagg")


def test_karoo_2024_integrity(cur):
    # Slice 1 ingested Karoo 2024 (8,784 hours, leap year). Every row has a
    # non-NULL capacity factor, and southern-hemisphere seasonality holds:
    # December (summer) outperforms June (winter).
    cur.execute(
        "SELECT count(*) FROM weather_hour w JOIN site s ON s.id = w.site_id "
        "WHERE s.name = 'Karoo' AND w.local_date >= '2024-01-01' "
        "AND w.local_date < '2025-01-01'"
    )
    (total,) = cur.fetchone()
    assert total == 8784

    cur.execute(
        "SELECT count(*) FROM weather_hour w JOIN site s ON s.id = w.site_id "
        "WHERE s.name = 'Karoo' AND w.local_date >= '2024-01-01' "
        "AND w.local_date < '2025-01-01' "
        "AND pv_capacity_factor(w.global_tilted_irradiance, w.temperature_2m) IS NULL"
    )
    (nulls,) = cur.fetchone()
    assert nulls == 0

    cur.execute(
        "SELECT "
        "avg(pv_capacity_factor(global_tilted_irradiance, temperature_2m)) "
        "FILTER (WHERE local_date >= '2024-12-01' AND local_date < '2025-01-01'), "
        "avg(pv_capacity_factor(global_tilted_irradiance, temperature_2m)) "
        "FILTER (WHERE local_date >= '2024-06-01' AND local_date < '2024-07-01') "
        "FROM weather_hour w JOIN site s ON s.id = w.site_id WHERE s.name = 'Karoo'"
    )
    december_cf, june_cf = cur.fetchone()
    assert december_cf > june_cf
