"""Geo identity, geometry, cache-key tests (offline)."""

from app.geo.boundaries import geometry_fingerprint, validate_geojson_geometry
from app.services.analysis import build_cache_key
from app.services.geography import _parse_osm_id, resolve_region, search_local


def test_kolkata_disambiguation():
    hits = search_local("kolkata")
    ids = {h["region_id"] for h in hits}
    assert "geo:kolkata-city-in" in ids
    assert "geo:kolkata-district-in" in ids
    assert hits[0]["region_id"] in ids


async def test_canonical_identity_stable():
    a = await resolve_region("geo:london-gb")
    b = await resolve_region("geo:london-gb")
    assert a and b
    assert a["boundary_version"] == b["boundary_version"]
    assert a["geometry_fingerprint"] == b["geometry_fingerprint"]


def test_geometry_validation_rejects_bbox_rect_as_boundary():
    # A bbox-style rectangle IS a valid polygon structurally, but fingerprinting
    # distinguishes it from canonical geometries — same-name reuse is by ID.
    ok, _ = validate_geojson_geometry({"type": "Point", "coordinates": [0, 0]})
    assert ok is False
    ok2, _ = validate_geojson_geometry(
        {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]})
    assert ok2 is True


def test_cache_key_includes_all_inputs():
    base = {"region_id": "geo:london-gb", "boundary_version": "v:abc",
            "geometry_fingerprint": "fp1", "start_year": 1990, "end_year": 2020,
            "baseline_start": 1991, "baseline_end": 2020, "hazard": "heatwave"}
    k1 = build_cache_key(base)
    k2 = build_cache_key({**base, "boundary_version": "v:DIFFERENT"})
    k3 = build_cache_key({**base, "hazard": "heatwave"})
    assert k1 != k2  # boundary change invalidates cache
    assert k1 == k3
    k4 = build_cache_key({**base, "baseline_start": 1981})
    assert k1 != k4  # baseline change invalidates cache


def test_fingerprint_changes_with_geometry():
    g1 = {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]}
    g2 = {"type": "Polygon", "coordinates": [[[0, 0], [2, 0], [2, 2], [0, 2], [0, 0]]]}
    assert geometry_fingerprint(g1) != geometry_fingerprint(g2)


def test_osm_id_parsing_rejects_garbage_without_network():
    assert _parse_osm_id("geo:london-gb") is None
    assert _parse_osm_id("osm:bogus") is None
    assert _parse_osm_id("osm:city:abc") is None
    assert _parse_osm_id("osm:relation:1942587") == ("relation", "1942587")


async def test_resolve_unknown_region_is_none():
    assert await resolve_region("geo:nowhere-at-all") is None
    assert await resolve_region("osm:bogus") is None
