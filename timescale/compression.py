"""Compression on weather_hour, and the background policies.

Measured 3.4x on a probe chunk (600 kB -> 176 kB) with segmentby = site_id.

Verified before adopting (specs/slice-4.md, Understand): INSERT ... ON
CONFLICT DO NOTHING still detects conflicts against a compressed chunk, new
rows still insert into one, and a continuous aggregate still refreshes over
compressed data. Slice 3's resumability guarantee therefore survives
compression -- tests/test_compression.py re-proves it against real
compressed chunks rather than trusting that note.
"""
from __future__ import annotations

HYPERTABLE = "weather_hour"

# A judgement call, not a sourced constant: recent chunks stay uncompressed
# so writes are cheap, everything older is read-only in practice. Every chunk
# in this project is historical, so effectively all of it compresses.
COMPRESS_AFTER = "30 days"

# The cagg refresh policy's moving window. Judgement calls, like
# COMPRESS_AFTER above -- not sourced constants. 90 days is comfortably wider
# than any plausible late-arriving correction to ERA5 reanalysis; end_offset
# of 1 day avoids rebuilding a day that may still be filling.
#
# It deliberately does NOT re-materialise history -- after a model
# coefficient changes, the full-range refresh is an explicit act
# (timescale.refresh_daily_cf).
REFRESH_START_OFFSET = "90 days"
REFRESH_END_OFFSET = "1 day"
REFRESH_INTERVAL = "1 day"


def compression_settings_ddl() -> str:
    return f"""
    ALTER TABLE {HYPERTABLE} SET (
        timescaledb.compress,
        timescaledb.compress_segmentby = 'site_id',
        timescaledb.compress_orderby   = 'ts DESC'
    );
    """


def ensure_compression(cur) -> None:
    """Enable compression and add its policy. Idempotent."""
    cur.execute(compression_settings_ddl())
    # %s::interval, not INTERVAL %s -- the INTERVAL keyword takes a literal,
    # not a bound parameter.
    cur.execute(
        "SELECT add_compression_policy(%s::regclass, %s::interval, "
        "if_not_exists => true)",
        (HYPERTABLE, COMPRESS_AFTER),
    )


def ensure_refresh_policy(cur, cagg_name: str) -> None:
    """Add the cagg's refresh policy. Idempotent."""
    cur.execute(
        "SELECT add_continuous_aggregate_policy("
        "  %s::regclass,"
        "  start_offset => %s::interval,"
        "  end_offset => %s::interval,"
        "  schedule_interval => %s::interval,"
        "  if_not_exists => true)",
        (cagg_name, REFRESH_START_OFFSET, REFRESH_END_OFFSET, REFRESH_INTERVAL),
    )


def compress_all(conn) -> tuple[int, str, str]:
    """Compress every eligible chunk now, rather than waiting for the policy.

    Returns (chunks_compressed, size_before, size_after). Needs its own
    autocommit connection: compress_chunk is a maintenance operation and
    cannot run inside a transaction block.
    """
    previous = conn.autocommit
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT pg_size_pretty(sum(total_bytes)) FROM "
                "hypertable_detailed_size(%s::regclass)",
                (HYPERTABLE,),
            )
            (before,) = cur.fetchone()

            cur.execute(
                "SELECT compress_chunk(c, if_not_compressed => true) "
                "FROM show_chunks(%s::regclass) c",
                (HYPERTABLE,),
            )
            compressed = cur.rowcount

            cur.execute(
                "SELECT pg_size_pretty(sum(total_bytes)) FROM "
                "hypertable_detailed_size(%s::regclass)",
                (HYPERTABLE,),
            )
            (after,) = cur.fetchone()
        return compressed, before, after
    finally:
        conn.autocommit = previous
