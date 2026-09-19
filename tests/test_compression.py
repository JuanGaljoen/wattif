"""Compression and the background policies.

The load-bearing test here is the last one: slice 3's whole resumability
story rests on INSERT ... ON CONFLICT DO NOTHING, and TimescaleDB has
historically restricted that against compressed chunks. It reads real
compressed chunks, so `test_compressed_chunks_exist` guards it from passing
vacuously against uncompressed data.
"""
from __future__ import annotations

from ingest.backfill import load

HYPERTABLE = "weather_hour"

COMPRESSED_ROWS = """
    SELECT w.site_id, w.ts, w.local_date, w.global_tilted_irradiance,
           w.shortwave_radiation, w.direct_radiation, w.diffuse_radiation,
           w.direct_normal_irradiance, w.temperature_2m, w.wind_speed_100m,
           w.wind_direction_100m, w.surface_pressure
    FROM timescaledb_information.chunks c
    JOIN weather_hour w ON w.ts >= c.range_start AND w.ts < c.range_end
    WHERE c.hypertable_name = %s AND c.is_compressed
    LIMIT %s
"""


def test_compression_settings_applied(db):
    with db.cursor() as cur:
        cur.execute(
            "SELECT segmentby, orderby "
            "FROM timescaledb_information.hypertable_compression_settings "
            "WHERE hypertable = %s::regclass",
            (HYPERTABLE,),
        )
        row = cur.fetchone()
    assert row == ("site_id", "ts DESC")


def test_compression_policy_exists(db):
    with db.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM timescaledb_information.jobs "
            "WHERE proc_name = 'policy_compression' AND hypertable_name = %s",
            (HYPERTABLE,),
        )
        assert cur.fetchone()[0] == 1


def test_refresh_policy_exists(db):
    """Scoped to daily_cf by name.

    It counted every refresh policy until slice 5b added hourly_cf, at which
    point it failed on a correct change -- the count was standing in for
    "daily_cf has one". tests/test_timescale.py owns the "every cagg has a
    policy" assertion.
    """
    with db.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM timescaledb_information.jobs j "
            "JOIN timescaledb_information.continuous_aggregates c "
            "  ON c.materialization_hypertable_name = j.hypertable_name "
            "WHERE j.proc_name = 'policy_refresh_continuous_aggregate' "
            "  AND c.view_name = 'daily_cf'"
        )
        assert cur.fetchone()[0] == 1


def test_compressed_chunks_exist(db):
    """Guards the test below from being vacuous."""
    with db.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM timescaledb_information.chunks "
            "WHERE hypertable_name = %s AND is_compressed",
            (HYPERTABLE,),
        )
        assert cur.fetchone()[0] > 0


def test_backfill_idempotency_survives_compression(tx):
    """The slice-3 guarantee, re-proven against compressed data.

    Takes a real row from a compressed chunk and re-inserts it the way
    ingest/backfill.py does. It must insert nothing and leave the stored
    value untouched.
    """
    tx.execute(
        """
        SELECT w.site_id, w.ts, w.local_date, w.temperature_2m
        FROM timescaledb_information.chunks c
        JOIN weather_hour w
          ON w.ts >= c.range_start AND w.ts < c.range_end
        WHERE c.hypertable_name = %s AND c.is_compressed
        LIMIT 1
        """,
        (HYPERTABLE,),
    )
    row = tx.fetchone()
    assert row is not None, "no row found in a compressed chunk"
    site_id, ts, local_date, original_temp = row

    tx.execute(
        "INSERT INTO weather_hour (site_id, ts, local_date, temperature_2m) "
        "VALUES (%s, %s, %s, %s) ON CONFLICT (site_id, ts) DO NOTHING",
        (site_id, ts, local_date, -999.0),
    )
    assert tx.rowcount == 0  # conflict detected, nothing written

    tx.execute(
        "SELECT temperature_2m FROM weather_hour WHERE site_id = %s AND ts = %s",
        (site_id, ts),
    )
    assert tx.fetchone()[0] == original_temp  # not overwritten by -999


def test_production_load_path_is_idempotent_on_compressed_chunks(tx):
    """The REAL ingest mechanism against compressed data.

    The test above re-inserts a single row directly. Production doesn't do
    that -- ingest.backfill.load COPYs into a temp stage and then
    INSERT ... SELECT ... ON CONFLICT DO NOTHING. That's a different code
    path, and it's the one slice 3's resumability actually depends on, so it
    gets its own check against compressed chunks.
    """
    tx.execute(COMPRESSED_ROWS, (HYPERTABLE, 100))
    rows = tx.fetchall()
    assert len(rows) == 100, "need compressed rows to make this meaningful"

    tx.execute("SELECT count(*) FROM weather_hour")
    (before,) = tx.fetchone()

    inserted = load(tx, rows)  # COPY -> stage -> INSERT ON CONFLICT DO NOTHING

    tx.execute("SELECT count(*) FROM weather_hour")
    (after,) = tx.fetchone()

    assert inserted == 0
    assert after == before
