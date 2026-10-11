from __future__ import annotations

from fastapi import APIRouter

from app.config import get_settings
from app.hazards.registry import list_hazards

router = APIRouter(prefix="/api/v1/climate", tags=["climate"])


@router.get("/hazards")
async def hazards():
    return {"hazards": list_hazards()}


@router.get("/coverage")
async def coverage():
    s = get_settings()
    return {
        "provider": "open-meteo",
        "dataset_version": "openmeteo-archive-v1",
        "variables": ["temperature_2m_max"],
        "units": "celsius",
        "supported_years": {"min": s.MIN_START_YEAR, "max": s.MAX_END_YEAR},
        "max_year_span": s.MAX_YEAR_SPAN,
        "baseline_default": {"start": 1991, "end": 2020},
        "attribution": "Historical reanalysis via Open-Meteo archive API (ERA5 family). Coverage varies by location.",
        "note": "Requesting years outside provider coverage returns a clear limitation, not synthetic data.",
    }
