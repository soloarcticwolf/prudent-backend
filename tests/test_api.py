"""API + integration tests with mocked upstream provider (deterministic)."""

import time
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

import app.services.analysis as analysis_mod
from app.main import create_app


def _fake_series(lat, lon, start, end):
    # 1995-01-01..2005-12-31 synthetic: hot Julys in later years -> detectable trend.
    rows = []
    d = start
    while d <= end:
        base = 26.0 + 6.0 * (1 if d.month in (6, 7, 8) else 0)
        drift = max(0.0, (d.year - 2000)) * (0.9 if d.month == 7 and 10 <= d.day <= 20 else 0.05)
        rows.append({"date": d.isoformat(), "tmax_c": round(base + drift, 2)})
        d += timedelta(days=1)
    return {"provider": "open-meteo", "dataset_version": "openmeteo-archive-v1",
            "latitude": lat, "longitude": lon, "series": rows}


@pytest.fixture()
def client(monkeypatch):
    async def fake_fetch(lat, lon, start, end, client=None):
        return _fake_series(lat, lon, start, end)

    monkeypatch.setattr(analysis_mod, "fetch_daily_tmax", fake_fetch)
    app = create_app()
    with TestClient(app) as c:
        yield c


def test_health_and_ready(client):
    assert client.get("/health").json()["status"] == "ok"
    assert client.get("/ready").json()["ready"] is True


def test_geo_search_disambiguates(client):
    r = client.get("/api/v1/geography/search", params={"q": "kolkata"})
    assert r.status_code == 200
    ids = {x["region_id"] for x in r.json()}
    assert "geo:kolkata-city-in" in ids and "geo:kolkata-district-in" in ids


def test_geo_boundary_provenance(client):
    r = client.get("/api/v1/geography/regions/geo:london-gb/boundary")
    assert r.status_code == 200
    body = r.json()
    assert body["geometry"]["type"] == "Polygon"
    assert "provenance" in body and body["boundary_version"]


def test_hazards_registry(client):
    body = client.get("/api/v1/climate/hazards").json()
    heat = [h for h in body["hazards"] if h["hazard_id"] == "heatwave"][0]
    assert heat["status"] == "available"
    assert any(h["status"] == "planned" for h in body["hazards"])


def _wait_job(client, job_id, tries=120):
    last = None
    for _ in range(tries):
        last = client.get(f"/api/v1/jobs/{job_id}").json()
        if last["status"] in ("completed", "completed_with_warnings", "failed", "cancelled"):
            return last
        time.sleep(0.25)
    raise AssertionError(f"job did not finish: {last}")


def test_full_analysis_flow_with_cache_reuse(client):
    payload = {"region_id": "geo:london-gb", "start_year": 1995, "end_year": 2005,
               "hazard": "heatwave", "baseline_start": 1991, "baseline_end": 2020}
    # Baseline fetch is also mocked, but baseline window 1991-2020 is synthetic too.
    r1 = client.post("/api/v1/analyses", json=payload)
    assert r1.status_code == 202, r1.text
    j1 = _wait_job(client, r1.json()["job_id"])
    assert j1["status"] in ("completed", "completed_with_warnings"), j1
    assert j1["analysis_id"]
    res = client.get(f"/api/v1/analyses/{j1['analysis_id']}").json()
    assert res["region_id"] == "geo:london-gb"
    assert len(res["annual"]) == 11
    assert "heatwave_days" in res["trends"]
    assert isinstance(res["insights"], list) and res["insights"]
    # Second identical request -> cache hit, same analysis id.
    r2 = client.post("/api/v1/analyses", json=payload)
    assert r2.json()["cache_hit"] is True
    assert r2.json()["analysis_id"] == j1["analysis_id"]


def test_validation_errors(client):
    bad = {"region_id": "geo:london-gb", "start_year": 2020, "end_year": 2010}
    assert client.post("/api/v1/analyses", json=bad).status_code == 422
    unknown = {"region_id": "geo:nowhere", "start_year": 2000, "end_year": 2010}
    assert client.post("/api/v1/analyses", json=unknown).status_code == 422
    flood = {"region_id": "geo:london-gb", "start_year": 2000, "end_year": 2010, "hazard": "flood"}
    assert client.post("/api/v1/analyses", json=flood).status_code == 422
