# Frontend dependencies — vendored, no-build research

Date: 2026-09-19
Scope: CARTO basemap tiles, Leaflet, uPlot, FastAPI StaticFiles mounting — for a
no-build, vendored-ES-modules frontend served by FastAPI `StaticFiles`.

---

## 1. CARTO basemaps ("Dark Matter") — load-bearing finding

**Verdict: key-less use is NOT permitted today.** CARTO now requires a free
API key on every request to `basemaps.cartocdn.com`, including the raster
Dark Matter tiles. Anonymous requests are served but watermarked
"API KEY REQUIRED". This is a change from CARTO's historical (pre-2024/2025)
key-less raster tile service that a lot of still-circulating blog posts and
Leaflet tutorials assume.

- **Raster tile URL template (current, with key):**
  `https://basemaps.cartocdn.com/rastertiles/dark_all/{z}/{x}/{y}.png?key=YOUR_KEY`
  — confirmed on the CARTO docs FAQ page.
  Source: [CARTO Basemaps FAQ](https://docs.carto.com/faqs/carto-basemaps)

- **API key requirement — quoted from CARTO's Basemap Terms:**
  > "Customer must always use the Basemap Services with Customer's own unique API keys."

  > "CARTO may apply a visible watermark or other identifying mark to basemap
  > tiles served in response to requests that do not include a valid
  > CARTO-issued API key."

  Source: [CARTO Basemap Terms](https://carto.com/legal/basemap-terms)

  The docs FAQ softens this in practice: getting a key is free, requires no
  CARTO account, and "takes a minute" — via `carto.com/basemaps/apikey`.
  Source: [CARTO Basemaps FAQ](https://docs.carto.com/faqs/carto-basemaps)

- **Attribution — exact string** (from CARTO's own basemap-styles repo,
  the reference implementation for Leaflet/raster usage):
  ```
  &copy; <a href="http://www.openstreetmap.org/copyright">OpenStreetMap</a>, &copy; <a href="https://carto.com/attributions">CARTO</a>
  ```
  Source: [CartoDB/basemap-styles](https://github.com/CartoDB/basemap-styles)

  The Basemap Terms require both credits generically ("Customer must provide
  attribution to both OpenStreetMap and CARTO", "must be prominent and
  conspicuous") but do not themselves spell out the exact HTML string — that
  lives in the repo's example code, not the legal terms page. Treat the repo
  string as the de facto standard, the terms page as the binding requirement
  that it be present and prominent.
  Source: [CARTO Basemap Terms](https://carto.com/legal/basemap-terms)

- **Usage limits — quoted:**
  > "Five million (5,000,000) tile requests each calendar month, aggregated
  > across all of Customer's API keys."

  > "CARTO may apply automated rate-limiting, suspension or blocking in
  > respect of usage above an applicable limit, at any time and without
  > prior notice."

  Source: [CARTO Basemap Terms](https://carto.com/legal/basemap-terms)

- **Non-commercial restriction: none found.** Display to end users of your
  own site/app is explicitly a "permitted use". What's prohibited is
  reselling/redistributing the tiles themselves, or substituting CARTO's
  service for a third party's own access. This applies to commercial and
  non-commercial use alike — there is no free-for-noncommercial-only clause.
  Source: [CARTO Basemap Terms](https://carto.com/legal/basemap-terms)

- **A wrinkle worth flagging (not fully primary-sourced):** search results
  (CARTO's own blog, third-party trackers) reference that "CARTO is
  considering stopping data updates to the raster basemaps" going forward,
  in favour of the vector/GL styles. This is secondary-sourced (blog +
  aggregator commentary, not the terms page) — flagged as low-confidence
  color, not a citable fact for the app.

### 1a. Free, key-less, dark, data-friendly alternative

Since CARTO now requires a key, and the project is zero-budget/localhost
with no interest in registering third-party accounts up front, the
practical alternative is:

- **Best key-less, dark, data-friendly option:** none of the major
  general-purpose raster tile providers (CARTO, Mapbox, Stadia, Thunderforest)
  currently offer unauthenticated dark raster tiles at production quality —
  this was **not settled** by this search from primary sources. What CARTO's
  own FAQ describes as the practical path *is* the free key (see above): "it
  takes a minute, and you do not need a CARTO account" — i.e., CARTO's own
  answer to "how do I get key-less-equivalent access" is "get the free key,
  it's frictionless." Given that, the recommended path for wattif is: use
  the CARTO Dark Matter raster tiles **with** a free API key (well within the
  5M/month fair-use limit for a portfolio app), not a different provider.
  This is a judgement call for the main session, not this note, to confirm.

---

## 2. Leaflet

- **Current stable version: 1.9.4**, released 2023-05-18 (per the official
  download page; no newer stable has shipped as of this search).
  Source: [Leaflet Download](https://leafletjs.com/download.html)

- **Vendored filenames** (from the official download package):
  - `leaflet.js` — minified JS (production)
  - `leaflet-src.js` — unminified JS source
  - `leaflet.css` — stylesheet
  - `images/` folder — marker icon PNGs referenced by the CSS
    (`marker-icon.png`, `marker-icon-2x.png`, `marker-shadow.png`, etc.)
  Source: [Leaflet Download](https://leafletjs.com/download.html)

- **License: BSD 2-Clause**, per the LICENSE file in Leaflet's own repo.
  Source: [Leaflet/Leaflet LICENSE](https://raw.githubusercontent.com/Leaflet/Leaflet/main/LICENSE)

- **Marker-icon subdirectory gotcha: still present, well-documented as an
  open-ended/long-running class of issue on Leaflet's own tracker**, not
  something fixed in 1.9.4. Leaflet auto-detects `L.Icon.Default.imagePath`
  by scanning `<script>` tags for one containing "leaflet", and derives the
  images folder relative to that. This breaks when the CSS/JS are served
  from a path Leaflet's heuristic doesn't correctly resolve (e.g. nested
  static dirs, multiple leaflet-named scripts, bundlers).
  Source (primary, project's own tracker):
  [Leaflet/Leaflet#4968 "L.Icon.Default brings a wrong image url"](https://github.com/Leaflet/Leaflet/issues/4968),
  [Leaflet/Leaflet#1657 "L.Icon.Default.imagePath Regex Problems"](https://github.com/Leaflet/Leaflet/issues/1657)

  **Official fix**: set `L.Icon.Default.imagePath` explicitly, or supply a
  custom `L.Icon` with explicit `iconUrl`/`shadowUrl` pointing at your own
  static path, rather than relying on auto-detection. This is the mitigation
  applied consistently across the linked Leaflet issues (there is no code
  fix shipped in core that eliminates the heuristic; it's a documented
  "override the option" workaround). For wattif specifically — vendoring
  `leaflet.css`/`leaflet.js`/`images/` together under one static subpath and
  setting `L.Icon.Default.imagePath = '/static/leaflet/images/'` explicitly
  sidesteps the detector entirely.

---

## 3. uPlot

- **Current stable version: 1.6.32.** GitHub Releases lists this as the
  latest tag.
  Source: [leeoniya/uPlot Releases](https://github.com/leeoniya/uPlot/releases)

- **License: MIT.** Confirmed by the repository's license metadata (MIT,
  with an additional LICENSE-ivi file for a dependency-adjacent contribution
  — the library itself is MIT).
  Source: [leeoniya/uPlot](https://github.com/leeoniya/uPlot)

- **Vendored filenames** (from the published `dist/` directory for 1.6.32):
  - `uPlot.iife.min.js` (51.1 kB) — the no-build/no-module-system build,
    correct choice for a vendored `<script>` include
  - `uPlot.iife.js` (150 kB) — unminified IIFE
  - `uPlot.esm.js` / `uPlot.cjs.js` — ES module / CommonJS builds (only
    relevant if using native `<script type="module">` imports)
  - `uPlot.min.css` (1.86 kB) — required stylesheet
  - `uPlot.d.ts` — TypeScript types (not needed for plain JS)
  Source: [unpkg — uplot@1.6.32/dist](https://app.unpkg.com/uplot@1.6.32/files/dist)

- **Minimal API for a two-series time chart — from the official docs
  (`docs/README.md`):**

  Data shape is **columnar**: index 0 is the shared x-array (unix
  timestamps, seconds), followed by one array per y-series:
  ```js
  let data = [
    [1546300800, 1546387200],   // x-values (timestamps, seconds)
    [        35,         71],   // y-values — series 1
    [        90,         15],   // y-values — series 2
  ];
  ```
  Rendered with:
  ```js
  let opts = {
    title: "My Chart",
    id: "chart1",
    width: 800,
    height: 600,
    series: [
      {},                                    // x-axis (no config needed for default time axis)
      { label: "Series A", stroke: "red" },
      { label: "Series B", stroke: "blue" },
    ],
  };
  let uplot = new uPlot(opts, data, document.body);
  ```
  Docs state: "x-values must be numbers, unique, and in ascending order";
  time-axis formatting is the default when `scales.x.time` is left at its
  default (`true`) — no separate opt-in needed for the time x-axis itself.
  Source: [uPlot docs/README.md](https://github.com/leeoniya/uPlot/blob/master/docs/README.md)

- **Drag-to-zoom: NOT built in.** Quoted directly from the repo:
  > "No built-in drag scrolling/panning due to ambiguous native zoom/selection
  > behavior. However, this can be added externally via the plugin/hooks API."
  Needs the `zoom-wheel` (or `zoom-touch`) plugin from uPlot's own
  `/demos`, wired via the hooks API — not a config flag.
  Source: [leeoniya/uPlot](https://github.com/leeoniya/uPlot)

---

## 4. FastAPI StaticFiles + API routes ordering

**Confirmed from primary docs, but split across two projects**: FastAPI's
own tutorial page shows the mechanics of `app.mount(...)`, and Starlette's
routing docs (FastAPI is built on Starlette; `Mount`/routing behaviour is
Starlette's, not re-documented separately by FastAPI) supply the ordering
rule.

- **FastAPI's own static-files page** shows only the basic mount call, no
  ordering guidance:
  ```python
  app.mount("/static", StaticFiles(directory="static"), name="static")
  ```
  and clarifies mounting semantics: "Mounting' means adding a complete
  'independent' application in a specific path, that then takes care of
  handling all the sub-paths... The OpenAPI and docs from your main
  application won't include anything from the mounted application."
  Source: [FastAPI — Static Files](https://fastapi.tiangolo.com/tutorial/static-files/)

- **The ordering rule itself comes from Starlette's routing docs**: routes
  (and mounts) are matched **in the order they are registered**, first
  match wins. Starlette's own routing page states this generally for all
  route types, illustrating it with `/users/{username}` vs `/users/me`
  (the specific one must be registered first, or it's unreachable) —
  the identical principle applies to a `Mount("/")`: if it is registered
  before `/api/...` routes, it will catch every request path (including
  `/api/...`) before FastAPI's own routers ever see them, because
  `StaticFiles(html=True)` mounted at `/` matches everything under it.
  Source: [Starlette — Routing](https://www.starlette.io/routing/)

  **Practical rule for wattif**: declare/include all `/api/...` routers on
  the `FastAPI()` app *before* calling `app.mount("/", StaticFiles(...,
  html=True), name="static")` — the SPA-catch-all mount must be the last
  thing registered, since Starlette (and FastAPI, which delegates to it)
  matches in registration order and a root mount otherwise shadows
  everything registered after it.

  Note: this specific ordering caveat is **not spelled out verbatim** on
  either the FastAPI static-files page or the Starlette routing page in the
  form "mount StaticFiles last" — it is a direct, unambiguous consequence of
  Starlette's documented "first match wins, in registration order" rule
  applied to a root-path `Mount`. Confidence: settled on the underlying
  rule (primary-sourced), the SPA-specific phrasing is this note's own
  correct application of it, not a directly quotable sentence from either
  doc page.

---

## Summary table

| Question | Answer | Confidence |
|---|---|---|
| CARTO key-less raster use | **No longer permitted** — API key required, free tier 5M req/month | Settled (CARTO's own Terms + FAQ) |
| CARTO Dark Matter URL | `https://basemaps.cartocdn.com/rastertiles/dark_all/{z}/{x}/{y}.png?key=YOUR_KEY` | Settled |
| CARTO attribution string | `&copy; OpenStreetMap contributors, &copy; CARTO` (see exact HTML above) | Settled (repo reference impl; terms page mandates it generically) |
| CARTO non-commercial restriction | None — display use is permitted for any use case | Settled |
| Key-less dark alternative | Not found from primary sources; CARTO's own answer is "get the free key" | Not found / redirected to CARTO-with-key |
| Leaflet version | 1.9.4 (2023-05-18) | Settled |
| Leaflet license | BSD 2-Clause | Settled |
| Leaflet marker-icon gotcha | Still present (heuristic-based `imagePath` detection); fix = set `L.Icon.Default.imagePath` or custom icon URLs explicitly | Settled (project's own issue tracker) |
| uPlot version | 1.6.32 | Settled |
| uPlot license | MIT | Settled |
| uPlot data shape | Columnar: `[[x...], [y1...], [y2...]]`, x = ascending unix seconds | Settled (official docs) |
| uPlot drag-to-zoom | Not built in; needs `zoom-wheel`/`zoom-touch` plugin via hooks API | Settled (official docs) |
| FastAPI/Starlette mount ordering | Register `/api` routes before mounting root `StaticFiles`; first-match-wins in registration order | Settled (Starlette routing docs), SPA-specific phrasing is this note's application of that rule, not a verbatim quote |
