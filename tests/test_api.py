"""The API's seam is HTTP, not the Python functions behind it.

Same reasoning as slice 2 testing the physics through SQL rather than
through a Python callable (specs/slice-2.md, Design): test the public
interface, because that is what can silently change shape.

Two of these tests assert physics rather than plumbing -- the local-date
serialisation and the Dec>Jun seasonality. That is deliberate
(specs/slice-5a.md, Tests): both traps are silent, and an API is one more
place they can reappear.

Read-only against the seeded database, so no `tx` fixture and no synthetic
sites -- nothing here writes (docs/adr/0003 does not apply).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api import create_app


@pytest.fixture(scope="module")
def client():
    with TestClient(create_app()) as c:
        yield c


def test_sites_lists_the_six_seeded_sites(client):
    """The registry in ingest/sites.py, over HTTP.

    Karoo's coordinates are the independent source of truth here: they are
    the fetch parameters frozen on the site row at ingest, not something
    this endpoint computes.
    """
    r = client.get("/api/sites")
    assert r.status_code == 200

    sites = r.json()
    assert len(sites) == 6

    by_name = {s["name"]: s for s in sites}
    assert set(by_name) == {
        "Karoo",
        "Upington",
        "Cape West Coast",
        "Port Elizabeth",
        "Free State",
        "Limpopo",
    }

    karoo = by_name["Karoo"]
    assert karoo["latitude"] == pytest.approx(-32.25)
    assert karoo["longitude"] == pytest.approx(22.55)
    # 180 = NORTH. The azimuth trap, carried through to the wire.
    assert karoo["azimuth_deg"] == pytest.approx(180.0)


@pytest.fixture(scope="module")
def karoo_id(client):
    sites = client.get("/api/sites").json()
    return next(s["id"] for s in sites if s["name"] == "Karoo")


def test_daily_returns_ten_local_years_in_columnar_shape(client, karoo_id):
    """3,653 days: 2016-2025, with 2016/2020/2024 as leap years.

    Columnar because that is uPlot's data shape -- the client reshapes
    nothing (specs/slice-5a.md, Approach).
    """
    r = client.get(f"/api/sites/{karoo_id}/daily")
    assert r.status_code == 200

    body = r.json()
    assert body["site_id"] == karoo_id
    assert len(body["days"]) == 3653
    assert len(body["pv_cf"]) == len(body["days"])
    assert len(body["wind_cf"]) == len(body["days"])

    # Capacity factor is dimensionless and bounded. P_rated multiplies in at
    # read time, in the browser -- nothing here is in megawatts.
    assert all(0.0 <= v <= 1.0 for v in body["pv_cf"])
    assert all(0.0 <= v <= 1.0 for v in body["wind_cf"])


def test_days_are_local_calendar_dates_not_utc_instants(client, karoo_id):
    """The trap: daily_cf.day buckets at LOCAL midnight, i.e. 22:00Z the day
    before. Serialised without the timezone cast, the series starts on
    2015-12-31 and every point is off by one -- silently, and plausibly.

    The expected values are the ingest span (ingest/sites.py, YEARS =
    2016..2025), not something this endpoint derives.
    """
    body = client.get(f"/api/sites/{karoo_id}/daily").json()

    assert body["days"][0] == "2016-01-01"
    assert body["days"][-1] == "2025-12-31"
    assert body["timezone"] == "Africa/Johannesburg"


def test_pv_peaks_in_december_not_june(client, karoo_id):
    """The azimuth trap, through the public interface.

    Karoo is at -32.25: southern hemisphere, so midsummer is December. A
    south-facing array (azimuth 0) would inverted this curve and still look
    plausible -- 89.6 W/m2 against 841.9 (CLAUDE.md, The traps).
    """
    body = client.get(f"/api/sites/{karoo_id}/daily").json()
    pairs = list(zip(body["days"], body["pv_cf"]))

    dec = [cf for day, cf in pairs if day[5:7] == "12"]
    jun = [cf for day, cf in pairs if day[5:7] == "06"]

    assert sum(dec) / len(dec) > sum(jun) / len(jun)


def test_unknown_site_is_404(client):
    r = client.get("/api/sites/999999/daily")
    assert r.status_code == 404


def test_static_mount_does_not_swallow_the_api(client):
    """Starlette matches routes in registration order, first match wins, so
    a StaticFiles mount at "/" shadows every route registered after it.

    The failure mode is a 404 on an endpoint that plainly exists, which
    reads as a routing typo rather than an ordering bug -- so it gets a
    test (specs/slice-5a.md, Risks).
    """
    page = client.get("/")
    assert page.status_code == 200
    assert "wattif" in page.text

    assert client.get("/api/sites").status_code == 200


def test_startup_installs_the_runtime_ddl(monkeypatch):
    """The API must be self-sufficient against any schema'd database.

    db/schema.sql runs once, on an empty volume, and creates tables only.
    The model functions and daily_cf are applied at RUNTIME (models/__init__,
    timescale/__init__) -- and until this slice, nothing outside the test
    suite ever called them. A clean clone therefore had no daily_cf, and
    this endpoint would 500 rather than 404.

    Asserting "the functions exist" would be an always-green test here: they
    already exist in the dev database. So the seam under test is the startup
    wiring itself.
    """
    import api

    called = []
    monkeypatch.setattr(api, "apply_models", lambda cur: called.append("models"))
    monkeypatch.setattr(api, "apply_timescale", lambda cur: called.append("timescale"))

    with TestClient(api.create_app()):
        pass

    assert called == ["models", "timescale"]
