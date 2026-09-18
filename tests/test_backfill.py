"""Resumable backfill -- the seam is plan_jobs/backfill_one/run_backfill
against a real (rolled-back) transaction, with the network replaced by a
stub fetcher (specs/slice-3.md, Approach: "the fetcher is injected").
"""
from __future__ import annotations

from ingest.backfill import backfill_one, plan_jobs, run_backfill
from ingest.load import upsert_site
from ingest.openmeteo import Metadata
from ingest.sites import SITES

# Karoo already has real, committed 2024 data (slice 1) in the dev DB this
# fixture connects to -- tx isolates what a test WRITES, not what already
# exists. Write-tests use a site with no prior data instead of asserting
# global counts, so they hold under a fresh DB and this one alike.
WRITE_SITE = SITES[1]  # Upington

FAKE_META = (
    "latitude,longitude,elevation,utc_offset_seconds,timezone\n"
    "-28.45,21.26,825.0,7200,Africa/Johannesburg\n"
)

DATA_HEADER = (
    "time,global_tilted_irradiance (W/m2),shortwave_radiation (W/m2),"
    "direct_radiation (W/m2),diffuse_radiation (W/m2),"
    "direct_normal_irradiance (W/m2),temperature_2m (degC),"
    "wind_speed_100m (km/h),wind_direction_100m (deg),surface_pressure (hPa)"
)


def fake_csv(year: int, n_hours: int = 3) -> str:
    rows = [DATA_HEADER]
    for h in range(n_hours):
        rows.append(
            f"{year}-01-01T{h:02d}:00,500.0,400.0,300.0,100.0,600.0,"
            "20.0,36.0,180.0,1013.0"
        )
    return FAKE_META + "\n" + "\n".join(rows) + "\n"


def make_stub(n_hours: int = 3):
    calls = []

    def stub(site, year):
        calls.append((site.name, year))
        return fake_csv(year, n_hours)

    stub.calls = calls
    return stub


def seed_site(cur, site) -> int:
    # Real upsert (ON CONFLICT DO UPDATE), not a raw INSERT -- Karoo already
    # exists as committed data in the dev DB this fixture connects to.
    meta = Metadata(
        latitude=site.latitude, longitude=site.longitude, elevation_m=0.0,
        utc_offset_seconds=7200, timezone="Africa/Johannesburg",
    )
    return upsert_site(cur, site, meta)


def weather_row_count(cur, site) -> int:
    cur.execute(
        "SELECT count(*) FROM weather_hour w JOIN site s ON s.id = w.site_id "
        "WHERE s.name = %s",
        (site.name,),
    )
    return cur.fetchone()[0]


def test_six_sites_one_timezone():
    assert len(SITES) == 6
    assert all(s.azimuth_deg == 180.0 for s in SITES)  # southern-hemisphere trap
    assert len({s.name for s in SITES}) == 6


def test_plan_jobs_full_grid_minus_already_done(tx):
    # The dev DB this connects to already has Karoo 2024 (slice 1) committed
    # -- so "empty" isn't a safe assumption. Assert the real invariant:
    # planned == full grid minus whatever's genuinely done.
    tx.execute("SELECT count(*) FROM ingest_job WHERE status = 'done'")
    (already_done,) = tx.fetchone()
    jobs = plan_jobs(tx, SITES, list(range(2016, 2026)))
    assert len(jobs) == 60 - already_done


def test_plan_jobs_skips_done(tx):
    site_id = seed_site(tx, SITES[0])
    tx.execute(
        "INSERT INTO ingest_job (site_id, year, status) VALUES (%s, 2020, 'done')",
        (site_id,),
    )
    jobs = plan_jobs(tx, SITES, [2020])
    names = {s.name for s, _ in jobs}
    assert SITES[0].name not in names
    assert len(jobs) == 5


def test_plan_jobs_retries_running_and_error(tx):
    site_id = seed_site(tx, SITES[0])
    tx.execute(
        "INSERT INTO ingest_job (site_id, year, status) VALUES "
        "(%s, 2020, 'running'), (%s, 2021, 'error')",
        (site_id, site_id),
    )
    jobs = plan_jobs(tx, SITES, [2020, 2021])
    pairs = {(s.name, y) for s, y in jobs}
    assert (SITES[0].name, 2020) in pairs
    assert (SITES[0].name, 2021) in pairs


def test_backfill_inserts_rows(tx):
    stub = make_stub(3)
    site = WRITE_SITE
    inserted = backfill_one(tx, site, 2020, fetch=stub)
    assert inserted == 3
    assert weather_row_count(tx, site) == 3


def test_backfill_is_idempotent(tx):
    stub = make_stub(3)
    site = WRITE_SITE
    backfill_one(tx, site, 2020, fetch=stub)
    second = backfill_one(tx, site, 2020, fetch=stub)
    assert second == 0
    assert weather_row_count(tx, site) == 3


def test_backfill_resumes_after_interruption(tx):
    stub = make_stub(3)
    site = WRITE_SITE
    site_id = seed_site(tx, site)
    # Simulate a crash mid-fetch: the job row exists as 'running', no data
    # ever landed. plan_jobs must still consider this pending.
    tx.execute(
        "INSERT INTO ingest_job (site_id, year, status) VALUES (%s, 2020, 'running')",
        (site_id,),
    )
    assert len(plan_jobs(tx, [site], [2020])) == 1

    n = run_backfill(tx, [site], [2020], fetch=stub)
    assert n == 1
    tx.execute(
        "SELECT status FROM ingest_job WHERE site_id=%s AND year=2020", (site_id,)
    )
    assert tx.fetchone()[0] == "done"
    assert weather_row_count(tx, site) == 3


def test_backfill_rows_is_held_not_inserted(tx):
    # CLAUDE.md's carried-forward bug: a safe re-run (0 rows inserted) must
    # not overwrite ingest_job.rows with 0 -- it should still read the rows
    # actually held for that site-year.
    stub = make_stub(3)
    site = WRITE_SITE
    site_id = seed_site(tx, site)
    backfill_one(tx, site, 2020, fetch=stub)
    second = backfill_one(tx, site, 2020, fetch=stub)
    assert second == 0  # inserted this run -- unchanged behaviour
    tx.execute(
        "SELECT rows FROM ingest_job WHERE site_id=%s AND year=2020", (site_id,)
    )
    assert tx.fetchone()[0] == 3  # held -- the fix


def test_run_backfill_processes_only_pending(tx):
    stub = make_stub(3)
    site = WRITE_SITE
    n = run_backfill(tx, [site], [2020, 2021], fetch=stub)
    assert n == 2
    assert len(stub.calls) == 2

    n2 = run_backfill(tx, [site], [2020, 2021], fetch=stub)
    assert n2 == 0
    assert len(stub.calls) == 2  # no new fetches -- already done
