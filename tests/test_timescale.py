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
from timescale.cagg import (
    CAGG_NAME,
    CAGG_VERSION,
    HOURLY_NAME,
    HOURLY_VERSION,
    version_marker,
)

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
            sql.Literal(version_marker(CAGG_NAME, "v-something-older")),
        )
    )
    with pytest.raises(RuntimeError, match="version"):
        apply_timescale(tx)


def test_version_marker_is_set(db):
    with db.cursor() as cur:
        cur.execute(
            "SELECT obj_description(%s::regclass, 'pg_class')", (CAGG_NAME,)
        )
        assert cur.fetchone()[0] == version_marker(CAGG_NAME, CAGG_VERSION)


# ---- hourly_cf (slice 5b) -------------------------------------------------
#
# A second aggregate, over the same generation expression at hourly
# resolution. It exists because the reliability metrics are hourly-led and
# the physics is what makes them slow: evaluating the model functions over
# 526k rows costs ~41.6s, the windowing over precomputed values ~126ms per
# site (specs/slice-5b.md, measured). docs/adr/0005 named this fix.

HOURLY_EXPECTED_ROWS = 526_032


def test_hourly_cagg_has_expected_columns(db):
    """gti is stored, not inferred.

    The PV "hours below 10%" metric counts DAYLIGHT hours only -- counting
    all of them measures darkness. `pv_cf > 0` would be an exact proxy, but
    storing gti lets the endpoint mirror db/reliability.sql's `gti > 0`
    filter literally, which is what makes "matches the oracle" checkable
    rather than argued.
    """
    with db.cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = %s ORDER BY ordinal_position",
            (HOURLY_NAME,),
        )
        cols = [r[0] for r in cur.fetchall()]
    assert cols == ["site_id", "hour", "pv_cf", "wind_cf", "gti"]


def test_hourly_cagg_row_count(db):
    """One row per site-hour: 6 sites x 87,672 hours (2016-2025 inclusive,
    three leap years). Same span as weather_hour, because a 1-hour bucket
    over already-hourly data is one-to-one."""
    with db.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM {HOURLY_NAME}")
        (rows,) = cur.fetchone()
        cur.execute("SELECT count(*) FROM weather_hour")
        (raw,) = cur.fetchone()
    assert rows == HOURLY_EXPECTED_ROWS
    assert rows == raw


def test_hourly_cagg_matches_direct_computation(db):
    """A sampled site-hour must equal the physics applied straight to the
    weather row -- the aggregate is a cache of that, nothing more."""
    with db.cursor() as cur:
        cur.execute(
            f"""
            SELECT h.pv_cf, h.wind_cf, h.gti,
                   pv_capacity_factor(w.global_tilted_irradiance,
                                      w.temperature_2m),
                   wind_capacity_factor(w.wind_speed_100m, w.surface_pressure,
                                        w.temperature_2m),
                   w.global_tilted_irradiance
            FROM {HOURLY_NAME} h
            JOIN weather_hour w ON w.site_id = h.site_id AND w.ts = h.hour
            WHERE h.site_id = 1 AND h.hour = '2024-06-15 12:00:00+02'
            """
        )
        row = cur.fetchone()
    assert row is not None, "sampled hour missing from the cagg"
    cagg_pv, cagg_wind, cagg_gti, direct_pv, direct_wind, direct_gti = row

    # 1e-6, not tighter: the model functions RETURN `real` (float4, ~7
    # significant digits), while the cagg stores avg(real) as float8. In the
    # database the two are equal -- their difference is exactly 0 -- but the
    # float4 arrives as the text '0.77518845' and the float8 as
    # '0.7751884460449219', which psycopg parses into different Python
    # floats. A tolerance below float4's own precision is asserting on a
    # rendering artifact, not on the physics.
    # rel, not abs: gti is ~845 W/m2 where float4's ~7 digits put the
    # widening gap at ~1e-5, while a capacity factor sits in [0, 1] where it
    # is ~1e-8. One relative tolerance covers both honestly; abs=1e-9 keeps
    # a genuine zero comparable.
    tol = dict(rel=1e-6, abs=1e-9)
    assert cagg_pv == pytest.approx(direct_pv, **tol)
    assert cagg_wind == pytest.approx(direct_wind, **tol)
    assert cagg_gti == pytest.approx(direct_gti, **tol)


def test_hourly_version_marker_is_set(db):
    with db.cursor() as cur:
        cur.execute(
            "SELECT obj_description(%s::regclass, 'pg_class')", (HOURLY_NAME,)
        )
        assert cur.fetchone()[0] == version_marker(HOURLY_NAME, HOURLY_VERSION)


def test_both_caggs_have_refresh_policies(db):
    """Each aggregate gets its own policy -- apply_timescale loops rather
    than naming daily_cf, so adding a third is a one-line declaration."""
    with db.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM timescaledb_information.jobs "
            "WHERE proc_name = 'policy_refresh_continuous_aggregate'"
        )
        assert cur.fetchone()[0] == 2
