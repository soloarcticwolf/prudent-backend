"""Geometry validation, fingerprinting, simplification."""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from app.utils.hashing import sha256_hex

_VALID_TYPES = {"Polygon", "MultiPolygon"}


def validate_geojson_geometry(geom: Dict[str, Any]) -> Tuple[bool, str]:
    if not isinstance(geom, dict):
        return False, "geometry must be an object"
    gtype = geom.get("type")
    coords = geom.get("coordinates")
    if gtype not in _VALID_TYPES:
        return False, f"unsupported geometry type {gtype!r}; expected Polygon/MultiPolygon"
    if not isinstance(coords, list) or not coords:
        return False, "coordinates missing or empty"
    try:
        if gtype == "Polygon":
            _check_polygon(coords)
        else:
            for poly in coords:
                _check_polygon(poly)
    except ValueError as e:
        return False, str(e)
    return True, "ok"


def _check_polygon(rings: Any) -> None:
    if not isinstance(rings, list) or not rings:
        raise ValueError("polygon must have at least one ring")
    for ring in rings:
        if not isinstance(ring, list) or len(ring) < 4:
            raise ValueError("linear ring must have >= 4 positions")
        for pos in ring:
            if not isinstance(pos, (list, tuple)) or len(pos) < 2:
                raise ValueError("position must be [lon, lat]")
            lon, lat = float(pos[0]), float(pos[1])
            if not (-180.0 <= lon <= 180.0 and -90.0 <= lat <= 90.0):
                raise ValueError(f"position out of range: {pos}")
        first, last = ring[0], ring[-1]
        if abs(float(first[0]) - float(last[0])) > 1e-9 or abs(float(first[1]) - float(last[1])) > 1e-9:
            raise ValueError("linear ring is not closed")


def geometry_fingerprint(geom: Dict[str, Any]) -> str:
    return sha256_hex({"type": geom.get("type"), "coordinates": geom.get("coordinates")})[:16]


def bbox_of(geom: Dict[str, Any]) -> List[float]:
    xs: List[float] = []
    ys: List[float] = []

    def walk(c: Any) -> None:
        if isinstance(c, (list, tuple)) and c and isinstance(c[0], (int, float)):
            xs.append(float(c[0]))
            ys.append(float(c[1]))
        elif isinstance(c, (list, tuple)):
            for part in c:
                walk(part)

    walk(geom.get("coordinates", []))
    if not xs:
        return [0.0, 0.0, 0.0, 0.0]
    return [min(xs), min(ys), max(xs), max(ys)]


def centroid_of(geom: Dict[str, Any]) -> List[float]:
    bb = bbox_of(geom)
    return [(bb[0] + bb[2]) / 2.0, (bb[1] + bb[3]) / 2.0]


def simplify_coords(coords: Any, step: int = 2) -> Any:
    """Naive stride-based simplification for display geometries only.

    Analytical geometry is always preserved separately; this is display-only.
    """
    if isinstance(coords, (list, tuple)) and coords and isinstance(coords[0], (int, float)):
        return list(coords)
    if not isinstance(coords, (list, tuple)):
        return coords
    # Detect a linear ring: list of positions
    if coords and isinstance(coords[0], (list, tuple)) and coords[0] and isinstance(coords[0][0], (int, float)):
        if len(coords) <= 8:
            return [list(p) for p in coords]
        kept = [list(coords[i]) for i in range(0, len(coords), step)]
        if kept[-1] != list(coords[-1]):
            kept.append(list(coords[-1]))
        return kept
    return [simplify_coords(c, step) for c in coords]


def display_geometry(geom: Dict[str, Any]) -> Dict[str, Any]:
    return {"type": geom["type"], "coordinates": simplify_coords(geom["coordinates"], 2)}
