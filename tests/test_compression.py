"""Compression and the background policies.

The load-bearing test here is the last one: slice 3's whole resumability
story rests on INSERT ... ON CONFLICT DO NOTHING, and TimescaleDB has
historically restricted that against compressed chunks. It reads real
compressed chunks, so `test_compressed_chunks_exist` guards it from passing
vacuously against uncompressed data.
"""
from __future__ import annotations

HYPERTABLE = "weather_hour"


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
    with db.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM timescaledb_information.jobs "
            "WHERE proc_name = 'policy_refresh_continuous_aggregate'"
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
