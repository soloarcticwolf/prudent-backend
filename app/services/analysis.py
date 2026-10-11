"""Analysis service: cache-key, pipeline orchestration, persistence."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any, Dict, List, Optional

from app.climate.openmeteo import (
    DATASET_VERSION, area_weighted_mean, fetch_daily_tmax, sample_points,
)
from app.config import get_settings
from app.db import DB
from app.geo.boundaries import point_in_polygon
from app.hazards.registry import get_hazard
from app.science.heatwave import (
    BASELINE_END_DEFAULT, BASELINE_START_DEFAULT, DailyObs,
    aggregate_annual, build_insights, calculate_thresholds, detect_events, ols_trend,
)
from app.services.geography import boundary_for, resolve_region
from app.services.provenance import (
    ProvenanceDetail, heatwave_metric_lineage, heatwave_stages,
)
from app.utils.hashing import sha256_hex, utcnow

AGGREGATION_METHOD = "sampled-area-mean-v1"
METHODOLOGY_VERSION = "heatwave-v2"


def build_cache_key(params: Dict[str, Any]) -> str:
    canonical = {
        "region_id": params["region_id"],
        "boundary_version": params["boundary_version"],
        "geometry_fingerprint": params.get("geometry_fingerprint"),
        "aggregation": AGGREGATION_METHOD,
        "provider": "open-meteo",
        "dataset_version": DATASET_VERSION,
        "start_year": params["start_year"],
        "end_year": params["end_year"],
        "baseline_start": params.get("baseline_start", BASELINE_START_DEFAULT),
        "baseline_end": params.get("baseline_end", BASELINE_END_DEFAULT),
        "hazard": params.get("hazard", "heatwave"),
        "methodology_version": METHODOLOGY_VERSION,
    }
    return "hw:" + sha256_hex(canonical)


async def validate_request(region_id: str, start_year: int, end_year: int, hazard: str) -> List[str]:
    s = get_settings()
    errors: List[str] = []
    if await resolve_region(region_id) is None:
        errors.append(f"unknown region_id {region_id!r}")
    if start_year < s.MIN_START_YEAR or end_year > s.MAX_END_YEAR:
        errors.append(f"supported range is {s.MIN_START_YEAR}–{s.MAX_END_YEAR}")
    if end_year - start_year + 1 > s.MAX_YEAR_SPAN:
        errors.append(f"interval exceeds {s.MAX_YEAR_SPAN} years")
    if get_hazard(hazard) is None or get_hazard(hazard).get("status") != "available":
        errors.append(f"hazard {hazard!r} is not available (heatwave is the supported MVP hazard)")
    return errors


async def run_pipeline(
    region_id: str,
    start_year: int,
    end_year: int,
    hazard: str = "heatwave",
    baseline_start: int = BASELINE_START_DEFAULT,
    baseline_end: int = BASELINE_END_DEFAULT,
    progress: Optional[List[str]] = None,
    progress_state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    prog = progress if progress is not None else []

    def _pct(frac: float) -> None:
        if progress_state is not None:
            progress_state["pct"] = frac
    region = await resolve_region(region_id)
    if region is None:
        raise ValueError(f"unknown region {region_id}")
    bnd = await boundary_for(region_id)
    if bnd is None:
        raise ValueError("verified boundary unavailable for region")

    params = {
        "region_id": region_id,
        "boundary_version": region["boundary_version"],
        "geometry_fingerprint": region.get("geometry_fingerprint"),
        "start_year": start_year, "end_year": end_year,
        "baseline_start": baseline_start, "baseline_end": baseline_end,
        "hazard": hazard,
    }
    cache_key = build_cache_key(params)

    prog.append("Checking available data")
    _pct(15.0)
    cached = await DB.collection("analysis_results").find_one({"cache_key": cache_key})
    if cached:
        return cached["result"]

    prog.append("Retrieving historical observations")
    cache_checked_at = utcnow().isoformat()
    points = sample_points(region["bbox"], [region["centroid"]["lon"], region["centroid"]["lat"]])
    # Containment vs the stored boundary polygon (accuracy audit §6).
    containment = [point_in_polygon(lon, lat, bnd["geometry"]) for lon, lat in points]
    if region.get("approximate"):
        # Preview geometries are display-only approximations: documented
        # bounding-box-interior sampling applies; containment is reported.
        used = points
        sampling_note = (
            "Bounding-box-interior sampling: the stored boundary is a simplified "
            "preview, so samples target the bbox interior; containment vs the "
            "preview polygon is reported, not enforced."
        )
    else:
        used = [p for p, inside in zip(points, containment) if inside]
        if not used:
            used = [points[0]]
        sampling_note = "Samples constrained to the stored boundary polygon."
    dropped = len(points) - len(used)
    spatial_points = [
        {"lon": p[0], "lat": p[1],
         "inside_stored_boundary": point_in_polygon(p[0], p[1], bnd["geometry"])}
        for p in used
    ]
    # Retrieval dominates wall time: 15% -> 70% spread across every fetch.
    n_fetches = 2 * len(used)
    done = 0

    def _retrieval_pct() -> None:
        _pct(15.0 + 55.0 * done / max(n_fetches, 1))

    per_point = []
    fetch_log: List[Dict[str, Any]] = []
    for lon, lat in used:
        requested_at = utcnow()
        fetched = await fetch_daily_tmax(lat, lon, date(start_year, 1, 1), date(end_year, 12, 31))
        responded_at = utcnow()
        per_point.append(fetched["series"])
        rows = fetched["series"]
        fetch_log.append({
            "lon": lon, "lat": lat,
            "requested_at": requested_at.isoformat(),
            "responded_at": responded_at.isoformat(),
            "retrieved": len(rows) + int(fetched.get("rejected", 0)),
            "accepted": sum(1 for r in rows if r.get("tmax_c") is not None),
            "missing": sum(1 for r in rows if r.get("tmax_c") is None),
            "rejected": int(fetched.get("rejected", 0)),
            "fields": fetched.get("fields_consumed", ["temperature_2m_max"]),
        })
        done += 1
        _retrieval_pct()
    # Baseline uses the SAME points and aggregation as the analysis series
    # (heatwave-v2; v1 used the centroid only — a spatial mismatch).
    base_fetch_log: List[Dict[str, Any]] = []
    base_per_point = []
    for lon, lat in used:
        requested_at = utcnow()
        fetched = await fetch_daily_tmax(lat, lon, date(baseline_start, 1, 1), date(baseline_end, 12, 31))
        responded_at = utcnow()
        base_per_point.append(fetched["series"])
        rows = fetched["series"]
        base_fetch_log.append({
            "lon": lon, "lat": lat,
            "requested_at": requested_at.isoformat(),
            "responded_at": responded_at.isoformat(),
            "retrieved": len(rows) + int(fetched.get("rejected", 0)),
            "accepted": sum(1 for r in rows if r.get("tmax_c") is not None),
            "missing": sum(1 for r in rows if r.get("tmax_c") is None),
            "rejected": int(fetched.get("rejected", 0)),
        })
        done += 1
        _retrieval_pct()
    base_responded_at = max(f["responded_at"] for f in base_fetch_log)
    base_requested_at = min(f["requested_at"] for f in base_fetch_log)
    base = {"series": area_weighted_mean(base_per_point),
            "rejected": sum(f["rejected"] for f in base_fetch_log)}
    prog.append("Validating data coverage")
    _pct(72.0)
    combined = area_weighted_mean(per_point)
    if not combined:
        raise RuntimeError("provider returned no usable observations for this region/period")
    obs = [DailyObs(day=date.fromisoformat(r["date"]), tmax_c=r["tmax_c"]) for r in combined]
    base_obs = [DailyObs(day=date.fromisoformat(r["date"]), tmax_c=r.get("tmax_c")) for r in base["series"]]

    prog.append("Detecting heatwave events")
    _pct(78.0)
    thresholds, thr_meta = calculate_thresholds(base_obs)
    if thr_meta["days_with_threshold"] < 200:
        raise RuntimeError(
            "This region does not have sufficient reference data for the selected heatwave definition."
        )
    events, det_info = detect_events(obs, thresholds)

    prog.append("Calculating annual trends")
    _pct(88.0)
    annual = aggregate_annual(obs, events, date(start_year, 1, 1), date(end_year, 12, 31))
    years = [a["year"] for a in annual if not a["incomplete"]]
    trends = {
        "event_count": ols_trend(years, [a["event_count"] if not a["incomplete"] else None for a in annual]),
        "heatwave_days": ols_trend(years, [a["heatwave_days"] if not a["incomplete"] else None for a in annual]),
    }
    insights = build_insights({
        "annual": annual, "trends": trends, "start_year": start_year, "end_year": end_year,
    })

    partial_days = sum(1 for r in combined if r.get("n_points", len(used)) < len(used))
    warnings: List[str] = []
    if region.get("approximate"):
        warnings.append("Boundary is a simplified preview; regional estimate is approximate.")
    if dropped and not region.get("approximate"):
        warnings.append(
            f"{dropped} sample point(s) fell outside the stored boundary and were excluded.")
    if any(a["incomplete"] for a in annual):
        warnings.append("The analysis completed, but some years were excluded because of missing observations.")
    if partial_days:
        warnings.append(
            f"{partial_days} day(s) have partial spatial coverage (fewer sample points reporting).")
    if thr_meta["days_insufficient"] > 0:
        warnings.append(f"{thr_meta['days_insufficient']} calendar days lacked stable thresholds.")
    warnings.append("Coarse reanalysis resolution cannot describe street-level conditions.")

    analysis_id = "an_" + uuid.uuid4().hex[:12]
    run_id = "run_" + uuid.uuid4().hex[:12]
    created_at = utcnow().isoformat()
    retrieved_at = max(
        [f["responded_at"] for f in fetch_log] + [base_responded_at]
    )
    det = _build_provenance_detail(
        run_id=run_id,
        region_id=region_id, region=region, bnd=bnd,
        start_year=start_year, end_year=end_year,
        baseline_start=baseline_start, baseline_end=baseline_end,
        fetch_log=fetch_log,
        base_series=base["series"],
        base_fetch_log=base_fetch_log,
        base_requested_at=base_requested_at,
        base_responded_at=base_responded_at,
        combined=combined, obs=obs, base_obs=base_obs,
        points=used, spatial_points=spatial_points, sampling_note=sampling_note,
        partial_days=partial_days, thresholds_meta=thr_meta,
        events=events, annual=annual, trends=trends,
        cache_checked_at=cache_checked_at,
        retrieved_at=retrieved_at, completed_at=created_at,
        warnings=warnings,
    )
    result = {
        "analysis_id": analysis_id,
        "run_id": run_id,
        "cache_key": cache_key,
        "region_id": region_id,
        "display_name": region["display_name"],
        "admin_level": region.get("admin_level"),
        "boundary_version": region["boundary_version"],
        "geometry_fingerprint": region.get("geometry_fingerprint"),
        "start_year": start_year, "end_year": end_year,
        "actual_start": f"{start_year}-01-01", "actual_end": f"{end_year}-12-31",
        "hazard": hazard,
        "methodology_version": METHODOLOGY_VERSION,
        "baseline": {"start": baseline_start, "end": baseline_end,
                     "window_days": 7, "percentile": 95.0, **thr_meta},
        "provider": "open-meteo",
        "dataset_version": DATASET_VERSION,
        "aggregation": AGGREGATION_METHOD,
        "annual": annual,
        "events": events,
        "trends": trends,
        "insights": insights,
        "warnings": warnings,
        "provenance": {
            "boundary": bnd["provenance"],
            "dataset": {"provider": "open-meteo", "version": DATASET_VERSION,
                        "attribution": "Historical reanalysis via Open-Meteo archive API (ERA5 family)."},
            "methodology": (f"Prudent {METHODOLOGY_VERSION}: 95th pct calendar-day threshold, "
                            ">=3 consecutive days, baseline sampled as the analysis series."),
        },
        "provenance_detail": det.model_dump(),
        "created_at": created_at,
    }
    prog.append("Preparing results")
    _pct(96.0)
    await DB.collection("analysis_results").insert_one({"_id": analysis_id, "cache_key": cache_key, "result": result, "created_at": result["created_at"]})
    # Immutable run record: refreshes never rewrite this run's history.
    await DB.collection("provenance_runs").insert_one(
        {"_id": run_id, "analysis_id": analysis_id, "created_at": created_at,
         "provenance": det.model_dump()})
    # Persist annual metrics + events (chunked, indexed).
    for a in annual:
        await DB.collection("annual_climate_metrics").update_one(
            {"region_id": region_id, "year": a["year"], "methodology_version": METHODOLOGY_VERSION},
            {"$set": {**a, "region_id": region_id, "methodology_version": METHODOLOGY_VERSION}}, upsert=True)
    for e in events:
        await DB.collection("heatwave_events").insert_one(
            {"region_id": region_id, "methodology_version": METHODOLOGY_VERSION, **e})
    return result


def _build_provenance_detail(
    *,
    run_id: str,
    region_id: str,
    region: Dict[str, Any],
    bnd: Dict[str, Any],
    start_year: int,
    end_year: int,
    baseline_start: int,
    baseline_end: int,
    fetch_log: List[Dict[str, Any]],
    base_series: List[Dict[str, Any]],
    base_fetch_log: List[Dict[str, Any]],
    base_requested_at: str,
    base_responded_at: str,
    combined: List[Dict[str, Any]],
    obs: List[Any],
    base_obs: List[Any],
    points: List[List[float]],
    spatial_points: List[Dict[str, Any]],
    sampling_note: str,
    partial_days: int,
    thresholds_meta: Dict[str, Any],
    events: List[Dict[str, Any]],
    annual: List[Dict[str, Any]],
    trends: Dict[str, Any],
    cache_checked_at: str,
    retrieved_at: str,
    completed_at: str,
    warnings: List[str],
) -> ProvenanceDetail:
    s = get_settings()
    tot = {"retrieved": 0, "accepted": 0, "missing": 0, "rejected": 0}
    for f in fetch_log:
        for k in tot:
            tot[k] += int(f[k])
    btot = {"retrieved": 0, "accepted": 0, "missing": 0, "rejected": 0}
    for f in base_fetch_log:
        for k in btot:
            btot[k] += int(f[k])

    climate_source = {
        "source_id": "openmeteo-archive",
        "provider": "open-meteo",
        "dataset": "ERA5-family reanalysis archive",
        "role": "analytical",
        "endpoint": s.OPENMETEO_BASE_URL,
        "operation": "GET daily=temperature_2m_max (per sampled point, analysis period)",
        "fields_consumed": ["temperature_2m_max"],
        "units_provider": "celsius",
        "units_used": "celsius",
        "unit_conversion": "none — provider and analysis both use °C",
        "observation_start": f"{start_year}-01-01",
        "observation_end": f"{end_year}-12-31",
        "retrieved_at": retrieved_at,
        "provider_updated_at": None,  # endpoint returns no publication timestamp
        "records_retrieved": tot["retrieved"],
        "records_accepted": tot["accepted"],
        "records_rejected": tot["rejected"],
        "records_missing": tot["missing"],
        "response_fingerprint": sha256_hex({"series": combined}),
        "cache": {"hit": False, "checked_at": cache_checked_at},
        "attribution": "Historical reanalysis via Open-Meteo (ERA5 family)",
        "license": "Subject to Open-Meteo provider terms; see dataset documentation.",
        "website": "https://open-meteo.com/",
        "docs_url": "https://open-meteo.com/en/docs/historical-weather-api",
        "baseline": {
            "period": f"{baseline_start}-01-01 to {baseline_end}-12-31",
            "sampling": "same points and aggregation as the analysis series (heatwave-v2)",
            "requested_at": base_requested_at,
            "responded_at": base_responded_at,
            "retrieved": btot["retrieved"],
            "accepted": btot["accepted"],
            "missing": btot["missing"],
            "rejected": btot["rejected"],
            "fingerprint": sha256_hex({"series": base_series}),
        },
    }

    if region_id.startswith("geo:"):
        boundary_source = {
            "source_id": "boundary-bundled",
            "provider": region.get("boundary_provider", "prudent"),
            "dataset": str(region.get("source_dataset", "prudent-preview")),
            "role": "boundary",
            "endpoint": "bundled with application (no network retrieval)",
            "operation": "local catalog lookup",
            "fields_consumed": ["polygon geometry", "centroid", "bbox"],
            "observation_start": None, "observation_end": None,
            "retrieved_at": None,
            "provider_updated_at": None,
            "response_fingerprint": region.get("geometry_fingerprint"),
            "cache": {"hit": False, "checked_at": cache_checked_at},
            "attribution": str(region.get("attribution", "")),
            "license": str(region.get("license", "")),
            "website": "",
            "docs_url": "",
            "notes": "Preview geometry; approximate and always flagged in the report.",
        }
    else:
        boundary_source = {
            "source_id": "boundary-nominatim",
            "provider": "openstreetmap-nominatim",
            "dataset": "openstreetmap",
            "role": "boundary",
            "endpoint": f"{s.NOMINATIM_BASE_URL}/lookup",
            "operation": "GET polygon_geojson=1 for the selected OSM object",
            "fields_consumed": ["geojson", "display_name", "address", "lat", "lon", "boundingbox"],
            "observation_start": None, "observation_end": None,
            "retrieved_at": region.get("_cache_original_ts"),
            "provider_updated_at": None,
            "response_fingerprint": region.get("geometry_fingerprint"),
            "cache": {"hit": bool(region.get("_cache_hit")),
                      "checked_at": cache_checked_at,
                      "original_retrieved_at": region.get("_cache_original_ts")},
            "attribution": "© OpenStreetMap contributors (ODbL)",
            "license": "ODbL — © OpenStreetMap contributors",
            "website": "https://nominatim.org/",
            "docs_url": "https://nominatim.org/release-docs/latest/api/Overview/",
        }

    # Map tiles are display context only — never an analytical input.
    map_source = {
        "source_id": "basemap-carto",
        "provider": "carto",
        "dataset": "positron basemap (OpenStreetMap data)",
        "role": "context",
        "endpoint": "https://basemaps.cartocdn.com/gl/positron-gl-style/style.json",
        "operation": "vector tile style + tiles for map display",
        "fields_consumed": [],
        "observation_start": None, "observation_end": None,
        "retrieved_at": None,
        "provider_updated_at": None,
        "cache": {"hit": False, "checked_at": cache_checked_at},
        "attribution": "© OpenStreetMap contributors © CARTO",
        "license": "Map data ODbL; tiles per CARTO terms.",
        "website": "https://carto.com/basemaps/",
        "docs_url": "https://carto.com/basemaps/",
        "notes": "Display context only; contributes no values to any metric.",
    }

    incomplete = [a for a in annual if a.get("incomplete")]
    th = trends.get("heatwave_days", {})
    if th.get("supported"):
        trend_stats = (
            f"slope {th.get('slope_per_year', 0):+.4f} days/yr, "
            f"p={th.get('p_value')}, 95% CI [{th.get('ci95_low')}, {th.get('ci95_high')}], "
            f"n={th.get('n_years')} complete years"
        )
    else:
        trend_stats = f"unsupported ({th.get('reason', 'no estimate')})"

    ctx = {
        "fields": "temperature_2m_max",
        "provider": "open-meteo",
        "dataset_version": DATASET_VERSION,
        "n_points": len(points),
        "obs_start": f"{start_year}-01-01",
        "obs_end": f"{end_year}-12-31",
        "endpoint": s.OPENMETEO_BASE_URL,
        "timeout_s": s.OPENMETEO_TIMEOUT_S,
        "records_retrieved": tot["retrieved"],
        "records_accepted": tot["accepted"],
        "records_missing": tot["missing"],
        "records_rejected": tot["rejected"],
        "combined_days": len(combined),
        "boundary_version": region.get("boundary_version"),
        "methodology_version": METHODOLOGY_VERSION,
        "baseline": {"start": baseline_start, "end": baseline_end},
        "baseline_days": len(base_series),
        "baseline_sampling": "same points and aggregation as analysis series (heatwave-v2)",
        "min_obs": thresholds_meta.get("min_baseline_obs"),
        "threshold_days": thresholds_meta.get("days_with_threshold"),
        "threshold_missing": thresholds_meta.get("days_insufficient"),
        "event_count": len(events),
        "annual_years": len(annual),
        "incomplete_years": len(incomplete),
        "completeness_rule": "valid days vs the full expected date index per year; <80% valid is incomplete",
        "sampling_note": sampling_note,
        "partial_days": partial_days,
    }
    lineage_ctx = {
        "total_events": len(events),
        "trend_exclusions": (
            f"{len(incomplete)} incomplete year(s) excluded: "
            + ", ".join(str(a["year"]) for a in incomplete)
            if incomplete else "none — all years complete"
        ),
        "trend_stats": trend_stats,
    }
    return ProvenanceDetail(
        run_id=run_id,
        observation_start=f"{start_year}-01-01",
        observation_end=f"{end_year}-12-31",
        retrieved_at=retrieved_at,
        completed_at=completed_at,
        cache_hit=False,
        cache_checked_at=cache_checked_at,
        sources=[climate_source, boundary_source, map_source],
        stages=heatwave_stages(ctx),
        metric_lineage=heatwave_metric_lineage(lineage_ctx),
        spatial={
            "strategy": "sampled-area-mean-v1",
            "points": [{**sp, "weight": round(1.0 / len(points), 4)} for sp in spatial_points],
            "n_points": len(points),
            "containment": (f"{sum(1 for sp in spatial_points if sp['inside_stored_boundary'])}/"
                            f"{len(spatial_points)} used points inside the stored boundary"),
            "sampling_note": sampling_note,
            "partial_coverage_days": partial_days,
            "note": "Equal-weight daily mean across used sample points; an estimate, not a measurement.",
        },
        software={
            "methodology_version": METHODOLOGY_VERSION,
            "aggregation": AGGREGATION_METHOD,
            "dataset_version": DATASET_VERSION,
            "provenance_schema": "provenance-v1",
            "baseline_fingerprint": sha256_hex({"series": base_series}),
        },
        warnings=warnings,
    )
