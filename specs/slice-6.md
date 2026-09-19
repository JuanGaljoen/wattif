# SLICE-6 — Package it

Classification: **feature** (three checkpoints)
Status: Design frozen 2026-09-19 · built and verified 2026-09-19
No Jira. This file is the source of truth; the branch's commits are the rest.

## Why

PLAN.md's build order item 6. Everything the product does is built; what is
missing is that **a clean clone can't see any of it**. `docker compose up`
installs the schema, the model functions and both aggregates — and renders a
correct, entirely empty map, because no code can conjure ten years of weather.
The gap is named in CLAUDE.md (Known gaps) and in the README itself
("wait for slice 6's seed dump").

Budget is zero and nothing is hosted (PLAN § Hosting), so `docker compose up`
*is* the deliverable. It has to actually deliver.

## What is already written, and not proven

`ingest/seed.py`, `data/seed/` and an `api/__init__.py` startup hook exist
uncommitted. They are the right shape, but the container path is broken:
`api/__init__.py` imports `ingest.seed` while the Dockerfile explicitly does
not copy `ingest/`. The running container is a stale image, so nothing has
caught it. Slice 6 finishes and proves that work rather than restarting it.

## Design decision, deviating from PLAN

**The corpus ships in the repo, not in a GitHub Release.** PLAN estimated
~30 MB and kept it out on that basis; the measured gzip is **8.5 MB**, which
is ordinary for a repo. In-repo means `git clone && docker compose up` needs
no download, no network and no release tooling — which is the whole claim.
`fetch_seed(url)` stays, so moving it to a Release later is a flag, not a
rewrite. → ADR 0011.

**CSV, not `pg_dump`.** A CSV restores with COPY against any TimescaleDB
version and is legible to anyone who opens it. Only `site` and `weather_hour`
are dumped; both caggs are derived and rebuild in ~45 s. Dumping a cagg needs
`timescaledb_pre_restore()/post_restore()`, which couples the file to an
extension version — real fragility bought to save a minute of compute.

## Success criteria (the bar Verify checks)

- [x] `docker compose down -v && docker compose up` on a **clean volume**
      ends with a populated map — sites, daily series and reliability all
      answering, no manual step, no Open-Meteo calls
- [x] The restore is **idempotent**: a second startup against a populated
      database inserts 0 and refreshes nothing it shouldn't
- [x] Restored row counts match the live database exactly:
      **6 sites, 526,032 weather rows, 21,918 `daily_cf`, 526,032 `hourly_cf`**
- [x] The seeded database is **compressed** — 122/122 chunks, 74 MB → 23 MB —
      rather than waiting up to 12 h for the policy
      *(added at Verify: three compression tests went red on the clean volume,
      and the README advertises the compressed figure on its front page)*
- [x] Site **ids are preserved** (1, 75, 85, 95, 105, 115) and the sequence is
      bumped past them, so the next real insert doesn't collide
- [x] The whole cold start takes **under ~2 minutes** — measured **90 s**
- [x] All 65 existing tests stay green, plus 8 new ones covering the seed (**73 passed**)
- [x] README no longer says "a fresh clone shows an empty map"

## Checkpoints

### CP1 — the container can actually seed itself
Dockerfile copies `ingest/` and `data/seed/`; the comment that says "ingest/
stays out: this image only reads the database" is now false and is corrected.
Add a `.dockerignore` so `.venv/`, `__pycache__/` and `.git/` stay out of the
build context. Prove it with a clean-volume cold start.

### CP2 — the seed is pinned by tests
Following [ADR 0003](../docs/adr/0003-test-isolation-uses-synthetic-fixtures.md),
the write-tests use **synthetic** fixtures (`__test_only__` sites and a tiny
generated CSV pair), never the real corpus — `site` and `weather_hour` are
both seeded with real data and cannot be assumed empty. Tests: `is_empty`
reads the database correctly; `restore` inserts then inserts 0; ids and
sequence survive; a missing seed file raises rather than silently no-ops.

### CP3 — the README stands in for a live URL
Status → slice 6 of 6. The empty-map warning is replaced by the one-command
story. A section on the seed corpus: what's in it, how big, that it's
CC BY 4.0 with a modification notice, and how to re-dump it. PLAN's build
order item 6 marked DONE; CLAUDE.md's "clean clone has no data" gap ticked;
ADR 0011 written.

## Out of scope, and why

- **The GIF and stills.** PLAN lists them under item 6, but recording a
  screen capture is not something this session can do. Everything else in
  item 6 lands; the README is left with the one hole named explicitly.
- **Splitting `web/app.js`.** A separate known gap, unrelated to packaging.

## Found at Verify

**A clean clone was uncompressed for up to 12 hours.** `compress_all` is
deliberately not part of `apply_timescale` — it moves data, like
`refresh_daily_cf` — so nothing on the startup path ever called it. The live
database was compressed only because someone ran it by hand during slice 4.

The cold start exposed it twice over: `pg_size_pretty(hypertable_size(...))`
read **74 MB**, which is the number the README promises to have reduced, and
`tests/test_compression.py` failed three times with "need compressed rows to
make this meaningful" — a clone following the README's own `pytest` line gets
three red tests.

Compressing on the seed path costs **1.3 s**. The startup hook now does it
after materialising the aggregates.

This is the same shape as the import-graph bug in CP1, and as
[ADR 0008](../docs/adr/0008-runtime-ddl-needs-a-production-caller.md): a
property the database had only because a human once ran a command. Recorded
in ADR 0011's consequences.
