# 7. A cagg bucket is a local-midnight instant, not a date

Date: 2026-09-19
Status: accepted — the third face of
[ADR 0001](0001-cagg-cannot-group-on-local-date.md)

## Context

`daily_cf.day` comes from `time_bucket('1 day', ts, timezone => 'Africa/
Johannesburg')`. It is a **`timestamptz`**, and its value is the instant of
**local** midnight — which, for a UTC+2 site, is `22:00:00Z` on the
*previous* calendar date.

Read that column straight out and serialise it, and the series begins on
2015-12-31 rather than 2016-01-01. Every point on the chart sits one day
earlier than the day it describes.

This is the third time the same underlying fact has produced a different
bug:

1. **Slice 4, ADR 0001** — a continuous aggregate cannot group on
   `local_date` at all; it must bucket on the hypertable's time column, with
   a constant timezone.
2. **Slice 4, `db/reliability.sql`** — extracting the year from `day`
   directly converts to the server's UTC first, pushing 1 January into the
   previous year. Fixed there with `AT TIME ZONE` before `extract`.
3. **Slice 5a, the API** — serialising `day` as an instant puts the whole
   series off by one day.

Each has the same shape and the same signature: **silent, plausible, and
off by one.** Nothing errors. The row count is right. The values are right.
Only the labels are wrong, and they are wrong by an amount small enough to
look like a rounding question rather than a bug.

It is the same family as the azimuth trap — the failure mode this project
keeps meeting is not a crash, it is a believable wrong number.

## Decision

**Any read of `daily_cf.day` that means "which day" must convert before
use:**

```sql
(day AT TIME ZONE %(tz)s)::date AS day
```

and `tz` is **`SITE_TIMEZONE`, imported from the `timescale` package** —
never a retyped literal. It is the same constant the aggregate is defined
with, and a second copy is precisely how the single-timezone constraint
gets broken without anyone noticing.

The API therefore serves `days` as **calendar date strings**
(`"2016-01-01"`), not epoch seconds. Design had specified seconds because
that is uPlot's input format; a date string was chosen instead because it is
readable in devtools, greppable in a test, and unambiguous about *which*
day — which is the entire point of the thing it guards. The client converts
once at load.

**Pinned by a test**: `days[0] == "2016-01-01"`. Removing the cast turns it
red with `'2015-12-31' == '2016-01-01'`, which names the bug in its own
failure message.

## Consequence

- Hardcoding South Africa now constrains a **third** place. The rule
  hardens: a site in another timezone invalidates the cagg (ADR 0001), the
  reliability queries, **and** the API. One imported constant keeps that a
  single edit rather than a hunt.
- `local_date` on `weather_hour` remains the right column for hourly reads.
  `daily_cf.day` is a bucket instant and always needs the cast. The two are
  not interchangeable despite describing the same calendar day.
- Any future endpoint reading the cagg inherits this. The next one is 5b's
  reliability view.
