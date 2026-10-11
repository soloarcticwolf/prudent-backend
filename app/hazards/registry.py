"""Hazard registry — extensible multi-hazard architecture (heatwaves live)."""

from __future__ import annotations

from typing import Any, Dict, List

REGISTRY: List[Dict[str, Any]] = [
    {
        "hazard_id": "heatwave",
        "display_name": "Heatwaves",
        "description": "Extended periods of extreme heat detected from daily maximum temperature.",
        "provider": "open-meteo",
        "dataset": "era5-reanalysis-archive",
        "method": "calendar-day 95th percentile exceedance, >=3 consecutive days (baseline sampled as analysis series)",
        "methodology_version": "heatwave-v2",
        "units": {"temperature": "°C", "duration": "days", "frequency": "events/yr"},
        "legend": {"kind": "sequential", "low": "#EAEDE5", "high": "#C96F53"},
        "status": "available",
        "coverage": {"start_year": 1940, "end_year": 2025, "note": "coverage varies by location"},
    },
    {
        "hazard_id": "flood",
        "display_name": "Flood indicators",
        "description": "Precipitation-based flood indicators.",
        "provider": None, "dataset": None, "method": None,
        "methodology_version": None, "units": {}, "legend": {},
        "status": "planned", "coverage": {},
    },
    {
        "hazard_id": "drought",
        "display_name": "Drought indices",
        "description": "Standardized precipitation/drought indices.",
        "provider": None, "dataset": None, "method": None,
        "methodology_version": None, "units": {}, "legend": {},
        "status": "planned", "coverage": {},
    },
    {
        "hazard_id": "cold-wave",
        "display_name": "Cold waves",
        "description": "Extended cold extremes.",
        "provider": None, "dataset": None, "method": None,
        "methodology_version": None, "units": {}, "legend": {},
        "status": "planned", "coverage": {},
    },
    {
        "hazard_id": "wildfire-weather",
        "display_name": "Wildfire-weather conditions",
        "description": "Hot-dry-windy fire-weather indicators.",
        "provider": None, "dataset": None, "method": None,
        "methodology_version": None, "units": {}, "legend": {},
        "status": "planned", "coverage": {},
    },
]


def list_hazards() -> List[Dict[str, Any]]:
    return REGISTRY


def get_hazard(hazard_id: str) -> Dict[str, Any] | None:
    for h in REGISTRY:
        if h["hazard_id"] == hazard_id:
            return h
    return None
