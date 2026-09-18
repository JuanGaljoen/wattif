# SLICE-2 — Generation models: PV and wind

Classification: **feature** (full spine, two checkpoints)
Status: Design frozen 2026-09-18 · not yet built
No Jira. This file is the source of truth; the branch's commits are the rest.

## Why

Slice 1 proved the spine (hypertable, ingest, `time_bucket`). Nothing yet turns
weather into generation. This slice builds the physics, with every number either
cited or openly labelled an assumption — per PLAN.md's standing rule that a model
whose constants came from nowhere looks authoritative and is quietly wrong.

## Success criteria (the bar Verify checks)

- [ ] Karoo 2024 PV capacity factor lands in a defensible range, and December > June
- [ ] **Azimuth guard**: north-facing GTI ≫ south-facing at this latitude (the 9.4× trap)
- [ ] **km/h guard**: an input of 36 km/h is treated as 10 m/s, not 36
- [ ] **Cut-out guard**: 30 m/s yields zero, not rated power
- [ ] Leap-year integrity: 8,784 hours in → 8,784 capacity factors out, no NULLs
      where all inputs are present
- [ ] Both functions run inside a real `timescaledb.continuous` cagg definition
      without error

## Approach

**The physics exists exactly once, as a generated SQL `IMMUTABLE` function.**
Python owns the constants; the DDL is built from them and applied at runtime with
`CREATE OR REPLACE`, which is idempotent — so the database can never hold a
definition that disagrees with the constants. Tests drive the functions through
the database, one query over a fixture `VALUES` list rather than a round-trip per
assertion.

Why not pure-Python models with SQL added at slice 4: the derate is nonlinear, so
`f(avg(x)) ≠ avg(f(x))` (PLAN.md:138) — the expression must evaluate per hour
*inside* the aggregate. Two implementations would drift. Verified today that a
user-defined `IMMUTABLE` function is accepted inside a cagg on TimescaleDB 2.17.2.

**The turbine curve must be embedded in the function body**, not held in a lookup
table: Postgres forbids an `IMMUTABLE` function from querying tables, and a cagg
demands immutability. So `models/ddl.py` emits the 50 curve points as two constant
arrays inside the function, and interpolates with `unnest(...) WHERE sp <= v` /
`sp >= v`. Verified working, including the cut-out cliff.

**Interface is one function** — `apply_models(cur)`. Everything else is private.

### Contracts

```
pv_capacity_factor(gti real, t_air real) -> real          -- dimensionless 0..~1
wind_capacity_factor(wind_kmh real, pressure_hpa real, t_air real) -> real

PV:    T_cell = t_air + (NOCT - 20)/800 * gti
       cf     = (gti/1000) * (1 + GAMMA * (T_cell - 25))
WIND:  v      = wind_kmh / 3.6                            -- km/h, NOT m/s
       rho    = (pressure_hpa * 100) / (R_SPECIFIC * (t_air + 273.15))
       cf     = interp(curve, v) * (rho / RHO_STANDARD) / RATED_KW
       zero outside [3.0, 25.0] m/s
```

### Constants — verified vs assumed

Full citations in `docs/research/2026-09-18-model-coefficients.md`.

| Constant | Value | Status |
|---|---|---|
| `R_SPECIFIC` | 287.058 J/(kg·K) | **verified** — ICAO/ISO 2533 |
| `RHO_STANDARD` | 1.2250 kg/m³ | **verified** — ICAO/ISO 2533 |
| `RATED_KW` | 3370 | **verified** — NREL/TP-5000-73492 |
| curve, cut-in/out | 50 points, 3–25 m/s | **verified** — NREL `turbine-models`, BSD-3 |
| `NOCT` | 45.0 °C | **assumed** — typical c-Si; per-module in reality |
| `GAMMA` | −0.004 /°C | **assumed** — datasheet convention, no primary table |

Two assumptions to state in the README, not to fix here:

- The formula `T_air + (NOCT−20)/800·POA` is NREL's full SAM model with the
  wind-speed and efficiency terms dropped — so worse than its ±2–3 °C, especially
  when windy.
- `surface_pressure` is used at 100 m hub height; true density there is ~1.19% lower.
- The published curve carries power at 3 m/s despite a nominal 4 m/s cut-in. We use
  the curve verbatim rather than substituting our judgement for NREL's.
- Density correction scales *power* by `rho/rho0` (PLAN.md:110). IEC 61400-12-1
  instead normalises *wind speed* by `(rho/rho0)^(1/3)` for pitch-regulated
  turbines. Not researched; follow-up, not a blocker.

## Checkpoints

- [x] **CP1 — PV end-to-end.** All the new plumbing, simplest physics.
      files: `models/__init__.py` (`apply_models`), `models/constants.py`,
      `models/ddl.py`, `tests/conftest.py` (session fixture applies DDL),
      `tests/test_pv.py`, `requirements.txt` (+pytest)
- [x] **CP2 — Wind.** Adds only curve loading and interpolation.
      files: `data/IEA_Reference_3.4MW_130.csv` (vendored + BSD-3 notice),
      `data/LICENSE-NREL`, `models/curve.py`, `models/ddl.py`, `tests/test_wind.py`
- [ ] **CP3 — Fold in slice 1's leftovers.** `CLAUDE.md` (untracked since slice 1),
      `docs/research/` note, README verified-vs-assumed table.

## Tests — seams and what each pins

Seam is the SQL function, called through psycopg. Never the DDL string.

| Test | Pins |
|---|---|
| `test_pv_derate_reduces_output` | hot cell < cold cell at identical GTI |
| `test_pv_zero_at_night` | GTI 0 → cf 0 |
| `test_azimuth_trap` | north-facing ≫ south-facing GTI for Karoo (the 9.4×) |
| `test_wind_kmh_not_ms` | 36 km/h → the curve's 10 m/s power, not its 36 m/s |
| `test_wind_cutout_cliff` | 25.0 m/s → rated; 25.1 and 30 → 0 |
| `test_wind_density_direction` | thinner air (lower hPa) → less power |
| `test_cagg_accepts_functions` | both functions inside a real cagg definition |
| `test_karoo_year_integrity` | 8,784 rows → 8,784 non-NULL cf, Dec mean > Jun mean |

## Risks

1. **The assumed constants are the weakest part of the model**, and they are the
   two that most affect PV output. Mitigation is honesty in the README, not
   precision we do not have.
2. **`float4` rounding** through `real` columns and `real` returns may make exact
   equality assertions flaky — use tolerances, and pin the tolerance explicitly.
3. **The embedded curve makes the DDL large** (~50 array entries). If it becomes
   unreadable, that is a signal to generate it from `models/curve.py` at apply
   time rather than to hand-maintain it — which is already the plan.

## Carried forward (no tracker; do not lose)

- [ ] **PLAN.md:128 is invalid** — "the daily cagg groups on `local_date`" is
      rejected by TimescaleDB: a cagg must bucket on the hypertable's time column,
      and a per-site `timezone =>` argument fails as non-immutable (a constant
      literal works). **Blocks slice 4.** Needs a decision: per-timezone caggs, a
      shifted time dimension, or accept UTC days.
- [ ] **`ingest_job.rows` records rows inserted *this run***, not rows held for the
      site-year, so a safe re-run overwrites 8,784 with 0. Fix in slice 3.
- [ ] README verified-vs-assumed table, now that `gamma` has failed the citation test.
