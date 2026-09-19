# SLICE-5A — Map + generation chart, end to end

Classification: **feature** (three checkpoints)
Status: Design frozen 2026-09-19 · not yet built
No Jira. This file is the source of truth; the branch's commits are the rest.

## Why

526,032 hourly rows and a 21,918-row daily aggregate exist, and nothing can
see them. This slice puts the data on screen: pick a site, see ten years of
what it would have generated.

PLAN.md build order item 5 was one slice — "FastAPI + frontend: map,
generation chart, reliability view". It splits in two at Understand:

- **5a (this file)** — map + generation chart, end to end.
- **5b** — the reliability view, where the ~2m21s hourly-query problem gets
  decided (PLAN.md § Still open, and `docs/adr/0005` for the fix's shape).

Vertical, not horizontal: an API designed without a consumer guesses its own
shape. 5a proves the contract with a real screen.

## Success criteria (the bar Verify checks)

- [ ] `GET /api/sites` returns the 6 sites with id, name, latitude, longitude
- [ ] `GET /api/sites/{id}/daily` returns **3,653 days** of
      `(day, pv_cf, wind_cf)`, served from `daily_cf`, warm response under
      ~200 ms
- [ ] `days[0]` is **`2016-01-01`** — the local-date serialisation is right
- [ ] `docker compose up` from a clean clone → `http://localhost:8000`
      renders the map with six markers, no manual step
- [ ] Clicking a marker draws both series
- [ ] **PV visibly peaks Dec–Feb** — the azimuth trap, now visible on screen
      rather than only asserted in a test
- [ ] Changing rated capacity rescales the chart **with no network call**
- [ ] The map renders correctly **with and without** `CARTO_KEY` set
- [ ] All 39 existing tests stay green, plus the new API tests

## Approach

### API — sync endpoints over a connection pool

FastAPI routes are `def`, **not** `async def`: Starlette runs sync endpoints
in a threadpool, so `psycopg_pool.ConnectionPool` (sync) is safe and avoids
an async driver entirely. The pool opens and closes on the FastAPI lifespan.

Two endpoints. No speculative surface — 5b adds its own.

```
GET /api/sites            -> [{id, name, latitude, longitude, elevation_m,
                               tilt_deg, azimuth_deg}]
GET /api/sites/{id}/daily -> {site_id, days: [...], pv_cf: [...],
                              wind_cf: [...]}
```

**Columnar, not row-per-object.** That is exactly uPlot's data shape
(`[xs, ys1, ys2]`), so the client reshapes nothing, and it drops 3,653
repeated key sets: ~90 KB instead of ~250 KB. Capacity factors are rounded
to 4 decimals — the models' precision does not support more, and the
hypertable is `real` (float4) anyway.

### The timezone contract — one constant, never a literal

`daily_cf.day` is a `timestamptz` whose bucket starts at **local** midnight.
Serialised naively it is `2025-12-30T22:00:00Z`, and every point on the
chart lands on the previous day. Silent, plausible, and off by one — the
same class of bug as the azimuth trap.

The query therefore casts:

```sql
(day AT TIME ZONE %(tz)s)::date AS day
```

`tz` is **`SITE_TIMEZONE` imported from the `timescale` package**, never a
retyped string. It already exists at `timescale/cagg.py` and is what makes
the continuous aggregate legal at all (`docs/adr/0001`). This slice
re-exports it from `timescale/__init__.py` so the API binds to the same
constant.

Hardcoding South Africa is deliberate and agreed: all six sites share one
timezone by design (specs/slice-3.md). But it now constrains a **third**
place, so the existing rule hardens — *a site in another timezone
invalidates the cagg, the reliability queries, and now the API*. One
constant is what keeps that a single edit rather than a hunt.

### Capacity factor stays dimensionless over the wire

The API returns exactly what the cagg holds. The page carries a **rated
capacity** input (default 100 MW) and multiplies client-side:

```
MWh/day = cf x rated_mw x 24
```

Instant on every keystroke, no refetch, and it honours PLAN.md's
"`P_rated` multiplies in at read time" rather than baking a farm size into
stored data.

### Frontend — no build step

Plain ES modules, dependencies vendored into `web/vendor/`, served by
FastAPI's `StaticFiles`. No npm, no bundler, no `node_modules`;
`docker compose up` stays the whole story. Versions and licences confirmed
in `docs/research/2026-09-19-frontend-deps.md`:

- **Leaflet 1.9.4** (BSD-2-Clause) — `leaflet.js`, `leaflet.css`, `images/`.
  Its `Icon.Default.imagePath` auto-detection breaks under a nested static
  dir; set `L.Icon.Default.imagePath` explicitly.
- **uPlot 1.6.32** (MIT) — `uPlot.iife.min.js`, `uPlot.min.css`. Data is
  columnar, x ascending **unix seconds**. Panning is not built in and is out
  of scope; drag-select zoom is uPlot's own cursor behaviour — confirm at
  Forge rather than assume, and drop it if it needs a plugin.

### Basemap — CARTO with a key, OSM-inverted without

CARTO requires a free API key on every tile request; unauthenticated tiles
come back stamped "API KEY REQUIRED"
(`docs/research/2026-09-19-frontend-deps.md`, quoting CARTO's basemap
terms). A key that a cloner has not set would make the map look broken —
the same failure mode PLAN.md's hosting section rejects for dead links.

So the page branches once:

```js
if (CARTO_KEY) {                      // dark_all tiles, already dark
  L.tileLayer(cartoUrl, {attribution: CARTO_ATTR}).addTo(map);
} else {                              // OSM tiles, inverted in CSS
  L.tileLayer(osmUrl, {attribution: OSM_ATTR}).addTo(map);
  mapEl.classList.add('invert-tiles');
}
```

The CSS filter belongs to the fallback **only** — applied to Dark Matter it
would invert an already-dark map back to light. Each provider's required
attribution string is used verbatim from the research note; CARTO needs
both OSM's and its own.

`CARTO_KEY` is not a secret: it travels in the tile URL and is readable in
devtools. It lives in the environment to avoid hardcoding, not to hide it.

### Containers — two, not three

`db` + `api`. **Caddy is dropped**, superseding PLAN.md § Hosting: its one
load-bearing job was automatic TLS, which needs a public domain that the
"nothing hosted" decision rules out. On localhost it would be a reverse
proxy in front of a single service, doing what
`app.mount("/", StaticFiles(...))` does in one line.

Deferring costs nothing **because** the frontend uses **same-origin
relative paths** (`fetch('/api/sites')`, never an absolute URL) and reads
config from the environment. Adding Caddy on the day a domain exists is then
a compose-file change with **zero** code change and no CORS.

**Route ordering is load-bearing**: Starlette matches routes in registration
order, first match wins, so a `StaticFiles` mount at `/` swallows
everything registered after it. API routers are included **before** the
static mount. Pinned by a test.

## Files

| File | Change |
|---|---|
| `config.py` | **new** — `DSN`, currently duplicated in `ingest/load.py:32` and `tests/conftest.py:16`; a third copy was about to appear |
| `api/__init__.py` | **new** — app factory, route registration, static mount (in that order) |
| `api/main.py` | **new** — the two endpoints |
| `api/db.py` | **new** — the connection pool, on lifespan |
| `timescale/__init__.py` | re-export `SITE_TIMEZONE` |
| `web/index.html` | **new** — page shell, map + chart + capacity input |
| `web/app.js` | **new** — fetch, map, chart, capacity multiply |
| `web/style.css` | **new** — layout, dark theme, the fallback tile filter |
| `web/vendor/` | **new** — Leaflet 1.9.4, uPlot 1.6.32, vendored |
| `Dockerfile` | **new** — the api image |
| `docker-compose.yml` | add the `api` service |
| `requirements.txt` | `fastapi`, `uvicorn`, `psycopg_pool` |
| `.env.example` | **new** — `CARTO_KEY`, `DATABASE_URL` |
| `ingest/load.py`, `tests/conftest.py` | import `DSN` from `config` |
| `README.md` | running it, the basemap key, attribution |

## Tests — `tests/test_api.py`

The seam is **HTTP**, via `fastapi.testclient` — not the Python functions.
Same reasoning as slice 2 testing the physics through SQL rather than
through a Python callable: test the public interface, not internals.

- [ ] `/api/sites` returns 6 sites; Karoo's lat/lon match `ingest/sites.py`
- [ ] `/api/sites/{id}/daily` returns 3,653 days; all three arrays are the
      same length; every cf is within `[0, 1]`
- [ ] **`days[0] == "2016-01-01"`** — pins the timezone serialisation
- [ ] **December mean `pv_cf` > June mean** — pins the azimuth trap through
      the public interface
- [ ] Unknown site id returns 404
- [ ] `GET /` serves the page **and** `/api/sites` still returns JSON — pins
      the mount-ordering risk

Two of these are physics assertions in an API suite, deliberately: those
traps are the project's whole thesis about being silently wrong, and an API
is one more place they can reappear.

## Risks

1. **Mount ordering** swallowing `/api/*`. Known rule, pinned by a test.
2. **Leaflet's vendored marker icons** — `Icon.Default.imagePath` breaks
   under nested static dirs. Known fix, applied up front.
3. **Chart responsiveness** — 3,653 points x 2 series, re-multiplied on
   every capacity keystroke. uPlot was chosen for this; if it stutters,
   debounce before reaching for a different library.
4. **`daily_cf` read latency** unmeasured behind HTTP. Expected trivial
   (21,918 rows, indexed by `site_id`) but the ~200 ms criterion is a real
   bar, not a formality.

## Checkpoints

- [ ] **CP1** — API + container · files: `config.py`, `api/*`,
      `timescale/__init__.py`, `Dockerfile`, `docker-compose.yml`,
      `requirements.txt`, `ingest/load.py`, `tests/conftest.py`,
      `tests/test_api.py`
- [ ] **CP2** — map · files: `web/index.html`, `web/app.js`,
      `web/style.css`, `web/vendor/*`, `.env.example`
- [ ] **CP3** — chart + capacity · files: `web/app.js`, `web/style.css`,
      `README.md`

## For Chronicle

- **Caddy dropped** — supersedes PLAN.md § Hosting. Likely an ADR: the
  reasoning (no domain → no TLS → no job) outlives this slice.
- **The local-date serialisation trap** — a third face of the same
  timezone problem as ADR 0001. Worth a CLAUDE.md line at minimum.
- **PLAN.md build order** — item 5 is now 5a and 5b.
