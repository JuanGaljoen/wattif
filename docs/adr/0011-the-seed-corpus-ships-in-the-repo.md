# 11. The seed corpus ships in the repo, as CSV

Date: 2026-09-19
Status: accepted

Supersedes PLAN.md § Hosting, "A seed dump in a GitHub Release".

## Context

Nothing is hosted (PLAN § Hosting, [ADR 0006](0006-no-reverse-proxy-until-there-is-a-domain.md)),
so `docker compose up` is the deliverable and the only demo. Since
[ADR 0008](0008-runtime-ddl-needs-a-production-caller.md) the structures
install themselves, which took a clean clone from a 500 to a correct and
entirely empty map. That is the whole remaining gap: nobody who clones this
sees the product without first running ~1,570 Open-Meteo calls.

PLAN's answer was a `pg_dump`, gzipped, published as a GitHub Release asset —
kept out of the repo on an estimate of **~30 MB**.

Two things about that estimate turned out to be wrong in our favour. The
measured gzip of the two tables that matter is **8.5 MB**, not 30; and the
30 MB figure assumed `pg_dump` of the whole database, aggregates included.

## Decision

**The corpus is two CSV files committed to the repo**, at `data/seed/`:
`site.csv` (6 rows) and `weather_hour.csv.gz` (526,032 rows, 8.5 MB). The API
restores them on startup when `weather_hour` is empty, then materialises both
aggregates.

**In the repo rather than a Release**, because the claim being made is
*`git clone && docker compose up`*. A Release asset adds a download, a network
dependency, a token for private repos, and release tooling to the one command
that is supposed to be the entire story. 8.5 MB is an ordinary size for a
repository; 30 MB was the only reason to put it elsewhere, and 30 MB was not
the number. `fetch_seed(url)` is kept in `ingest/seed.py` so moving the corpus
to a Release later is a flag, not a rewrite.

**CSV rather than `pg_dump`**, because a CSV restores with `COPY` against any
TimescaleDB version and is legible to anyone who opens it. A `pg_dump` of this
database is version-coupled in a way that only bites the person cloning it.

**`site` and `weather_hour` only.** `daily_cf` and `hourly_cf` are derived and
rebuild in about 45 s. A continuous aggregate *can* be dumped, but only via
`timescaledb_pre_restore()` / `post_restore()`, which pins the file to an
extension version — a real fragility bought to save a minute of compute.

**Restore is idempotent, the same way the backfill is**
(`ON CONFLICT DO NOTHING` off a temp stage), and startup guards on
`weather_hour` being empty. So it fires exactly once per volume and can never
touch a database someone has backfilled themselves.

**Site ids are preserved through the round trip** — `weather_hour.site_id`
references them, and `/api/sites/1/daily` is the kind of URL people bookmark.
Inserting explicit ids leaves the serial where it was, so the restore
`setval`s past them; without that, the next real insert collides.

## Consequence

- **A clean clone is now the demo.** `git clone && docker compose up`, no
  network beyond the image pull, no Open-Meteo quota, no manual step.
- **The corpus is redistributed data and is licensed as such.** Open-Meteo
  ERA5, CC BY 4.0, carried as a derived corpus with attribution and a
  modification notice in the README. Committing it makes that obligation
  permanent rather than incidental.
- **Re-dumping is a command, and the files are generated artefacts.**
  `python -m ingest.seed dump` after any change to `db/schema.sql`'s column
  order — the CSVs are positional, and a new column silently misaligns them.
  A test pins the column lists against `information_schema` so that failure
  is loud.
- **Re-dump deliberately: each one is permanent.** Gzip defeats git's delta
  compression, so a new dump is a full ~8.8 MB blob in history that nothing
  short of a history rewrite reclaims. That is affordable because re-dumps are
  rare by construction — a schema column-order change, a new site, more years —
  and not affordable if the file is treated as something to refresh casually.
  Git LFS was considered and rejected: it would put an install step between a
  cloner and a working app, which is the one thing this decision exists to
  avoid.
- **A restore has to reproduce the database's *properties*, not just its
  rows.** The first green cold start restored all 526,032 rows, materialised
  both aggregates — and left the hypertable at **74 MB with 0 of 122 chunks
  compressed**, because `compress_all` moves data and so was deliberately kept
  out of `apply_timescale`. Nothing else ever called it; the live database was
  compressed only because a human ran it once during slice 4. The compression
  policy would have caught up within 12 hours, during which a clone shows 74 MB
  against a README advertising 23, and `tests/test_compression.py` fails three
  times for want of a compressed chunk to assert against. The seed path now
  compresses, which costs 1.3 s.
- **[ADR 0008](0008-runtime-ddl-needs-a-production-caller.md)'s last lesson
  repeated itself, in the same file.** That ADR closes with "the image's
  import graph is not the test suite's import graph". Slice 6 added
  `from ingest.seed import ...` to `api/__init__.py` against a Dockerfile
  whose comment read *"ingest/ stays out: this image only reads the database,
  it never fills it"* — true when written, false the moment the import landed.
  Every local test passed; the image would not have booted. Writing the lesson
  down did not prevent it, because nothing executed it. **A cold start on a
  clean volume is the only check that does**, and it is now what Verify runs
  for this slice.
