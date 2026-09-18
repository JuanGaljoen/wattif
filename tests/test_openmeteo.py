"""fetch_csv resilience -- the seam is fetch_csv with httpx.get replaced by a
stubbed sequence of responses. No network, no database.
"""
from __future__ import annotations

import ingest.openmeteo as openmeteo
from ingest.openmeteo import SiteSpec, fetch_csv

SITE = SiteSpec(
    name="Test", latitude=-28.0, longitude=21.0, tilt_deg=28.0, azimuth_deg=180.0
)


class FakeResponse:
    def __init__(self, status_code, text, headers=None):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"status {self.status_code}")


def test_fetch_retries_on_429_honouring_retry_after(monkeypatch):
    calls = []
    sleeps = []

    def fake_get(url, params=None, timeout=None):
        calls.append(1)
        if len(calls) == 1:
            return FakeResponse(429, "", headers={"Retry-After": "5"})
        return FakeResponse(200, "latitude,longitude\n-28.0,21.0\n\ntime\n")

    monkeypatch.setattr(openmeteo.httpx, "get", fake_get)
    monkeypatch.setattr(openmeteo.time, "sleep", lambda s: sleeps.append(s))

    body = fetch_csv(SITE, 2020)
    assert body.startswith("latitude,")
    assert sleeps == [5]  # honoured Retry-After, no default fallback used
    assert len(calls) == 2


def test_fetch_429_does_not_consume_the_attempts_budget(monkeypatch):
    # A 429 is a pause, not a failed attempt -- it must not compete with the
    # "non-CSV body" retry budget (attempts=4 below would otherwise exhaust
    # after 3 rate-limit pauses and one real check).
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(1)
        if len(calls) <= 5:  # more 429s than the attempts budget allows
            return FakeResponse(429, "", headers={"Retry-After": "1"})
        return FakeResponse(200, "latitude,longitude\n-28.0,21.0\n\ntime\n")

    monkeypatch.setattr(openmeteo.httpx, "get", fake_get)
    monkeypatch.setattr(openmeteo.time, "sleep", lambda s: None)

    body = fetch_csv(SITE, 2020, attempts=4)
    assert body.startswith("latitude,")
    assert len(calls) == 6
