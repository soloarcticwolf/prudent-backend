"""Deterministic science tests — synthetic fixtures only, no network."""

from datetime import date, timedelta

from app.science.heatwave import (
    DailyObs, aggregate_annual, build_insights, calculate_thresholds,
    detect_events, ols_trend, period_change,
)


def _series(start: date, days: int, tmax: float) -> list[DailyObs]:
    return [DailyObs(day=start + timedelta(days=i), tmax_c=tmax) for i in range(days)]


def test_thresholds_basic_and_leap():
    base = []
    for y in range(1991, 2021):
        for doy in range(1, 366):
            d = date(y, 1, 1) + timedelta(days=doy - 1)
            # Seasonal curve + noise-free.
            base.append(DailyObs(day=d, tmax_c=28.0 + 4.0 * (doy / 365.0)))
    base.append(DailyObs(day=date(2000, 2, 29), tmax_c=30.0))
    th, meta = calculate_thresholds(base)
    assert meta["days_with_threshold"] > 300
    assert 1 in th
    # Feb29 maps into Feb28 window — no crash, threshold exists nearby.
    assert 59 in th or 60 in th


def test_thresholds_insufficient_baseline():
    th, meta = calculate_thresholds(_series(date(2020, 1, 1), 30, 30.0))
    assert meta["days_with_threshold"] == 0


def test_detection_min_duration_and_grouping():
    # Threshold flat 30.0 for all days.
    th = {k: 30.0 for k in range(1, 367)}
    obs = [
        DailyObs(date(2010, 6, 1), 31.0), DailyObs(date(2010, 6, 2), 32.0),
        DailyObs(date(2010, 6, 3), 33.0),  # event 1 (3 days)
        DailyObs(date(2010, 6, 4), 20.0),
        DailyObs(date(2010, 6, 5), 31.0), DailyObs(date(2010, 6, 6), 31.0),  # only 2 -> ignored
        DailyObs(date(2010, 6, 7), 20.0),
    ]
    events, info = detect_events(obs, th)
    assert len(events) == 1
    assert events[0]["duration_days"] == 3
    assert events[0]["peak_tmax_c"] == 33.0


def test_detection_missing_breaks_runs():
    th = {k: 30.0 for k in range(1, 367)}
    obs = [
        DailyObs(date(2010, 6, 1), 31.0), DailyObs(date(2010, 6, 2), 32.0),
        DailyObs(date(2010, 6, 3), None),  # missing breaks continuity
        DailyObs(date(2010, 6, 4), 33.0), DailyObs(date(2010, 6, 5), 34.0),
    ]
    events, info = detect_events(obs, th)
    assert events == []  # never bridge missing observations
    assert info["skipped_missing_days"] == 1


def test_annual_aggregation_and_completeness_flag():
    th = {k: 30.0 for k in range(1, 367)}
    obs = _series(date(2010, 1, 1), 365, 20.0)
    # Inject a 4-day event in July.
    for i in [181, 182, 183, 184]:
        obs[i] = DailyObs(day=obs[i].day, tmax_c=35.0)
    # Blank out half the year -> incomplete.
    for i in range(0, 200):
        if i not in (181, 182, 183, 184):
            obs[i] = DailyObs(day=obs[i].day, tmax_c=None)
    events, _ = detect_events(obs, th)
    annual = aggregate_annual(obs, events)
    assert len(annual) == 1
    assert annual[0]["incomplete"] is True
    assert annual[0]["event_count"] == 1


def test_trend_and_percent_edges():
    t = ols_trend([2010, 2011], [1.0, 2.0])
    assert t["supported"] is False  # <3 years
    t2 = ols_trend([2010, 2011, 2012, 2013], [2.0, 2.0, 2.0, 2.0])
    assert t2["supported"] and t2["slope_per_year"] == 0.0
    pc = period_change([0.0, 0.0], [1.0, 2.0])
    assert pc["supported"] and pc["kind"] == "absolute" and pc["percent_change"] is None
    pc2 = period_change([1.0, 1.0], [2.0, 2.0])
    assert abs(pc2["percent_change"] - 100.0) < 1e-6


def test_insights_traceable_no_hardcoded_percent():
    annual = [
        {"year": y, "event_count": (y - 2009), "heatwave_days": (y - 2009) * 3,
         "incomplete": False} for y in range(2010, 2020)
    ]
    trends = {"heatwave_days": {"supported": True, "slope_per_year": 3.0,
                                "p_value": 0.001, "n_years": 10, "modeled_change": 27.0}}
    ins = build_insights({"annual": annual, "trends": trends, "start_year": 2010, "end_year": 2019})
    assert any(i["type"] == "trend" for i in ins)
    assert any(i["type"] == "peak-year" for i in ins)
    for i in ins:
        assert "text" in i and "metric" in i
