# Model coefficients — sourced

Research pass for PLAN.md § "The models". Covers: wind turbine power curve, NOCT and
its formula's provenance, temperature coefficient gamma, air-density constants, and
the surface-pressure-at-hub-height assumption. Every number below carries a source and
a confidence rating; where a "coefficient" is actually a convention, that's said plainly.

---

## 1. Wind turbine power curve — a choice, not a verdict

Three options, ranked by fit for a ~100 m hub-height, legally-reproducible use case.
All three are IEC-class geared, pitch-regulated turbines with openly published,
tabulated data.

### Option A (recommended): IEA 3.4 MW / 130 RWT — IEA Wind Task 37 reference turbine

- **Rated power:** 3,370 kW electrical (3.6 MW rated aerodynamic)
- **Rotor diameter:** 130 m
- **Hub height:** 110 m — closest match to `wind_speed_100m` of the three options
- **IEC class:** IIIA (low-wind-speed class — the most broadly applicable class, good
  default for arbitrary sites)
- **Cut-in / rated / cut-out:** 4 m/s / 9.8 m/s / 25 m/s
- **Source:** Bortolotti, P., Canet Tarrés, H., Dykes, K., Merz, K., Sethuraman, L.,
  Verelst, D., Zahle, F. (2019). *IEA Wind TCP Task 37: Systems Engineering in Wind
  Energy — WP2.1 Reference Wind Turbines.* NREL Technical Report NREL/TP-5000-73492.
  https://www.nrel.gov/docs/fy19osti/73492.pdf (primary source — peer-reviewed-adjacent
  national-lab technical report)
