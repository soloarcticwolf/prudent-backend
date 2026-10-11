"""Curated canonical region catalog.

Provenance policy:
- Entries marked provider 'prudent-curated-preview' carry SIMPLIFIED preview
  polygons around the documented centroid. They are NOT authoritative
  boundaries and responses always include a quality warning + provenance.
- Live Nominatim / geoBoundaries matches (when reachable) carry their own
  provenance and are preferred over preview polygons.
Licenses: preview geometries are original simplified shapes (no upstream
license encumbrance). Nominatim data is ODbL (c) OpenStreetMap contributors.
geoBoundaries releases carry their own per-release license, recorded per fetch.
"""

from __future__ import annotations

from typing import Any, Dict, List


def _preview_polygon(lon: float, lat: float, dx: float = 0.12, dy: float = 0.09) -> Dict[str, Any]:
    return {
        "type": "Polygon",
        "coordinates": [[
            [lon - dx, lat - dy],
            [lon + dx, lat - dy],
            [lon + dx, lat + dy],
            [lon - dx, lat + dy],
            [lon - dx, lat - dy],
        ]],
    }


CATALOG: List[Dict[str, Any]] = [
    {
        "region_id": "geo:kolkata-city-in",
        "display_name": "Kolkata (city), West Bengal, India",
        "aliases": ["kolkata", "calcutta", "kolkata city"],
        "country_code": "IN",
        "admin_level": "city",
        "parent_id": "geo:west-bengal-in",
        "centroid": [88.3639, 22.5726],
        "bbox": [88.20, 22.45, 88.52, 22.70],
        "boundary_provider": "prudent-curated-preview",
        "source_dataset": "prudent-preview",
        "source_release": "v1",
        "boundary_version": "preview-v1",
        "license": "Preview geometry (original simplified shape); not authoritative.",
        "attribution": "Prudent preview geometry — approximate only",
        "approximate": True,
    },
    {
        "region_id": "geo:kolkata-district-in",
        "display_name": "Kolkata district, West Bengal, India",
        "aliases": ["kolkata district"],
        "country_code": "IN",
        "admin_level": "district",
        "parent_id": "geo:west-bengal-in",
        "centroid": [88.3639, 22.5726],
        "bbox": [88.25, 22.50, 88.48, 22.65],
        "boundary_provider": "prudent-curated-preview",
        "source_dataset": "prudent-preview",
        "source_release": "v1",
        "boundary_version": "preview-v1",
        "license": "Preview geometry (original simplified shape); not authoritative.",
        "attribution": "Prudent preview geometry — approximate only",
        "approximate": True,
    },
    {
        "region_id": "geo:west-bengal-in",
        "display_name": "West Bengal, India",
        "aliases": ["west bengal"],
        "country_code": "IN",
        "admin_level": "state",
        "parent_id": "geo:india",
        "centroid": [87.8550, 22.9868],
        "bbox": [85.8, 21.5, 89.9, 27.2],
        "boundary_provider": "prudent-curated-preview",
        "source_dataset": "prudent-preview",
        "source_release": "v1",
        "boundary_version": "preview-v1",
        "license": "Preview geometry (original simplified shape); not authoritative.",
        "attribution": "Prudent preview geometry — approximate only",
        "approximate": True,
    },
    {
        "region_id": "geo:india",
        "display_name": "India",
        "aliases": ["india", "republic of india"],
        "country_code": "IN",
        "admin_level": "country",
        "parent_id": None,
        "centroid": [78.9629, 20.5937],
        "bbox": [68.0, 6.5, 97.5, 35.5],
        "boundary_provider": "natural-earth-preview",
        "source_dataset": "natural-earth",
        "source_release": "ne_110m_admin_0",
        "boundary_version": "ne110m-v5.1-preview",
        "license": "Natural Earth data is public domain.",
        "attribution": "Made with Natural Earth (public domain) — simplified overview",
        "approximate": True,
    },
    {
        "region_id": "geo:london-gb",
        "display_name": "London, England, United Kingdom",
        "aliases": ["london"],
        "country_code": "GB",
        "admin_level": "city",
        "parent_id": "geo:united-kingdom",
        "centroid": [-0.1276, 51.5074],
        "bbox": [-0.52, 51.28, 0.34, 51.69],
        "boundary_provider": "prudent-curated-preview",
        "source_dataset": "prudent-preview",
        "source_release": "v1",
        "boundary_version": "preview-v1",
        "license": "Preview geometry (original simplified shape); not authoritative.",
        "attribution": "Prudent preview geometry — approximate only",
        "approximate": True,
    },
    {
        "region_id": "geo:new-york-city-us",
        "display_name": "New York City, New York, United States",
        "aliases": ["new york", "new york city", "nyc"],
        "country_code": "US",
        "admin_level": "city",
        "parent_id": "geo:new-york-us",
        "centroid": [-74.0060, 40.7128],
        "bbox": [-74.26, 40.49, -73.70, 40.92],
        "boundary_provider": "prudent-curated-preview",
        "source_dataset": "prudent-preview",
        "source_release": "v1",
        "boundary_version": "preview-v1",
        "license": "Preview geometry (original simplified shape); not authoritative.",
        "attribution": "Prudent preview geometry — approximate only",
        "approximate": True,
    },
    {
        "region_id": "geo:paris-fr",
        "display_name": "Paris, France",
        "aliases": ["paris"],
        "country_code": "FR",
        "admin_level": "city",
        "parent_id": "geo:france",
        "centroid": [2.3522, 48.8566],
        "bbox": [2.22, 48.81, 2.47, 48.90],
        "boundary_provider": "prudent-curated-preview",
        "source_dataset": "prudent-preview",
        "source_release": "v1",
        "boundary_version": "preview-v1",
        "license": "Preview geometry (original simplified shape); not authoritative.",
        "attribution": "Prudent preview geometry — approximate only",
        "approximate": True,
    },
    {
        "region_id": "geo:sydney-au",
        "display_name": "Sydney, New South Wales, Australia",
        "aliases": ["sydney"],
        "country_code": "AU",
        "admin_level": "city",
        "parent_id": "geo:new-south-wales-au",
        "centroid": [151.2093, -33.8688],
        "bbox": [150.9, -34.1, 151.5, -33.6],
        "boundary_provider": "prudent-curated-preview",
        "source_dataset": "prudent-preview",
        "source_release": "v1",
        "boundary_version": "preview-v1",
        "license": "Preview geometry (original simplified shape); not authoritative.",
        "attribution": "Prudent preview geometry — approximate only",
        "approximate": True,
    },
    {
        "region_id": "geo:nairobi-ke",
        "display_name": "Nairobi, Kenya",
        "aliases": ["nairobi"],
        "country_code": "KE",
        "admin_level": "city",
        "parent_id": "geo:kenya",
        "centroid": [36.8219, -1.2921],
        "bbox": [36.65, -1.45, 37.10, -1.16],
        "boundary_provider": "prudent-curated-preview",
        "source_dataset": "prudent-preview",
        "source_release": "v1",
        "boundary_version": "preview-v1",
        "license": "Preview geometry (original simplified shape); not authoritative.",
        "attribution": "Prudent preview geometry — approximate only",
        "approximate": True,
    },
    {
        "region_id": "geo:sao-paulo-br",
        "display_name": "São Paulo, Brazil",
        "aliases": ["sao paulo", "são paulo"],
        "country_code": "BR",
        "admin_level": "city",
        "parent_id": "geo:brazil",
        "centroid": [-46.6333, -23.5558],
        "bbox": [-46.83, -23.70, -46.36, -23.39],
        "boundary_provider": "prudent-curated-preview",
        "source_dataset": "prudent-preview",
        "source_release": "v1",
        "boundary_version": "preview-v1",
        "license": "Preview geometry (original simplified shape); not authoritative.",
        "attribution": "Prudent preview geometry — approximate only",
        "approximate": True,
    },
]


def geometry_for(entry: Dict[str, Any]) -> Dict[str, Any]:
    lon, lat = entry["centroid"]
    bb = entry["bbox"]
    dx = max((bb[2] - bb[0]) / 2.0, 0.05)
    dy = max((bb[3] - bb[1]) / 2.0, 0.05)
    # Keep preview polygons small enough for point-scale climate sampling.
    dx = min(dx, 0.35)
    dy = min(dy, 0.30)
    return _preview_polygon(lon, lat, dx, dy)
