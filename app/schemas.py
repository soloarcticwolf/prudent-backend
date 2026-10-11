"""Pydantic v2 request/response schemas — explicit contracts, no raw DB docs."""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator


class SearchResult(BaseModel):
    region_id: str
    display_name: str
    country_code: Optional[str] = None
    admin_level: Optional[str] = None
    parent_id: Optional[str] = None
    centroid: Optional[Dict[str, float]] = None
    bbox: Optional[List[float]] = None
    boundary_provider: Optional[str] = None
    source_dataset: Optional[str] = None
    source_release: Optional[str] = None
    boundary_version: Optional[str] = None
    geometry_fingerprint: Optional[str] = None
    license: Optional[str] = None
    attribution: Optional[str] = None
    approximate: bool = False


class BoundaryResponse(BaseModel):
    region_id: str
    geometry: Dict[str, Any]
    display_geometry: Dict[str, Any]
    bbox: Optional[List[float]] = None
    centroid: Optional[Dict[str, float]] = None
    boundary_version: str
    geometry_fingerprint: str
    provenance: Dict[str, Any]
    warning: Optional[str] = None


class AnalysisRequest(BaseModel):
    region_id: str = Field(min_length=3, max_length=200)
    start_year: int = Field(ge=1940, le=2025)
    end_year: int = Field(ge=1940, le=2025)
    hazard: str = Field(default="heatwave", max_length=64)
    baseline_start: int = Field(default=1991, ge=1900, le=2025)
    baseline_end: int = Field(default=2020, ge=1900, le=2025)

    @field_validator("end_year")
    @classmethod
    def _order(cls, v: int, info) -> int:
        start = info.data.get("start_year")
        if start is not None and v < start:
            raise ValueError("end_year must be >= start_year")
        return v

    @field_validator("baseline_end")
    @classmethod
    def _border(cls, v: int, info) -> int:
        start = info.data.get("baseline_start")
        if start is not None and v < start:
            raise ValueError("baseline_end must be >= baseline_start")
        return v


class JobAccepted(BaseModel):
    status: Literal["queued", "completed"]
    job_id: str
    analysis_id: Optional[str] = None
    cache_hit: bool = False
    message: str


class AnnualMetric(BaseModel):
    year: int
    event_count: int
    heatwave_days: int
    mean_duration_days: Optional[float] = None
    max_duration_days: Optional[int] = None
    peak_tmax_c: Optional[float] = None
    valid_days: int
    total_days: int
    completeness: float
    incomplete: bool = False


class AnalysisResult(BaseModel):
    analysis_id: str
    region_id: str
    display_name: str
    admin_level: Optional[str] = None
    boundary_version: str
    geometry_fingerprint: Optional[str] = None
    start_year: int
    end_year: int
    actual_start: str
    actual_end: str
    hazard: str
    methodology_version: str
    baseline: Dict[str, Any]
    provider: str
    dataset_version: str
    aggregation: str
    annual: List[AnnualMetric]
    trends: Dict[str, Any]
    insights: List[Dict[str, Any]]
    warnings: List[str]
    provenance: Dict[str, Any]
    created_at: str


class JobStatus(BaseModel):
    job_id: str
    status: str
    progress: List[str]
    progress_pct: Optional[float] = None
    request: Dict[str, Any]
    analysis_id: Optional[str] = None
    warnings: List[str] = []
    error: Optional[str] = None
    created_at: str
    updated_at: str
