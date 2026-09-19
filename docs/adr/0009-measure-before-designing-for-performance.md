# 9. Measure before designing for performance

Date: 2026-09-19
Status: accepted

## Context

PLAN.md carried this as an open question into slice 5b:

> **Hourly metrics are too slow to serve live.** The two hourly reliability
> queries take ~2m21s over 526k rows. Fine from a script, not behind an HTTP
> request. `docs/adr/0005` names the fix's shape: materialise hourly capacity
> factor the way `daily_cf` materialises daily.

That framing contains an assumption nobody had checked: that the 2m21s was
spread across the work, so materialising the hourly series would shave some
fraction off it and the rest would still need a precomputed summary.

At Design, the question looked like a choice between three storage shapes —
a cagg queried live, a 6-row metrics table, or both. Two of the three are
only necessary if live querying is too slow, and nothing on hand said
whether it was.

So it was measured first, against the real 526,032 rows, before any of the
three was chosen:

| | |
|---|---|
| Evaluating the physics for every hour | **41.6 s** |
| Worst rolling 24h, one site, over precomputed values | **126 ms** |
| Worst rolling 24h, all six sites | 983 ms |
| Hours below 10%, all six sites | 21 ms |

**Essentially all of the 2m21s was the model functions, and effectively none
of it was the windowing.** `wind_capacity_factor` unnests a 50-point power
curve on every call; at 526k calls that is the query. The window functions
over the same rows cost under a second.

Once that was on the table the design question dissolved. Precompute the
physics and a per-site metric query is ~126 ms — servable live, with no
summary table, no invalidation rules, and no second thing to keep in step
with the first. The measurement did not merely inform the choice; it
removed two of the three options.

Guessing would have picked the metrics table — it is the obvious answer to
"2m21s is too slow to serve" — and it would have been a hand-rolled cache
with its own staleness problem, built to solve a cost that was never where
it appeared to be.

## Decision

**When a performance number decides a design, measure it before freezing the
design.** Specifically:

- **Measure the parts, not the total.** "2m21s" is not actionable; "41.6 s of
  function evaluation and 126 ms of windowing" chooses the design by itself.
- **Measure against real data.** Six sites and ten years, not a sample —
  this project's whole corpus fits in a scratch table, so there is no excuse
  for extrapolating.
- **Throw the probe away.** The measurement here was an `UNLOGGED` table
  built, timed and dropped. It proved the shape without committing to it.

The same rule already applies to physical constants (every coefficient gets
a citation, README) and to external contracts (Research, `docs/research/`).
This extends it to performance: **a number that decides a design is a fact to
look up, not a quantity to reason about**, and the reasoning is at its most
convincing exactly when it is wrong.

## Consequence

- `hourly_cf` exists and the metrics are computed per request (~274–315 ms
  end to end, including three other queries). There is no summary table, and
  therefore nothing to invalidate when a coefficient changes — the cagg
  refresh already covers it.
- Adding a metric later is a new query, not a recompute-and-migrate step.
  That flexibility was bought by the measurement, not paid for separately.
- The cost is 62 MB and a second aggregate to maintain — accepted knowingly,
  with `cagg.py` generalised so the version-marker discipline is not
  duplicated per aggregate.
- **The probe is the cheap part.** It took one command and about a minute.
  Any future "is X fast enough to serve?" question in this repo should be
  answered the same way rather than argued.
