"""Heatwave threshold + detection + annual aggregation + trends + insights.

Methodology version: heatwave-v2
- Baseline: 1991-2020 preferred (configurable), sampled with the SAME spatial
  points and aggregation as the analysis series (v1 used the centroid only).
- Threshold: calendar-day 95th percentile over a +/-7 day window.
- Event: >=3 consecutive valid days with Tmax > threshold.
- Missing observations break runs (never bridged), never treated as zero.
- Annual completeness is measured against the full expected date index,
  not the filtered set of dates that survived aggregation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from scipy import stats

METHODOLOGY_VERSION = "heatwave-v2"
BASELINE_START_DEFAULT = 1991
BASELINE_END_DEFAULT = 2020
WINDOW_RADIUS_DAYS = 7
PERCENTILE = 95.0
MIN_BASELINE_OBS = 100
MIN_EVENT_DAYS = 3


@dataclass
class DailyObs:
    day: date
    tmax_c: Optional[float]


def _doy(d: date) -> int:
    return d.timetuple().tm_yday


def _doy_key(d: date) -> int:
    # Map Feb 29 -> Feb 28 (doy 59) for stable calendar-day grouping.
    if d.month == 2 and d.day == 29:
        return 59
    return _doy(d)


def calculate_thresholds(
    baseline_obs: List[DailyObs],
    percentile: float = PERCENTILE,
    window: int = WINDOW_RADIUS_DAYS,
    min_obs: int = MIN_BASELINE_OBS,
) -> Tuple[Dict[int, float], Dict[str, Any]]:
    """Return {doy_key: threshold} plus metadata. doy_key 1..366 (60 = Feb29-mapped 59)."""
    buckets: Dict[int, List[float]] = {}
    for o in baseline_obs:
        if o.tmax_c is None:
            continue
        buckets.setdefault(_doy_key(o.day), []).append(float(o.tmax_c))
    thresholds: Dict[int, float] = {}
    insufficient: List[int] = []
    # Widen each calendar day with neighbouring-day pooling.
    for doy in range(1, 367):
        pooled: List[float] = []
        for delta in range(-window, window + 1):
            k = doy + delta
            if k < 1 or k > 366:
                continue
            pooled.extend(buckets.get(k, []))
        if len(pooled) >= min_obs:
            thresholds[doy] = float(np.percentile(pooled, percentile, method="linear"))
        else:
            insufficient.append(doy)
    meta = {
        "percentile": percentile,
        "window_days": window,
        "min_baseline_obs": min_obs,
        "days_with_threshold": len(thresholds),
        "days_insufficient": len(insufficient),
        "methodology_version": METHODOLOGY_VERSION,
    }
    return thresholds, meta


def detect_events(
    obs: List[DailyObs],
    thresholds: Dict[int, float],
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    run: List[DailyObs] = []
    skipped_missing = 0
    days_without_threshold = 0

    def flush() -> None:
        nonlocal run
        if len(run) >= MIN_EVENT_DAYS:
            tmaxs = [float(o.tmax_c) for o in run if o.tmax_c is not None]
            thr = [thresholds[_doy_key(o.day)] for o in run]
            exceed = [t - th for t, th in zip(tmaxs, thr)]
            events.append({
                "start": run[0].day.isoformat(),
                "end": run[-1].day.isoformat(),
                "duration_days": len(run),
                "peak_tmax_c": round(max(tmaxs), 2),
                "mean_exceedance_c": round(float(sum(exceed) / len(exceed)), 2),
                "max_exceedance_c": round(max(exceed), 2),
            })
        run = []

    for o in sorted(obs, key=lambda x: x.day):
        if o.tmax_c is None:
            skipped_missing += 1
            flush()
            continue
        th = thresholds.get(_doy_key(o.day))
        if th is None:
            days_without_threshold += 1
            flush()
            continue
        if float(o.tmax_c) > th:
            run.append(o)
        else:
            flush()
    flush()
    info = {
        "event_count": len(events),
        "skipped_missing_days": skipped_missing,
        "days_without_threshold": days_without_threshold,
        "methodology_version": METHODOLOGY_VERSION,
    }
    return events, info


def aggregate_annual(
    obs: List[DailyObs],
    events: List[Dict[str, Any]],
    start: Optional[date] = None,
    end: Optional[date] = None,
) -> List[Dict[str, Any]]:
    """Annual aggregation. When start/end are given, completeness is measured
    against the full expected date index: a year is never classified complete
    merely because every date remaining in a filtered dataset has a value."""
    expected: Dict[int, int] = {}
    if start is not None and end is not None:
        d = start
        while d <= end:
            expected[d.year] = expected.get(d.year, 0) + 1
            d += timedelta(days=1)
    by_year: Dict[int, List[DailyObs]] = {}
    for o in obs:
        by_year.setdefault(o.day.year, []).append(o)
    ev_by_year: Dict[int, List[Dict[str, Any]]] = {}
    for e in events:
        y = int(str(e["start"])[:4])
        ev_by_year.setdefault(y, []).append(e)
    out: List[Dict[str, Any]] = []
    for year in sorted(set(list(by_year) + list(expected))):
        days = by_year.get(year, [])
        valid = sum(1 for d in days if d.tmax_c is not None)
        total = expected.get(year, len(days))
        completeness = round(valid / total, 4) if total else 0.0
        evs = ev_by_year.get(year, [])
        durations = [e["duration_days"] for e in evs]
        peaks = [e["peak_tmax_c"] for e in evs]
        out.append({
            "year": year,
            "event_count": len(evs),
            "heatwave_days": int(sum(durations)),
            "mean_duration_days": round(float(sum(durations) / len(durations)), 2) if durations else None,
            "max_duration_days": int(max(durations)) if durations else None,
            "peak_tmax_c": round(max(peaks), 2) if peaks else None,
            "valid_days": valid,
            "total_days": total,
            "missing_days": total - len(days),
            "completeness": completeness,
            # Incomplete years (<80% valid) are flagged; callers must not treat them as zero-event years.
            "incomplete": completeness < 0.80,
        })
    return out


def ols_trend(years: List[int], values: List[Optional[float]]) -> Dict[str, Any]:
    pairs = [(x, float(v)) for x, v in zip(years, values) if v is not None]
    if len(pairs) < 3:
        return {"supported": False, "reason": "fewer than 3 valid years"}
    xs = np.array([p[0] for p in pairs], dtype=float)
    ys = np.array([p[1] for p in pairs], dtype=float)
    if float(np.std(ys)) == 0.0:
        return {
            "supported": True, "slope_per_year": 0.0, "intercept": float(ys[0]),
            "r_value": 0.0, "p_value": 1.0, "std_err": 0.0,
            "ci95_low": 0.0, "ci95_high": 0.0,
            "n_years": len(pairs),
            "modeled_change": 0.0, "warning": "no variation in series",
        }
    try:
        res = stats.linregress(xs, ys)
    except Exception as e:  # pragma: no cover
        return {"supported": False, "reason": f"regression failed: {e}"}
    span = float(xs.max() - xs.min())
    ci = 1.96 * float(res.stderr) if res.stderr == res.stderr else None
    out: Dict[str, Any] = {
        "supported": True,
        "slope_per_year": float(res.slope),
        "intercept": float(res.intercept),
        "r_value": float(res.rvalue),
        "p_value": float(res.pvalue),
        "std_err": float(res.stderr),
        "n_years": len(pairs),
        "modeled_change": float(res.slope * span),
        "span_years": float(span),
        "method": "ordinary-least-squares",
    }
    if ci is not None:
        out["ci95_low"] = float(res.slope - ci)
        out["ci95_high"] = float(res.slope + ci)
    if len(pairs) < 8:
        out["warning"] = "short series (<8 valid years); trend is indicative only"
    return out


def period_change(first: List[float], second: List[float]) -> Dict[str, Any]:
    import statistics as _s
    if not first or not second:
        return {"supported": False, "reason": "empty comparison period"}
    m1 = float(_s.fmean(first))
    m2 = float(_s.fmean(second))
    abs_diff = m2 - m1
    if abs(m1) < 1e-9:
        return {
            "supported": True, "kind": "absolute",
            "first_mean": m1, "second_mean": m2, "absolute_change": abs_diff,
            "percent_change": None,
            "note": "baseline mean is zero; reporting absolute change only",
        }
    pct = (abs_diff / abs(m1)) * 100.0
    out: Dict[str, Any] = {
        "supported": True, "kind": "percent",
        "first_mean": m1, "second_mean": m2,
        "absolute_change": abs_diff, "percent_change": pct,
    }
    if abs(m1) < 0.5:
        out["warning"] = "small baseline; percent change is unstable, prefer absolute change"
    return out


def build_insights(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Deterministic template insights, each traceable to computed stats."""
    insights: List[Dict[str, Any]] = []
    annual = payload.get("annual", [])
    trends = payload.get("trends", {})
    start = payload.get("start_year")
    end = payload.get("end_year")
    valid = [a for a in annual if not a.get("incomplete")]
    if not valid:
        return [{"type": "coverage", "text": "Insufficient valid annual data to support findings.", "metric": "completeness"}]
    counts = [a["event_count"] for a in valid]
    days = [a["heatwave_days"] for a in valid]
    # Frequency change first-half vs second-half.
    mid = len(valid) // 2
    if mid >= 2:
        ch = period_change([float(c) for c in counts[:mid]], [float(c) for c in counts[mid:]])
        if ch.get("supported") and ch.get("kind") == "percent":
            insights.append({
                "type": "frequency-change",
                "metric": "event_count",
                "text": (
                    f"Mean annual heatwave frequency changed from {ch['first_mean']:.1f} "
                    f"to {ch['second_mean']:.1f} events/yr between the first and second halves "
                    f"of {start}–{end} ({ch['percent_change']:+.1f}%)."
                ),
                "detail": ch,
            })
        elif ch.get("supported"):
            insights.append({
                "type": "frequency-change",
                "metric": "event_count",
                "text": (
                    f"Mean annual frequency changed by {ch['absolute_change']:+.2f} events/yr "
                    f"between halves of {start}–{end} (percent change not meaningful)."
                ),
                "detail": ch,
            })
    t = trends.get("heatwave_days") or {}
    if t.get("supported"):
        sig = ""
        try:
            sig = "" if float(t.get("p_value", 1.0)) < 0.05 else " (not statistically significant at the 5% level)"
        except (TypeError, ValueError):
            sig = ""
        insights.append({
            "type": "trend",
            "metric": "heatwave_days",
            "text": (
                f"Estimated trend in annual heatwave days is {t['slope_per_year']:+.3f} days/yr "
                f"(n={t['n_years']}, p={t.get('p_value', float('nan')):.3g}), "
                f"about {t.get('modeled_change', 0):+.1f} days over the interval{sig}."
            ),
            "detail": {"slope": t["slope_per_year"], "p": t.get("p_value"), "n": t["n_years"]},
        })
    peak_count = max([a["event_count"] for a in valid] + [0])
    if peak_count > 0:
        peak_years = sorted(a["year"] for a in valid if a["event_count"] == peak_count)
        if len(peak_years) == 1:
            text = f"{peak_years[0]} had the most heatwave events ({peak_count}) in {start}–{end}."
        else:
            text = (f"{', '.join(str(y) for y in peak_years)} tied for the most heatwave events "
                    f"({peak_count}) in {start}–{end}.")
        insights.append({
            "type": "peak-year",
            "metric": "event_count",
            "text": text,
            "detail": {"years": peak_years, "count": peak_count},
        })
    incomplete = [a for a in annual if a.get("incomplete")]
    if incomplete:
        yrs = ", ".join(str(a["year"]) for a in incomplete[:6])
        insights.append({
            "type": "coverage",
            "metric": "completeness",
            "text": f"{len(incomplete)} year(s) excluded from trend interpretation due to missing observations (e.g. {yrs}).",
            "detail": {"years": [a["year"] for a in incomplete]},
        })
    total_days = sum(days)
    insights.append({
        "type": "total",
        "metric": "heatwave_days",
        "text": f"{int(total_days)} total heatwave days detected across {len(valid)} valid years in {start}–{end}.",
        "detail": {"total_days": int(total_days), "valid_years": len(valid)},
    })
    return insights