- **Data / reproduction:** Full model repository, GitHub
  `IEAWindSystems/IEA-3.4-130-RWT` (https://github.com/IEAWindSystems/IEA-3.4-130-RWT),
  and a curated, tabulated power-curve archive entry at
  `nrel.github.io/turbine-models` / `natlabrockies.github.io/turbine-models`
  (`IEA_3.4MW_130_RWT.html`) — that archive (`NREL/turbine-models`, mirrored as
  `NatLabRockies/turbine-models`) is **BSD-3-Clause licensed**, i.e. explicitly cleared
  for reuse with attribution. This is the cleanest licensing story of the three options.
  I could not pull the exact wind-speed→kW row values through this session's tools (the
  CSV endpoints 404'd), but the precise pointer is: the CSV under `Onshore/` in that
  repo, and Table in NREL/TP-5000-73492 §3 (power/thrust curve). Transcribe from there
  directly rather than a secondary plot.
- **Trade-off:** it's a *reference* turbine, not a specific deployed commercial model —
  purpose-built by IEA Wind for exactly this kind of research/engineering use, so it's
  representative of a modern onshore machine rather than any one manufacturer's product.
  Best pick if "one published, unambiguously-reusable curve for all sites" matters more
  than "a turbine you could point to on a map."

### Option B: NREL 5 MW Reference Wind Turbine (Jonkman et al.)

- **Rated power:** 5 MW; **rotor diameter:** 126 m; **hub height:** 90 m
- **Cut-in / rated / cut-out:** 3 m/s / 11.4 m/s / 25 m/s
- **Source:** Jonkman, J., Butterfield, S., Musial, W., Scott, G. (2009). *Definition
  of a 5-MW Reference Wind Turbine for Offshore System Development.* NREL/TP-500-38060.
  https://www.nrel.gov/docs/fy09osti/38060.pdf — the field's most-cited reference
  turbine, full power-curve table in the report appendix.
- **Trade-off:** designed for **offshore** system development (hub height 90 m reflects
  that), so it's a weaker match for a 100 m-hub-height onshore variable than Option A,
  even though it's the most widely recognised reference turbine in the literature.
  Also archived in the same BSD-3-Clause `turbine-models` repo (`NREL_5MW_126_RWT`).

### Option C: a real deployed commercial turbine, e.g. Vestas V90-1.8/2.0 MW

- **Rated power:** 2.0 MW; **rotor diameter:** 90 m; **hub heights offered:** 80–125 m
  (so a 100 m-class hub is a real, sold configuration)
- **Cut-in / cut-out:** ~4 m/s / 25 m/s; power curve peaks ≈2,007.7 kW around 13.5 m/s
  per commonly-circulated datasheet figures
- **Sources found:** GlobalSpec datasheet mirror
  (https://datasheets.globalspec.com/ds/2797/VestasWindSystems/6CE15B5C-1187-4201-A1A3-B6E04594CE89),
  a university course PDF reproducing "TECHNICAL DATA FOR V90-1.8/2.0 MW"
  (https://catedras.facet.unt.edu.ar/centraleselectricas/wp-content/uploads/sites/19/2016/11/Anexo2_TP8.pdf),
  and the secondary compilation site wind-turbine-models.com. **None of these is Vestas
  itself publishing an open-licensed dataset** — they are third-party mirrors/course
  reproductions of a manufacturer datasheet whose original license terms are unstated.
- **Trade-off / risk:** most recognisable/"real" of the three, but the **licensing
  status of reproducing the exact curve is unclear** — Vestas has not (that I found)
  published the curve under an open license, and none of the mirrors state one. If you
  want a "you could look this turbine up and see it deployed" story, this is it, but
  flag the reproduction as resting on secondary mirrors, not a manufacturer-cleared
  open dataset.

**My read:** Option A (IEA 3.4 MW/130 RWT) is the strongest default — best hub-height
match, explicitly open (BSD-3-Clause archive + open GitHub repo + open NREL technical
report), and purpose-built for this kind of reuse. This is your call, not mine.

---

## 2. NOCT — definition, test conditions, typical value, and the plan's formula

- **Definition:** the temperature reached by open-circuited cells in a module under a
  defined set of conditions: **800 W/m² irradiance, 20°C ambient air, 1 m/s wind speed
  at module height, module tilted at 45°, back side open to ambient air.**
  Historically standardized as NOCT; **IEC 61215:2016 replaced NOCT with NMOT** (nominal
  module operating temperature), a related but distinctly-defined successor metric.
  Confidence: settled for the historical NOCT definition and its supersession by NMOT;
  both facts widely and consistently stated across PVEducation
  (https://www.pveducation.org/pvcdrom/modules-and-arrays/nominal-operating-cell-temperature)
  and technical summaries. PVEducation is a teaching resource (secondary), not a
  standards body — treat the *definition* as settled (it's just restating IEC 61215's
  own terms) but for the exact clause number, cite IEC 61215 directly if it matters for
  the README.
- **Typical value for a modern crystalline-silicon module:** commonly **45°C, in a
  range of roughly 42–48°C** depending on module/backsheet — this is a datasheet
  convention, not a physical constant; every manufacturer states its own measured NOCT.
  Confidence: thin as a single number — pick per-module from a datasheet if precision
  matters, or state 45°C as "typical" with the range as the honest caveat.
- **Is `T_cell = T_air + (NOCT-20)/800 * POA` the standard estimate?** Yes, this is the
  simplified/legacy "NOCT cell temperature model" — a linearised version of the full
  model. The **full, current form used in NREL's System Advisor Model (SAM)** is:

  ```
  T_c = T_a + (E_POA/800)(T_noct,adj − 20)(1 − η_ref/τα)(9.5/(5.7 + 3.8·v_w,adj))
  ```

  **Source:** Gilman, P., Dobos, A., DiOrio, N., Freeman, J., Janzou, S., Ryberg, D.
  (2018). *SAM Photovoltaic Model Technical Reference Update.* NREL/TP-6A20-67399. Cited
  via Sandia's PV Performance Modeling Collaborative:
  https://pvpmc.sandia.gov/modeling-guide/2-dc-module-iv/cell-temperature/noct-cell-temperature/
  (primary-adjacent — a national-lab modeling reference site citing the underlying NREL
  report). The plan's simplified formula is the version with the efficiency-derate and
  wind-adjustment terms dropped (implicitly: no electrical derate accounted, wind fixed
  at the 1 m/s NOCT test condition). **Provenance of the simplified form specifically**:
  I did not find a single canonical paper minting the bare `(NOCT-20)/800 * POA` form —
  it's the algebraic simplification of the SAM/PVWatts model that circulates widely in
  teaching material and implementation code (e.g. pvlib). Treat the simplified formula
  as a **convention/simplification**, not something with its own primary citation
  separate from the full Sandia/SAM model above.
- **Accuracy/limitations:** the full IEC/NOCT-class model is commonly cited as accurate
  to roughly **±2–3°C** for typical roof-mount arrays in still air (secondary summary,
  not independently verified against a primary error-analysis source in this pass —
  confidence thin on the exact number). The simplified formula the plan uses additionally
  drops the wind-speed and electrical-efficiency correction terms present in the full
  model, so expect it to be *less* accurate than that figure, particularly in
  higher-wind conditions (module cools faster than the fixed-1 m/s NOCT condition
  implies) — the plan's PLAN.md should note this is a first-order approximation, not
  the full SAM model.
- **Confidence:** settled on the definition and the full-model provenance; thin on a
  single "typical NOCT value" (it's genuinely module-specific); thin on the simplified
  formula's own accuracy number (no primary source found stating it directly).

---

## 3. Gamma — temperature coefficient of P_max for crystalline silicon

- **Typical value:** commonly stated as **−0.4%/°C**, with a **normal range of about
  −0.3%/°C to −0.5%/°C** across commercial monocrystalline/polycrystalline modules (some
  sources cite a slightly wider −0.35%/°C to −0.44%/°C band for "typical peak power loss
  with temperature rise").
- **Sources:** aggregated secondary summaries (ResearchGate figure captions compiling
  manufacturer/measured values; Sinovoltaics/solar-education explainer pages). **I did
  not find a single primary standards-body table** (e.g. an IEC 61215 clause or an
  NREL/Sandia dataset) giving "the" crystalline-silicon gamma in this pass — what's
  published is manufacturer-datasheet-specific (every module's own gamma is on its own
  datasheet, typically in the −0.35%/°C to −0.45%/°C band) rather than a single
  standardized constant.
- **Agrees/contradicts the plan's −0.004/°C (−0.4%/°C):** **agrees** — it sits centrally
  in the commonly-cited range. Confidence: this is where the plan's guess happens to be
  right, but it's still a "typical module" convention, not a physical constant — say so
  in the README rather than presenting it as measured for *your* module.
- **Confidence:** contested/thin — multiple secondary sources agree the −0.4%/°C figure
  is representative, but there is no single primary source "owning" this number the way
  a standards table would; it is fundamentally a per-datasheet quantity. Treat −0.004/°C
  as a stated assumption ("typical crystalline-Si value, per manufacturer datasheets
  generally"), not a cited constant.

---

## 4. Air constants — R_specific and standard density

- **Specific gas constant for dry air:** **287.058 J/(kg·K)** (the plan's 287.05 is a
  rounded match, correct to 3 significant figures). Derived from the universal gas
  constant R = 8.31447 J/(mol·K) divided by dry air's weighted-average molar mass,
  28.9644 g/mol.
- **Standard sea-level air density:** **1.2250 kg/m³**, defined at 101.325 kPa and
  15°C (288.15 K) — this is the **ICAO Standard Atmosphere** (also published as the
  ISO International Standard Atmosphere, ISO 2533). Confirmed consistently: Wikipedia's
  "International Standard Atmosphere" and "Density of air" summaries, both restating the
  ICAO/ISO-2533 definition (secondary summaries of a primary standard — the standard
  itself, ISO 2533:1975, is the actual primary source; I did not access ISO 2533's paywalled
  text directly in this pass).
- **Confidence:** settled — these are internationally standardized constants and every
  source found agrees to the stated precision; the only caveat is that ISO 2533 itself
  is paywalled, so this pass relied on consistent secondary restatements of it rather
  than the standard's own text.

---

## 5. Surface pressure at 100 m hub height — magnitude of the density error

- **Claim to check:** the plan states using `surface_pressure` (at ground level) instead
  of pressure at 100 m hub height makes density "~1% lower" than what's assumed.
- **Finding:** the barometric formula for the troposphere gives pressure (and, at near-
  constant temperature over 100 m, density) falling by **very close to 1% per 100 m** of
  altitude gain near sea level — commonly cited as ~1.2 kPa (≈1.2%) per 100 m at the
  surface, converging to "roughly 1%" as the standard shorthand figure.
  A first-principles check confirms the same order: using the barometric exponential
  approximation `Δp/p ≈ (g·M/(R·T))·Δz` with g = 9.80665 m/s², M = 0.0289644 kg/mol,
  R = 8.314 J/(mol·K), T = 288.15 K, Δz = 100 m gives **≈1.19%**.
- **Agrees/contradicts:** **agrees** — the plan's "~1% lower" is directionally and
  magnitude-correct (if anything marginally conservative; ~1.2% is closer to the
  first-principles number, but "~1%" is a reasonable rounding for a stated assumption).
- **Source:** general atmospheric-physics summaries (Wikipedia "Atmospheric pressure",
  tec-science barometric-formula derivation pages) plus the first-principles calculation
  above using standard constants from §4. These are secondary/derived, not a single
  authoritative table for "pressure loss per 100 m specifically" — but the physics is
  basic and uncontested, so confidence is still high.
- **Confidence:** settled on the order of magnitude (~1%, more precisely ~1.2%); this
  is a derived physical result rather than something with its own named citation, so
  state it as "confirmed by the barometric formula with ISA constants" rather than
  attributing it to a specific paper.

---

## Not found / could not verify in this pass

- **Exact tabulated wind-speed→kW rows** for the IEA 3.4 MW/130 RWT curve — the
  `turbine-models` GitHub raw CSV and the `nrel.github.io` HTML pages both returned
  404 through this session's fetch tool (likely bot-blocking or a stale mirror path,
  not a real absence of data — the repo and technical report clearly exist and are
  indexed). **Next step:** clone `github.com/IEAWindSystems/IEA-3.4-130-RWT` directly
  (or `github.com/NREL/turbine-models`) with `git`/`gh` rather than a web fetch, and
  transcribe the power-curve CSV or NREL/TP-5000-73492 §3 table by hand.
- **A single primary standards-table value for gamma** — every source found is a
  manufacturer-datasheet aggregation or teaching summary; no IEC/NREL table was located
  giving one canonical number. State −0.4%/°C as a typical-value assumption, sourced to
  the aggregated range above, not as a standards figure.
- **ISO 2533's own text** for the ICAO/ISA constants — relied on consistent secondary
  restatements (Wikipedia, engineering reference pages) rather than the paywalled
  standard itself. The values are not in dispute anywhere found, so this is a citation
  provenance gap, not a substantive uncertainty.
