"""Regression tests for the accuracy audit (3.prompt_accuracy.txt).

Each test pins a demonstrated defect so it cannot silently return.
"""

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

import app.services.analysis as analysis_mod
from app.geo.boundaries import point_in_polygon
from app.main import create_app
from app.science.heatwave import (
    DailyObs, aggregate_annual, build_insights, ols_trend,
)


def _obs(dates, tmax=30.0):
    return [DailyObs(day=d, tmax_c=tmax) for d in dates]


def test_completeness_uses_full_expected_index():
    # 10-day period, only 5 days present (all valid). Old code reported 1.0.
    present = [date(2019, 1, 1) + timedelta(days=i) for i in range(5)]
    annual = aggregate_annual(_obs(present), [], date(2019, 1, 1), date(2019, 1, 10))
    assert len(annual) == 1
    a = annual[0]
    assert a["total_days"] == 10  # expected index, not filtered survivors
    assert a["valid_days"] == 5
    assert a["missing_days"] == 5
    assert a["completeness"] == 0.5
    assert a["incomplete"] is True


def test_fully_present_year_still_complete():
    days = [date(2020, 1, 1) + timedelta(days=i) for i in range(366)]
    annual = aggregate_annual(_obs(days), [], date(2020, 1, 1), date(2020, 12, 31))
    assert annual[0]["completeness"] == 1.0
    assert annual[0]["incomplete"] is False


def test_peak_year_ties_listed():
    annual = [
        {"year": 2008, "event_count": 2, "heatwave_days": 6, "incomplete": False},
        {"year": 2009, "event_count": 2, "heatwave_days": 7, "incomplete": False},
        {"year": 2010, "event_count": 1, "heatwave_days": 3, "incomplete": False},
    ]
    ins = build_insights({"annual": annual, "trends": {}, "start_year": 2008, "end_year": 2010})
    peak = [i for i in ins if i["type"] == "peak-year"][0]
    assert "2008" in peak["text"] and "2009" in peak["text"]
    assert peak["detail"]["years"] == [2008, 2009]


def test_trend_insight_keeps_nonsignificance_caveat():
    annual = [{"year": y, "event_count": (y % 3), "heatwave_days": float((y % 3) * 3),
               "incomplete": False} for y in range(2000, 2020)]
    years = [a["year"] for a in annual]
    trends = {"heatwave_days": ols_trend(years, [a["heatwave_days"] for a in annual])}
    assert trends["heatwave_days"]["supported"]
    assert trends["heatwave_days"]["p_value"] >= 0.05
    ins = build_insights({"annual": annual, "trends": trends, "start_year": 2000, "end_year": 2019})
    trend = [i for i in ins if i["type"] == "trend"][0]
    assert "not statistically significant" in trend["text"]


def test_point_in_polygon():
    square = {"type": "Polygon", "coordinates": [[[0, 0], [4, 0], [4, 4], [0, 4], [0, 0]]]}
    assert point_in_polygon(2, 2, square) is True
    assert point_in_polygon(5, 5, square) is False
    assert point_in_polygon(0, 0, square) in (True, False)  # boundary: no crash
    donut = {"type": "Polygon", "coordinates": [
        [[0, 0], [6, 0], [6, 6], [0, 6], [0, 0]],
        [[2, 2], [4, 2], [4, 4], [2, 4], [2, 2]],
    ]}
    assert point_in_polygon(1, 1, donut) is True
    assert point_in_polygon(3, 3, donut) is False  # hole
    multi = {"type": "MultiPolygon", "coordinates": [square["coordinates"],
             [[[10, 10], [12, 10], [12, 12], [10, 12], [10, 10]]]]}
    assert point_in_polygon(11, 11, multi) is True
    assert point_in_polygon(7, 7, multi) is False


def _fake_series(lat, lon, start, end):
    rows = []
    d = start
    while d <= end:
        rows.append({"date": d.isoformat(), "tmax_c": 30.0})
        d += timedelta(days=1)
    return {"provider": "open-meteo", "dataset_version": "openmeteo-archive-v1",
            "latitude": lat, "longitude": lon, "series": rows, "rejected": 0,
            "fields_consumed": ["temperature_2m_max"]}


@pytest.fixture()
def client(monkeypatch):
    calls = []

    async def fake_fetch(lat, lon, start, end, client=None):
        calls.append((round(lat, 4), round(lon, 4), start.isoformat()))
        return _fake_series(lat, lon, start, end)

    monkeypatch.setattr(analysis_mod, "fetch_daily_tmax", fake_fetch)
    with TestClient(create_app()) as c:
        yield c, calls


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


def test_v2_baseline_and_accounting(client):
    c, calls = client
    payload = {"region_id": "geo:paris-fr", "start_year": 1990, "end_year": 1994,
               "hazard": "heatwave", "baseline_start": 1991, "baseline_end": 2020}
    res = _run(c, payload)
    assert res["methodology_version"] == "heatwave-v2"
    base_calls = [x for x in calls if x[2].startswith("1991-01-01")]
    anal_calls = [x for x in calls if x[2].startswith("1990-01-01")]
    # Baseline requested per sampled point — same spatial sampling as analysis.
    assert len(base_calls) == len(anal_calls) == 5
    det = res["provenance_detail"]
    clim = [s for s in det["sources"] if s["role"] == "analytical"][0]
    assert clim["baseline"]["accepted"] > 0
    assert clim["baseline"]["sampling"].startswith("same points")
    assert clim["baseline"]["fingerprint"]
    # Containment + sampling documented per point.
    assert det["spatial"]["n_points"] == 5
    assert all("inside_stored_boundary" in p for p in det["spatial"]["points"])
    assert det["spatial"]["sampling_note"]
    # Full-index completeness present.
    assert all("missing_days" in a for a in res["annual"])
