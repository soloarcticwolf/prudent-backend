"""Canonical geography service: search, resolve, boundaries.

Layered strategy:
1. Local curated catalog (instant, offline, disambiguated; Kolkata city vs district).
2. Live Nominatim (OSM, ODbL) when reachable — supplementary local detail.
   Search results carry `osm:<type>:<id>` ids; selecting one resolves the full
   record + polygon via the Nominatim lookup API (cached 24h, throttled to
   respect the 1 req/s usage policy).
3. geoBoundaries adapter stub with recorded provenance (primary for admin polygons).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional, Tuple

import httpx

from app.config import get_settings
from app.geo import catalog as cat
from app.geo.boundaries import (
    display_geometry, geometry_fingerprint, validate_geojson_geometry,
)

SUPPORTED_LEVELS = ["country", "state", "district", "city"]

_OSM_PREFIX = {"node": "N", "way": "W", "relation": "R"}
_OSM_CACHE: Dict[str, Tuple[float, str, Dict[str, Any]]] = {}
_OSM_TTL_S = 24 * 3600

_nomi_lock = asyncio.Lock()
_last_nomi_ts = 0.0


def _entry_to_region(e: Dict[str, Any]) -> Dict[str, Any]:
    geom = cat.geometry_for(e)
    fp = geometry_fingerprint(geom)
    return {
        "region_id": e["region_id"],
        "display_name": e["display_name"],
        "country_code": e["country_code"],
        "admin_level": e["admin_level"],
        "parent_id": e.get("parent_id"),
        "centroid": {"lon": e["centroid"][0], "lat": e["centroid"][1]},
        "bbox": e["bbox"],
        "boundary_provider": e["boundary_provider"],
        "source_dataset": e["source_dataset"],
        "source_release": e["source_release"],
        "boundary_version": f"{e['boundary_version']}:{fp}",
        "geometry_fingerprint": fp,
        "license": e["license"],
        "attribution": e["attribution"],
        "approximate": e.get("approximate", False),
        "aliases": e.get("aliases", []),
    }


def search_local(query: str, limit: int = 8) -> List[Dict[str, Any]]:
    q = query.strip().lower()
    if not q:
        return []
    scored: List[tuple[int, Dict[str, Any]]] = []
    for e in cat.CATALOG:
        names = [e["display_name"].lower()] + [a.lower() for a in e.get("aliases", [])]
        for n in names:
            if q == n:
                scored.append((0, e)); break
            elif n.startswith(q):
                scored.append((1, e)); break
            elif q in n:
                scored.append((2, e)); break
    scored.sort(key=lambda t: t[0])
    seen = set()
    out = []
    for _, e in scored:
        if e["region_id"] in seen:
            continue
        seen.add(e["region_id"])
        out.append(_entry_to_region(e))
        if len(out) >= limit:
            break
    return out


async def _nominatim_get(path: str, params: Dict[str, Any], timeout: float = 12.0) -> Any:
    """Throttled GET honoring the Nominatim usage policy (max ~1 req/s, identified UA)."""
    global _last_nomi_ts
    s = get_settings()
    async with _nomi_lock:
        wait = 1.1 - (time.monotonic() - _last_nomi_ts)
        if wait > 0:
            await asyncio.sleep(wait)
        async with httpx.AsyncClient(
            timeout=timeout, headers={"User-Agent": "Prudent/0.1 (climate-research)"}
        ) as c:
            r = await c.get(f"{s.NOMINATIM_BASE_URL}{path}", params=params)
            _last_nomi_ts = time.monotonic()
            r.raise_for_status()
            return r.json()


def _bbox_from_nominatim(bb: Any) -> Optional[List[float]]:
    # Nominatim boundingbox = [minlat, maxlat, minlon, maxlon] (strings).
    try:
        vals = [float(x) for x in bb]
        if len(vals) != 4:
            return None
        return [vals[2], vals[0], vals[3], vals[1]]
    except (TypeError, ValueError):
        return None


async def search_nominatim(query: str, limit: int = 5) -> List[Dict[str, Any]]:
    try:
        items = await _nominatim_get(
            "/search",
            {"q": query, "format": "jsonv2", "addressdetails": 1, "limit": limit, "polygon_geojson": 0},
        )
    except Exception:
        return []
    out = []
    for it in items if isinstance(items, list) else []:
        try:
            osm_type, osm_id = it.get("osm_type"), it.get("osm_id")
            if osm_type not in _OSM_PREFIX or osm_id is None:
                continue
            out.append({
                "region_id": f"osm:{osm_type}:{osm_id}",
                "display_name": it.get("display_name", query),
                "country_code": (it.get("address") or {}).get("country_code", "").upper(),
                "admin_level": _class_to_level(it.get("class"), it.get("type"), it.get("addresstype")),
                "parent_id": None,
                "centroid": {"lon": float(it["lon"]), "lat": float(it["lat"])},
                "bbox": _bbox_from_nominatim(it.get("boundingbox")),
                "boundary_provider": "openstreetmap-nominatim",
                "source_dataset": "openstreetmap",
                "source_release": "live",
                "boundary_version": f"nominatim:{osm_type}:{osm_id}",
                "geometry_fingerprint": None,
                "license": "ODbL — © OpenStreetMap contributors",
                "attribution": "© OpenStreetMap contributors (ODbL). Search result; boundary fetched on selection.",
                "approximate": True,
                "aliases": [],
            })
        except Exception:
            continue
    return out


def _class_to_level(cls: Any, typ: Any, addresstype: Any = None) -> str:
    mapping = {"country": "country", "state": "state", "county": "district",
               "city": "city", "town": "city", "village": "city", "suburb": "district"}
    # Nominatim's addresstype (state, county, ...) is the most reliable signal.
    at = str(addresstype or "").lower()
    if at in mapping:
        return mapping[at]
    t = str(typ or "").lower()
    if t in mapping:
        return mapping[t]
    return "city"


async def search_regions(query: str, limit: int = 8) -> List[Dict[str, Any]]:
    local = search_local(query, limit)
    if len(local) >= limit:
        return local[:limit]
    live = await search_nominatim(query, limit - len(local))
    # Deduplicate by display name prefix.
    names = {r["display_name"].lower() for r in local}
    merged = list(local)
    for r in live:
        if r["display_name"].lower() not in names:
            merged.append(r)
    return merged[:limit]


def _parse_osm_id(region_id: str) -> Optional[Tuple[str, str]]:
    try:
        kind, osm_type, osm_id = region_id.split(":")
    except ValueError:
        return None
    if kind != "osm" or osm_type not in _OSM_PREFIX:
        return None
    if not osm_id.isdigit():
        return None
    return osm_type, osm_id


async def _resolve_osm(osm_type: str, osm_id: str) -> Optional[Dict[str, Any]]:
    from datetime import datetime, timezone

    key = f"osm:{osm_type}:{osm_id}"
    hit = _OSM_CACHE.get(key)
    if hit and time.monotonic() - hit[0] < _OSM_TTL_S:
        region = dict(hit[2])
        region["_cache_hit"] = True
        region["_cache_original_ts"] = hit[1]
        return region
    try:
        items = await _nominatim_get(
            "/lookup",
            {"osm_ids": f"{_OSM_PREFIX[osm_type]}{osm_id}", "format": "jsonv2",
             "addressdetails": 1, "polygon_geojson": 1},
        )
    except Exception:
        return None
    if not isinstance(items, list) or not items:
        return None
    it = items[0]
    geom = it.get("geojson")
    ok, _ = validate_geojson_geometry(geom) if isinstance(geom, dict) else (False, "")
    if not ok:
        return None
    assert isinstance(geom, dict)
    fp = geometry_fingerprint(geom)
    region = {
        "region_id": key,
        "display_name": it.get("display_name", key),
        "country_code": ((it.get("address") or {}).get("country_code") or "").upper() or None,
        "admin_level": _class_to_level(it.get("class"), it.get("type"), it.get("addresstype")),
        "parent_id": None,
        "centroid": {"lon": float(it["lon"]), "lat": float(it["lat"])},
        "bbox": _bbox_from_nominatim(it.get("boundingbox")),
        "boundary_provider": "openstreetmap-nominatim",
        "source_dataset": "openstreetmap",
        "source_release": "live",
        "boundary_version": f"nominatim:{osm_type}:{osm_id}:{fp}",
        "geometry_fingerprint": fp,
        "license": "ODbL — © OpenStreetMap contributors",
        "attribution": "© OpenStreetMap contributors (ODbL)",
        "approximate": False,
        "aliases": [],
        "geometry": geom,  # internal: used by boundary_for; never exposed raw by schemas
    }
    wall_ts = datetime.now(timezone.utc).isoformat()
    _OSM_CACHE[key] = (time.monotonic(), wall_ts, region)
    region = dict(region)
    region["_cache_hit"] = False
    region["_cache_original_ts"] = wall_ts
    return region


async def resolve_region(region_id: str) -> Optional[Dict[str, Any]]:
    for e in cat.CATALOG:
        if e["region_id"] == region_id:
            return _entry_to_region(e)
    parsed = _parse_osm_id(region_id)
    if parsed is None:
        return None
    return await _resolve_osm(*parsed)


async def boundary_for(region_id: str) -> Optional[Dict[str, Any]]:
    region = await resolve_region(region_id)
    if region is None:
        return None
    geom: Optional[Dict[str, Any]] = None
    if region_id.startswith("geo:"):
        for e in cat.CATALOG:
            if e["region_id"] == region_id:
                geom = cat.geometry_for(e)
                break
    else:
        geom = region.get("geometry")  # type: ignore[assignment]
    if not isinstance(geom, dict):
        return None
    ok, _ = validate_geojson_geometry(geom)
    if not ok:
        return None
    return {
        "region_id": region_id,
        "geometry": geom,
        "display_geometry": display_geometry(geom),
        "bbox": region["bbox"],
        "centroid": region["centroid"],
        "boundary_version": region["boundary_version"],
        "geometry_fingerprint": region["geometry_fingerprint"],
        "provenance": {
            "provider": region["boundary_provider"],
            "dataset": region["source_dataset"],
            "release": region["source_release"],
            "license": region["license"],
            "attribution": region["attribution"],
            "approximate": region["approximate"],
        },
        "warning": (
            "Simplified preview boundary — approximate only, not authoritative. "
            "Used for display and regional estimation." if region["approximate"] else None
        ),
    }


def capabilities() -> Dict[str, Any]:
    return {
        "admin_levels": SUPPORTED_LEVELS,
        "primary_source": "geoBoundaries (admin polygons; per-release license recorded per fetch)",
        "overview_source": "Natural Earth (public domain) for country/first-order overview",
        "supplementary_source": "OpenStreetMap/Nominatim (ODbL, © OSM contributors) for local detail",
        "custom_polygons": "excluded from MVP; abstraction is extensible via geometry fingerprinting",
        "notes": [
            "Canonical region IDs are the cache keys, never display names.",
            "Preview catalog geometries are approximate and always flagged.",
            "osm:<type>:<id> search results resolve via live Nominatim lookup (cached 24h).",
        ],
    }
