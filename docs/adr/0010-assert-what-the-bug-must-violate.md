# 10. Assert what the bug must violate, not what it merely perturbs

Date: 2026-09-19
Status: accepted

## Context

Slice 5b's reliability tests were built around the strongest oracle this
project has: `db/reliability.sql`'s measured values, produced by a different
path — raw `weather_hour` with the model functions applied inline, rather
than the aggregates the endpoint reads. Hardcoding those numbers is not
recomputing the answer the way the code computes it, so the tests look sound.

At Verify, four mutations were run against them. Three produced correct
reds. The fourth did not.

**Removing the `AT TIME ZONE` cast from the year groupings changed every
per-year figure and no test failed.** The values moved like this:

| | with the cast | without |
|---|---|---|
| PV daylight hours below 10%, a year | 891 | 810 |
| Wind hours below 10%, a year | 4,475 | 4,068 |

Both corrupted values sit **inside the README's published ranges**
(750–912 and 2,065–4,617). The tests asserted membership of those ranges,
which is exactly what a ~9% error does not violate.

The cause is `docs/adr/0007` in its fourth guise: extract the year from a
local-boundary `timestamptz` without converting first and Postgres uses the
server's UTC, pushing 1 January into the previous year. **Ten complete local
years become eleven partial ones**, and averaging per year over eleven
buckets instead of ten drags every figure down by roughly the same
proportion.

The interesting part is not the bug — it is that a test suite built on a
genuinely independent oracle still missed it. A range check is a test of
plausibility, and the failure mode this project keeps meeting is precisely
the *plausible* wrong number: the azimuth trap (9.4×, and 89.6 W/m² does not
look absurd), the km/h-vs-m/s trap (3.6×), the local-date trap (off by one
day). A ~9% shift is the least absurd of all of them.

What did discriminate was **the year count**. The dataset is exactly ten
complete local years (`ingest/sites.py`, `YEARS = 2016..2025`). Under the
bug it is eleven. Not approximately eleven — exactly eleven, every time, for
every site. The bug cannot occur without producing it.

## Decision

**For each silent failure mode, find an assertion the bug cannot avoid
violating, and assert that instead of — or as well as — the magnitude.**

The test of a good discriminator: *can the bug happen and this assertion
still hold?* If yes, it is a plausibility check, not a guard.

Applied here:

| Failure mode | Weak assertion | Discriminator |
|---|---|---|
| Wrong year grouping | per-year figures in a published range | **`years == 10`** |
| Wrong local-date serialisation | dates look like dates | **`days[0] == "2016-01-01"`** |
| Azimuth flipped | values are plausible | **December mean > June mean** |
| Static mount swallowing routes | the page loads | **`/` and `/api/sites` both 200** |

Each right-hand assertion is a property the bug *necessarily* breaks, and
each is cheap. Ranges and magnitudes still earn their place — they catch
gross errors and they document the expected scale — but they are the second
line, not the first.

## Consequence

- Every metric endpoint in this repo should carry at least one structural
  assertion alongside its value assertions. `years == 10` is the one for the
  annual metrics; a new metric needs its own.
- **A passing suite over an independent oracle is still not proof of
  coverage.** This oracle was as good as the project has, and the gap was
  found by mutation, not by reading the tests. Mutate the code at Verify, and
  treat a mutation that stays green as a finding rather than a relief.
- `docs/adr/0007` now has four recorded faces. If a fifth appears, the
  question to ask is not "where else do we read this column" but "what
  assertion would have made all five impossible".
