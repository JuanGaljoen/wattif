# 6. No reverse proxy until there is a domain

Date: 2026-09-19
Status: accepted — supersedes PLAN.md § Hosting on the three-container topology

## Context

PLAN.md's hosting section, written at slice 1, specifies three containers:

> ```
> timescaledb   timescale/timescaledb:2.x-pg17   + named volume
> api           FastAPI
> caddy         TLS (automatic) + serves the built frontend as static files
> ```
>
> Caddy serving the frontend's `dist/` means **no Node in production** — one
> less service and one less thing to patch.

Caddy does three things worth having: it serves static files, it reverse
proxies, and — the reason anyone reaches for it specifically — it obtains
and renews Let's Encrypt certificates automatically.

That plan was written before hosting was decided. The same section later
concludes **nothing is hosted**: `docker compose up` is the deliverable,
because a dead link on a portfolio in eight months is worse than never
claiming one.

That conclusion removes Caddy's reason to exist. Automatic TLS needs a
public domain, and there isn't one. What remains is serving static files and
proxying to a single upstream on localhost — both of which FastAPI already
does, the first in one line:

```python
app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
```

A third container whose only remaining job is a line of Python is not a
topology, it is ceremony.

The counter-argument is real and was weighed: a reviewer reading the compose
file sees a conventional production shape, and wiring one up is practice.
It lost to the project's own standing principle — the best change is the
smallest one that solves the real problem.

## Decision

**Two containers: `db` and `api`.** FastAPI serves the API and the frontend
on one origin. Caddy is added on the day there is a domain to get a
certificate for, and not before.

**What makes deferring free is not the container count — it is the
addressing.** Two rules, both cheap now and expensive to retrofit:

1. **The frontend uses same-origin relative paths.** `fetch('/api/sites')`,
   never `fetch('http://localhost:8000/api/sites')`.
2. **Configuration comes from the environment**, never a literal
   (`config.py`).

With those, putting Caddy — or anything else — in front later is a
compose-file change with **zero code change and no CORS**, because the
browser still sees one origin. Hardcode an absolute URL instead and the day
you deploy you inherit a CORS configuration, an environment-specific base
URL, and a preflight problem.

**Route order is load-bearing.** Starlette matches routes in registration
order, first match wins, so a `StaticFiles` mount at `/` swallows everything
registered after it. The API router is included **first**; the static mount
goes last. This is pinned by a test, because the symptom — a 404 on an
endpoint that plainly exists — reads like a routing typo rather than an
ordering bug.

## Consequence

- PLAN.md's three-container topology is superseded. Its reasoning about
  *what* Caddy would do, and about no Node in production, still stands.
- The frontend must never acquire an absolute API URL. If one appears, this
  ADR's central claim — that adding a proxy is free — stops being true.
- A future deployment adds Caddy to the compose file and changes no Python
  and no JavaScript.
