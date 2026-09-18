# 3. Test isolation uses synthetic fixtures, never "a real entity with no data yet"

Date: 2026-09-18
Status: accepted

## Context

Slice 3's backfill tests write real rows and must not pollute the dev
database, which already holds real, committed data (Karoo 2024, from slice
1). The `tx` fixture (`tests/conftest.py`) solves half of this: a rolled-back
transaction isolates what a test *writes*.

It does not isolate a test from what *already exists*. Twice during slice 3,
a write-test picked "a real site with no prior data" as its subject --
first Karoo (collided immediately, Karoo already had 2024 data), then
Upington after switching away from Karoo (worked at the time, then silently
broke the moment the real slice-3 backfill actually ran and gave Upington
real data too). Both times the failure mode was the same: a `UniqueViolation`
or a row-count assertion off by exactly the real dataset's size, because the
test's isolation depended on a fact about the *dev database's current
contents* rather than a fact about the *test's own inputs*.

## Decision

**A write-test's fixtures are synthetic and never collide with anything the
domain could legitimately hold** -- a site name like `"__test_only__"`, not
a pick from the real registry (`ingest/sites.py: SITES`) reasoned to be
"probably still empty." The `tx` rollback fixture then gives complete
isolation: nothing the test creates can already exist, and nothing it
creates survives past the test.

This generalises past this one backfill: any table this project seeds with
real, growing data (sites now; whatever slice 5 or 6 adds later) is not a
safe source of "a fresh row to test against," because the assumption that
made it fresh is a snapshot of today, not an invariant.

## Consequence

When writing a test that needs to create a row in a table the real app also
populates, use a name/id that is obviously synthetic and cannot be produced
by real usage -- not a currently-unused real one. If a test ever asserts
"this specific real entity has no data," treat that as a smell: the
assertion is really about isolation, and reaching for a fabricated fixture is
the fix, not shrinking the search for a still-empty real one.
