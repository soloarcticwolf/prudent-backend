"""Structured provenance capture (schema provenance-v1).

Covers: source records (analytical / boundary / context), transformation
stages with input/transformation/parameters/output/contribution, and
metric-level lineage. Extensible to future hazards/providers — no
heatwave-only assumptions beyond the stage contents passed in.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

SCHEMA_VERSION = "provenance-v1"


class SourceCache(BaseModel):
    hit: bool = False
    checked_at: Optional[str] = None
    age_s: Optional[float] = None
    original_retrieved_at: Optional[str] = None


class SourceRecord(BaseModel):
    source_id: str
    provider: str
    dataset: str
    role: Literal["analytical", "boundary", "context"]
    endpoint: str
    operation: str
    fields_consumed: List[str] = []
    units_provider: Optional[str] = None
    units_used: Optional[str] = None
    unit_conversion: str = "none"
    observation_start: Optional[str] = None
    observation_end: Optional[str] = None
    retrieved_at: Optional[str] = None
    provider_updated_at: Optional[str] = None
    records_retrieved: int = 0
    records_accepted: int = 0
    records_rejected: int = 0
    records_missing: int = 0
    response_fingerprint: Optional[str] = None
    cache: SourceCache = Field(default_factory=SourceCache)
    attribution: str = ""
    license: str = ""
    website: str = ""
    docs_url: str = ""
    notes: str = ""
    baseline: Dict[str, Any] = {}


class StageRecord(BaseModel):
    stage_id: str
    title: str
    input: str
    transformation: str
    parameters: str
    output: str
    contributes_to: List[str] = []


class MetricLineage(BaseModel):
    metric: str
    kind: Literal["observed", "estimated", "derived", "inference"]
    derivation: str
    inputs: List[str] = []
    exclusions: Optional[str] = None
    method: Optional[str] = None
    statistics: Optional[str] = None


class ProvenanceDetail(BaseModel):
    schema_version: str = SCHEMA_VERSION
    run_id: str
    status: Literal["full"] = "full"
    observation_start: str
    observation_end: str
    retrieved_at: str
    completed_at: str
    cache_hit: bool = False
    cache_checked_at: Optional[str] = None
    sources: List[SourceRecord] = []
    stages: List[StageRecord] = []
    metric_lineage: List[MetricLineage] = []
    spatial: Dict[str, Any] = {}
    software: Dict[str, Any] = {}
    warnings: List[str] = []


def provenance_status(result: Dict[str, Any]) -> str:
    """'full' for runs captured with provenance-v1, else honest 'legacy'."""
    det = result.get("provenance_detail")
    if isinstance(det, dict) and det.get("schema_version") == SCHEMA_VERSION:
        return "full"
    return "legacy"


LEGACY_NOTICE = (
    "Provenance not fully captured for this run. This report was generated "
    "before detailed provenance capture existed, so retrieval timestamps and "
    "intermediate derivation records are unavailable. Re-running the analysis "
    "produces a fully traceable report."
)


def heatwave_stages(ctx: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Lineage stages for the heatwave pipeline from the run context."""
    bl = ctx["baseline"]
    return [
        {
            "stage_id": "source-observations",
            "title": "Source observations",
            "input": f"Daily {ctx['fields']} from {ctx['provider']} ({ctx['dataset_version']}) "
                     f"for {ctx['n_points']} sampled points, {ctx['obs_start']} to {ctx['obs_end']}.",
            "transformation": "Provider daily time series accepted as-is; rows with unparseable "
                              "dates rejected, null values retained as missing (never zero-filled).",
            "parameters": f"Endpoint {ctx['endpoint']}; timeout {ctx['timeout_s']}s with bounded retries.",
            "output": f"{ctx['records_retrieved']} records retrieved, {ctx['records_accepted']} accepted, "
                      f"{ctx['records_missing']} missing, {ctx['records_rejected']} rejected.",
            "contributes_to": ["heatwave_events", "heatwave_days", "mean_duration", "trend"],
        },
        {
            "stage_id": "normalization",
            "title": "Normalization and quality checks",
            "input": "Raw per-point series with provider units (°C) and ISO dates.",
            "transformation": "Unit check (provider °C, used °C — no conversion); date parsing to UTC days; "
                              "per-date equal-weight mean across sampled points.",
            "parameters": "Sampled-area-mean-v1; equal weights; dates failing ISO parse are rejected and counted.",
            "output": f"{ctx['combined_days']} combined daily values with per-date point counts.",
            "contributes_to": ["heatwave_events", "heatwave_days", "mean_duration", "trend"],
        },
        {
            "stage_id": "aggregation",
            "title": "Geographic aggregation",
            "input": f"Per-point series inside boundary {ctx['boundary_version']}.",
            "transformation": "Deterministic interior sampling (centroid + offsets) with equal-weight daily mean. "
                              + ctx.get("sampling_note", ""),
            "parameters": f"{ctx['n_points']} points used; strategy sampled-area-mean-v1; "
                          f"{ctx.get('partial_days', 0)} days with partial spatial coverage; "
                          "coarse-resolution limits disclosed.",
            "output": "One regional daily Tmax series representing the selected boundary (an estimate, not a measurement).",
            "contributes_to": ["heatwave_events", "heatwave_days", "mean_duration", "trend", "boundary_highlight"],
        },
        {
            "stage_id": "thresholds",
            "title": "Local percentile thresholds",
            "input": f"Baseline {bl['start']}-{bl['end']} daily Tmax ({ctx['baseline_days']} combined days).",
            "transformation": "Calendar-day 95th percentile with ±7-day pooling (linear interpolation); "
                              "Feb 29 mapped to Feb 28. Baseline uses the same sample points and "
                              "aggregation as the analysis series.",
            "parameters": f"Percentile 95.0; window ±7 days; minimum {ctx['min_obs']} pooled obs; "
                          f"methodology {ctx['methodology_version']}.",
            "output": f"{ctx['threshold_days']} calendar days with stable thresholds; "
                      f"{ctx['threshold_missing']} without (those days can never qualify).",
            "contributes_to": ["heatwave_events", "heatwave_days", "mean_duration"],
        },
        {
            "stage_id": "detection",
            "title": "Consecutive-day event detection",
            "input": "Regional daily Tmax vs calendar-day thresholds.",
            "transformation": "Group consecutive exceedance days; retain runs of >= 3 days; missing or "
                              "threshold-less days break runs (continuity never assumed).",
            "parameters": "Minimum event length 3 days; strict exceedance (> threshold).",
            "output": f"{ctx['event_count']} events with start/end dates, durations, peak Tmax and exceedances.",
            "contributes_to": ["heatwave_events", "heatwave_days", "mean_duration"],
        },
        {
            "stage_id": "annual",
            "title": "Annual statistics",
            "input": "Detected events + regional daily series.",
            "transformation": "Events allocated to the year of their start date; per-year counts, day totals, "
                              "duration stats, completeness = valid days vs the full expected date index.",
            "parameters": "Completeness rule: years with <80% of expected days valid are flagged incomplete "
                          "and excluded from trends; a year is never complete merely because its surviving "
                          "filtered dates all have values.",
            "output": f"{ctx['annual_years']} annual records; {ctx['incomplete_years']} flagged incomplete.",
            "contributes_to": ["heatwave_events", "heatwave_days", "mean_duration", "trend"],
        },
        {
            "stage_id": "trend",
            "title": "Trend analysis",
            "input": "Complete annual years only (incomplete years excluded with reasons).",
            "transformation": "Ordinary least-squares regression per metric; 95% CI from standard error.",
            "parameters": "Minimum 3 valid years; short series (<8 years) flagged indicative-only.",
            "output": "Slope (units/yr), p-value, CI, modeled change over the interval — statistical inference, not attribution.",
            "contributes_to": ["trend"],
        },
        {
            "stage_id": "display",
            "title": "Displayed result",
            "input": "Annual series, trends, insights, warnings, provenance record.",
            "transformation": "Deterministic insight templates traceable to computed statistics; charts break lines "
                              "across missing years instead of interpolating.",
            "parameters": "No LLM; no hardcoded percentages; missing values shown as gaps, never zero.",
            "output": "The dashboard report with this attached provenance record.",
            "contributes_to": ["heatwave_events", "heatwave_days", "mean_duration", "trend", "boundary_highlight"],
        },
    ]


