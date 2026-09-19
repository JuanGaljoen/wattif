"""The seed corpus: what a clean clone depends on.

Every fixture here is synthetic (`__test_only__`, ids in a reserved band) and
every write runs on the rollback-isolated `tx` connection. Both `site` and
`weather_hour` hold real data in any working database, so nothing in this file
may assume either table is empty -- docs/adr/0003.
"""
from __future__ import annotations

import gzip
from datetime import datetime, timezone

import pytest

from ingest.backfill import COPY_COLUMNS
from ingest.seed import SITE_COLUMNS, is_empty, restore

# Well above the real site ids (1..115) and above anything a sequence will
# reach in this test's lifetime, so a synthetic row can never collide with a
# real one even if a rollback were somehow missed.
TEST_SITE_ID = 900_001
TEST_SITE_NAME = "__test_only__ seed site"


def write_seed(tmp_path, *, hours: int = 3):
    """A two-file seed in the same format `dump()` writes, with one site."""
    sites_csv = tmp_path / "site.csv"
    sites_csv.write_text(
        f"{TEST_SITE_ID},{TEST_SITE_NAME},-30.0,25.0,1000,"
        f"Africa/Johannesburg,7200,30,180\n"
    )

    # site_id, ts, local_date, then the nine weather variables.
    lines = []
    for hour in range(hours):
        ts = datetime(2024, 6, 1, hour, tzinfo=timezone.utc)
        values = ",".join(str(float(hour)) for _ in range(len(COPY_COLUMNS) - 3))
        lines.append(f"{TEST_SITE_ID},{ts.isoformat()},2024-06-01,{values}")

    weather_gz = tmp_path / "weather_hour.csv.gz"
    with gzip.open(weather_gz, "wt") as fh:
        fh.write("\n".join(lines) + "\n")

    return weather_gz, sites_csv


@pytest.fixture
def seed(tmp_path):
    return write_seed(tmp_path)


def restore_into(tx, seed) -> int:
    """`restore` wants a connection; `tx` is the cursor that owns the rollback."""
    weather_gz, sites_csv = seed
    return restore(tx.connection, weather_gz=weather_gz, sites_csv=sites_csv)


def test_restore_inserts_the_seed(tx, seed):
    assert restore_into(tx, seed) == 3

    tx.execute("SELECT count(*) FROM weather_hour WHERE site_id = %s", (TEST_SITE_ID,))
    assert tx.fetchone()[0] == 3


def test_restore_preserves_site_ids(tx, seed):
    """Ids survive a dump/restore: weather_hour references them, and a URL
    like /api/sites/1/daily is a thing people bookmark."""
    restore_into(tx, seed)

    tx.execute("SELECT id FROM site WHERE name = %s", (TEST_SITE_NAME,))
    assert tx.fetchone()[0] == TEST_SITE_ID


def test_restore_bumps_the_sequence_past_the_restored_ids(tx, seed):
    """Explicit ids do not advance the serial, so the next real insert would
    collide on the primary key. `restore` setvals past them."""
    restore_into(tx, seed)

    tx.execute("SELECT nextval(pg_get_serial_sequence('site', 'id'))")
    assert tx.fetchone()[0] > TEST_SITE_ID


def test_restore_is_idempotent(tx, seed):
    """The property the backfill has and the restore must share: running it
    twice inserts nothing the second time."""
    assert restore_into(tx, seed) == 3
    assert restore_into(tx, seed) == 0

    tx.execute("SELECT count(*) FROM weather_hour WHERE site_id = %s", (TEST_SITE_ID,))
    assert tx.fetchone()[0] == 3


def test_restore_without_a_seed_file_raises(tx, tmp_path):
    """Startup guards on this before it decides to seed, so the failure has to
    be loud rather than a silent no-op that leaves an empty map."""
    with pytest.raises(FileNotFoundError):
        restore(
            tx.connection,
            weather_gz=tmp_path / "nope.csv.gz",
            sites_csv=tmp_path / "site.csv",
        )


def test_is_empty_is_false_once_there_is_weather(tx, seed):
    restore_into(tx, seed)
    assert is_empty(tx) is False


def test_is_empty_is_true_on_a_database_with_no_weather(tx):
    """The branch that decides whether a clean clone seeds itself.

    Observed by swapping the table out inside the transaction rather than
    emptying it: DDL is transactional in Postgres, so the rename is undone on
    rollback, and it works regardless of which chunks are compressed.
    """
    tx.execute("ALTER TABLE weather_hour RENAME TO weather_hour_real")
    tx.execute("CREATE TABLE weather_hour (LIKE weather_hour_real INCLUDING ALL)")

    assert is_empty(tx) is True


def test_seed_columns_match_the_tables_they_restore_into(tx):
    """The dump and the restore share one column list each; a schema change
    that adds a column must be reflected here or the CSV silently misaligns."""
    tx.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'site' ORDER BY ordinal_position"
    )
    assert [r[0] for r in tx.fetchall()] == SITE_COLUMNS

    tx.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'weather_hour' ORDER BY ordinal_position"
    )
    assert [r[0] for r in tx.fetchall()] == COPY_COLUMNS
