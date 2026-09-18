# SLICE-3 — Resumable backfill: 6 sites x 10 years

Classification: **feature** (full spine, three checkpoints)
Status: Design frozen 2026-09-18 · not yet built
No Jira. This file is the source of truth; the branch's commits are the rest.

## Why

Ingest is single-site, single-year, hardcoded to Karoo. PLAN.md:153 wants the
real thing: "CSV -> COPY, resumable per (site, year), re-runnable safely
(**idempotency is what will actually break -- test it**). Seed 6 sites."

## Success criteria (the bar Verify checks)

- [ ] All 6 sites in `site` with correct metadata (elevation, timezone
      `Africa/Johannesburg`, `utc_offset_seconds` 7200)
- [ ] 60 `ingest_job` rows, every one `status='done'`
- [ ] `weather_hour` holds **526,032** rows across the 6 sites
      (87,672/site = 7x8760 + 3x8784; leap years 2016, 2020, 2024)
- [ ] Re-running the full backfill inserts 0 rows and completes without error
- [ ] `ingest_job.rows` equals rows *held* for that (site, year), not rows
      inserted this run (the carried-forward bug, CLAUDE.md)
- [ ] An interrupted backfill resumes and finishes without duplicates
- [ ] A 429 pauses and retries rather than raising

## The six sites

All `Africa/Johannesburg` (UTC+2, no DST), all azimuth 180 (NORTH -- southern
hemisphere, the 9.4x trap). Tilt follows the repo's existing precedent from
Karoo: tilt ~= |latitude|, an **assumed** rule of thumb, not a cited optimum.

| Name | Lat | Lon | Tilt | Profile |
|---|---|---|---|---|
| Karoo | -32.25 | 22.55 | 32.0 | solar (already ingested, 2024) |
| Upington | -28.45 | 21.26 | 28.0 | solar++ |
| Cape West Coast | -32.80 | 18.15 | 33.0 | wind |
| Port Elizabeth | -33.96 | 25.60 | 34.0 | wind |
| Free State | -28.50 | 26.80 | 29.0 | both |
| Limpopo | -23.90 | 29.45 | 24.0 | solar |

**Karoo's tilt stays exactly 32.0.** `db/schema.sql:19` -- tilt/azimuth are
fetch parameters frozen at ingest; changing one means re-fetching that site.

Years: **2016-2025** (ten most recent complete years; today is 2026-09-18).

## Approach

**One seam: the fetcher is injected.** `run_backfill(cur, sites, years,
fetch=fetch_csv)`. Tests pass a stub returning canned CSV, so resume logic,
idempotency and the `rows` fix are all provable without the network. Exactly
one real run happens, by hand, at CP3.

**Job planning is a pure DB read, separate from fetching:**

```python
plan_jobs(cur, sites, years) -> list[tuple[SiteSpec, int]]
    # every (site, year) whose ingest_job row is not status='done'
    # joins ingest_job -> site on s.name, so a site that has never been
    # fetched (no row in `site` yet) simply contributes no done-pairs
```

A job left `running` (process killed) or `error` is **not** done, so it
re-plans and re-runs. Re-running is safe because the existing COPY-to-stage
+ `ON CONFLICT (site_id, ts) DO NOTHING` already guarantees it
(`ingest/load.py:44`, proven in slice 1). Reuse it; do not reinvent it.

**Library functions never commit.** Commits live only in the CLI entry point.
This is what makes the rollback-isolated test fixture work, and it is a
constraint on the implementation, not an incidental detail.

**The `ingest_job.rows` fix:** after the insert, set `rows` from a count of
what is actually held, not `cur.rowcount`:

```sql
SELECT count(*) FROM weather_hour
WHERE site_id = %s AND local_date >= %s AND local_date < %s
```

**429 handling** in `fetch_csv`, inside the existing retry loop:

```python
if r.status_code == 429:
    time.sleep(int(r.headers.get("Retry-After", 60)))
    continue          # before raise_for_status()
```

Sequential fetches, no artificial throttle: 60 fetches is ~12/min against a
600/min ceiling, ~1,560 weighted against 5,000/hour.

## Checkpoints

- [x] **CP1 — Site registry, job planning, resume.** The seam and the logic,
      stub-tested end to end.
      files: `ingest/sites.py` (the six), `ingest/backfill.py`
      (`plan_jobs`, `backfill_one`, `run_backfill`), `tests/conftest.py`
      (+`tx` rollback fixture, +canned-CSV stub), `tests/test_backfill.py`
- [x] **CP2 — Robustness.** 429 handling and the `rows` fix.
      files: `ingest/openmeteo.py`, `ingest/backfill.py`,
      `tests/test_openmeteo.py`, `tests/test_backfill.py`
- [x] **CP3 — Run it for real.** The deliverable is a seeded database, not
      code: ~60 fetches, ~5 min, hand it to the `runner` agent. Then verify
      criteria 1-3 against the real numbers and record them.
      files: `ingest/load.py` (CLI becomes a thin wrapper over backfill),
      `db/verify.sql` (extend to the multi-site counts), `README.md`

## Tests — seams and what each pins

Seam is `plan_jobs` / `run_backfill` with a stubbed fetcher, in a rolled-back
transaction. Never the network, never internals.

| Test | Pins |
|---|---|
| `test_plan_jobs_all_pending_when_empty` | 6 sites x 10 years -> 60 jobs |
| `test_plan_jobs_skips_done` | a `done` row drops exactly that pair |
| `test_plan_jobs_retries_running_and_error` | neither status counts as done |
| `test_backfill_inserts_rows` | stub CSV lands in `weather_hour` |
| `test_backfill_is_idempotent` | second run inserts 0, count unchanged |
| `test_backfill_rows_is_held_not_inserted` | re-run leaves `rows` at N, not 0 |
| `test_backfill_resumes_after_interruption` | a `running` job re-plans and completes |
| `test_fetch_retries_on_429` | honours `Retry-After`, does not raise |

## Risks

1. **CP3 is the only place criteria 1-3 are proven, and it burns real quota.**
   A mistake means re-fetching 60 site-years. Mitigation: dry-run `plan_jobs`
   first and assert it returns exactly 60 before any fetch runs.
2. **Rollback isolation is load-bearing and fragile.** One stray `conn.commit()`
   inside a library function silently pollutes the dev database that holds
   Karoo 2024. The "no commits below the CLI" rule is the mitigation; a code
   review of every `commit()` call site is the check.
3. **The 526,032 figure assumes every site-year returns a full calendar year.**
   South Africa has no DST, so 24h/day always holds. If a year comes back
   short, investigate the gap rather than loosening the assertion -- a silent
   shortfall is exactly the kind of thing this project exists to not do.

## Carried forward (no tracker; do not lose)

- [ ] **ADR 0001's blocker is now cheap to fix.** Six sites, one timezone means
      slice 4's cagg can bucket on a constant literal:
      `time_bucket('1 day', ts, timezone => 'Africa/Johannesburg')`. Record the
      resolution in slice 4 rather than editing the immutable ADR.
- [ ] PLAN.md:216 "Which 6 sites?" and "Which published turbine curve?" are
      both now answered -- PLAN's "Still open" section is stale.
