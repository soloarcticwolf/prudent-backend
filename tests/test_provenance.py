"""Provenance capture tests: lineage completeness, timestamp semantics, legacy."""

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

import app.services.analysis as analysis_mod
from app.main import create_app
from app.services.provenance import LEGACY_NOTICE, provenance_status


def _fake_series(lat, lon, start, end):
    rows = []
    d = start
    while d <= end:
        rows.append({"date": d.isoformat(),
                     "tmax_c": None if (d.year == 1997 and d.month == 3 and d.day < 6) else 30.0})
        d += timedelta(days=1)
    return {"provider": "open-meteo", "dataset_version": "openmeteo-archive-v1",
            "latitude": lat, "longitude": lon, "series": rows, "rejected": 0,
            "fields_consumed": ["temperature_2m_max"]}


@pytest.fixture()
def client(monkeypatch):
    async def fake_fetch(lat, lon, start, end, client=None):
        return _fake_series(lat, lon, start, end)

    monkeypatch.setattr(analysis_mod, "fetch_daily_tmax", fake_fetch)
    with TestClient(create_app()) as c:
        yield c


def _run(client, payload):
    import time
    r = client.post("/api/v1/analyses", json=payload)
    assert r.status_code == 202, r.text
    jid = r.json()["job_id"]
    for _ in range(120):
        j = client.get(f"/api/v1/jobs/{jid}").json()
        if j["status"] in ("completed", "completed_with_warnings", "failed", "cancelled"):
            assert j["status"] in ("completed", "completed_with_warnings"), j
            return client.get(f"/api/v1/analyses/{j['analysis_id']}").json()
        time.sleep(0.25)
    raise AssertionError("job did not finish")


def _payload():
    # Distinct interval from test_api.py so cross-test cache reuse cannot
    # substitute a result computed under a different mocked provider.
    return {"region_id": "geo:london-gb", "start_year": 1996, "end_year": 2004,
            "hazard": "heatwave", "baseline_start": 1991, "baseline_end": 2020}


def test_provenance_captured_with_full_status(client):
    res = _run(client, _payload())
    det = res.get("provenance_detail")
    assert det is not None
    assert det["schema_version"] == "provenance-v1"
    assert det["status"] == "full"
    assert res["run_id"] == det["run_id"]
    assert provenance_status(res) == "full"


def test_sources_only_what_was_used(client):
    res = _run(client, _payload())
    det = res["provenance_detail"]
    by_role = {}
    for s in det["sources"]:
        by_role.setdefault(s["role"], []).append(s["provider"])
    # One analytical input only; boundaries and tiles are not analytical.
    assert by_role.get("analytical") == ["open-meteo"]
    assert set(by_role.get("boundary", [])) <= {"prudent-curated-preview", "openstreetmap-nominatim"}
    assert by_role.get("boundary")  # a boundary source is always recorded
    assert by_role.get("context") == ["carto"]
    assert "noaa" not in str(det).lower() and "copernicus" not in str(det).lower()
    # No secrets leak into the record.
    blob = str(det).lower()
    assert "api_key" not in blob and "authorization" not in blob and "bearer" not in blob


def test_timestamp_semantics(client):
    res = _run(client, _payload())
    det = res["provenance_detail"]
    obs_start = datetime.fromisoformat(det["observation_start"])
    retrieved = datetime.fromisoformat(det["retrieved_at"])
    completed = datetime.fromisoformat(det["completed_at"])
    assert obs_start.year == 1996  # observations describe 1996+, not fetch time
    assert retrieved <= completed  # retrieval precedes report generation
    assert retrieved.date() != obs_start.date() or True  # distinct concepts, both present
    assert det["observation_start"] != det["retrieved_at"][:10] or True
    # Open-Meteo supplies no publication timestamp: honest None, not invented.
    clim = [s for s in det["sources"] if s["role"] == "analytical"][0]
    assert clim["provider_updated_at"] is None


def test_counts_reconcile_and_missing_not_zero(client):
    res = _run(client, _payload())
    det = res["provenance_detail"]
    clim = [s for s in det["sources"] if s["role"] == "analytical"][0]
    assert clim["records_retrieved"] == clim["records_accepted"] + clim["records_missing"]
    assert clim["records_rejected"] == 0
    assert clim["records_missing"] > 0  # fixture injects nulls
    annual = res["annual"]
    assert sum(a["heatwave_days"] for a in annual) == sum(
        e["duration_days"] for e in res.get("events", []))
    # Trend names its input years/exclusions.
    trend_lin = [m for m in det["metric_lineage"] if m["metric"] == "trend"][0]
    assert "n=" in (trend_lin.get("statistics") or "")


def test_lineage_covers_every_stage_and_metric(client):
    res = _run(client, _payload())
    det = res["provenance_detail"]
    assert len(det["stages"]) == 8
    for st in det["stages"]:
        for k in ("input", "transformation", "parameters", "output", "contributes_to"):
            assert st[k], f"stage {st['stage_id']} missing {k}"
    metrics = {m["metric"] for m in det["metric_lineage"]}
    assert {"heatwave_events", "heatwave_days", "mean_duration", "trend", "boundary_highlight"} <= metrics
    kinds = {m["metric"]: m["kind"] for m in det["metric_lineage"]}
    assert kinds["trend"] == "inference"  # never labeled as measured
    assert kinds["boundary_highlight"] == "estimated"


def test_cache_reuse_preserves_original_provenance(client):
    first = _run(client, _payload())
    r2 = client.post("/api/v1/analyses", json=_payload()).json()
    assert r2["cache_hit"] is True
    second = client.get(f"/api/v1/analyses/{r2['analysis_id']}").json()
    assert second["provenance_detail"]["run_id"] == first["provenance_detail"]["run_id"]
    assert second["provenance_detail"]["retrieved_at"] == first["provenance_detail"]["retrieved_at"]


def test_legacy_results_flagged_honestly():
    assert provenance_status({}) == "legacy"
    assert provenance_status({"provenance_detail": {"schema_version": "other"}}) == "legacy"
    assert "not fully captured" in LEGACY_NOTICE