def heatwave_metric_lineage(ctx: Dict[str, Any]) -> List[Dict[str, Any]]:
    trend_stats = ctx.get("trend_stats", {})
    return [
        {
            "metric": "heatwave_events",
            "kind": "derived",
            "derivation": "Count of qualifying events detected under the reported methodology version: >=3 consecutive valid days "
                          "above the calendar-day 95th percentile.",
            "inputs": ["temperature_2m_max (daily max, °C)", "calendar-day thresholds", "consecutive-day grouping"],
        },
        {
            "metric": "heatwave_days",
            "kind": "derived",
            "derivation": "Sum of durations of qualifying events, allocated to the year of each event's start date. "
                          "Missing days contribute nothing and are never counted as zero-event days.",
            "inputs": ["detected event start/end dates", "annual allocation rule (start-year)"],
        },
        {
            "metric": "mean_duration",
            "kind": "derived",
            "derivation": "Total qualifying event durations divided by the number of qualifying events "
                          f"(denominator n={ctx.get('total_events', 0)}). Shown as — when no events qualify, never 0.",
            "inputs": ["detected event durations", "event count denominator"],
        },
        {
            "metric": "trend",
            "kind": "inference",
            "derivation": "OLS regression over complete years only.",
            "inputs": ["complete annual years"],
            "exclusions": ctx.get("trend_exclusions", "none"),
            "method": "ordinary-least-squares",
            "statistics": trend_stats,
        },
        {
            "metric": "boundary_highlight",
            "kind": "estimated",
            "derivation": "Boundary polygon from the boundary provider; climate grid cells sampled inside it "
                          "and averaged (sampled-area-mean-v1). Regional values are estimates, not street-level measurements.",
            "inputs": ["boundary polygon", "sampled grid points", "equal-weight mean"],
        },
    ]
