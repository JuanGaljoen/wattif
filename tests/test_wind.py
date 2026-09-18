"""Wind capacity-factor function -- the seam is wind_capacity_factor(wind_kmh,
pressure_hpa, t_air) through psycopg. Expected values are worked from the
vendored IEA 3.4MW/130 curve (data/IEA_Reference_3.4MW_130.csv) and the
formula in specs/slice-2.md, never recomputed the way the SQL computes them.
"""
from __future__ import annotations

import pytest


def wind_cf(cur, wind_kmh: float, pressure_hpa: float, t_air: float) -> float:
    cur.execute(
        "SELECT wind_capacity_factor(%s, %s, %s)", (wind_kmh, pressure_hpa, t_air)
    )
    return cur.fetchone()[0]


# Standard conditions (1013.25 hPa, 15 degC) so rho/rho0 == 1 and the curve's
# raw kW / RATED_KW is the expected capacity factor.
STD_HPA = 1013.25
STD_T = 15.0


def test_wind_below_cut_in_is_zero(cur):
    assert wind_cf(cur, 3.0 * 3.6 * 0.9, STD_HPA, STD_T) == 0.0  # ~2.7 m/s


def test_wind_kmh_not_ms(cur):
    # 36 km/h == 10 m/s. The curve's rated wind speed is 9.8 m/s (full rated
    # power, 3370 kW); at exactly 10 m/s the curve is already at/above rated,
    # so cf should be at or very near 1.0 -- NOT the near-zero you'd get by
    # treating 36 as if it were already m/s (36 m/s is past cut-out).
    cf = wind_cf(cur, 36.0, STD_HPA, STD_T)
    assert cf == pytest.approx(1.0, abs=0.02)


def test_wind_cutout_cliff(cur):
    at_cutout = wind_cf(cur, 25.0 * 3.6, STD_HPA, STD_T)
    past_cutout = wind_cf(cur, 25.1 * 3.6, STD_HPA, STD_T)
    far_past = wind_cf(cur, 30.0 * 3.6, STD_HPA, STD_T)
    assert at_cutout == pytest.approx(1.0, abs=0.01)
    assert past_cutout == 0.0
    assert far_past == 0.0


def test_wind_thinner_air_gives_less_power(cur):
    # Same wind speed, lower pressure (thinner air) -> less power.
    dense = wind_cf(cur, 9.8 * 3.6, STD_HPA, STD_T)
    thin = wind_cf(cur, 9.8 * 3.6, 900.0, STD_T)
    assert thin < dense


def test_wind_usable_inside_a_continuous_aggregate(cur):
    cur.execute(
        """
        CREATE MATERIALIZED VIEW IF NOT EXISTS _test_wind_cagg
        WITH (timescaledb.continuous) AS
        SELECT site_id, time_bucket('1 day', ts) AS day,
               avg(wind_capacity_factor(wind_speed_100m, surface_pressure,
                                         temperature_2m)) AS cf
        FROM weather_hour GROUP BY site_id, day WITH NO DATA
        """
    )
    cur.execute("DROP MATERIALIZED VIEW _test_wind_cagg")
