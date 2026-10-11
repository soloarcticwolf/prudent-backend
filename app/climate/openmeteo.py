"""Open-Meteo archive adapter with bounded retries, timeouts, validation."""

from __future__ import annotations

import asyncio
import random
import time
from datetime import date
from typing import Any, Dict, List, Optional

import httpx

from app.config import get_settings

DATASET_VERSION = "openmeteo-archive-v1"
PROVIDER_ID = "open-meteo"

# Provider rate-limit compliance: the free archive API enforces a minutely
# request budget. All fetches share this throttle (min gap between upstream
# calls) and honor 429 Retry-After instead of hammering retries.
_MIN_GAP_S = 7.0
_om_lock = asyncio.Lock()
_last_call_ts = 0.0


async def _throttled_get(client: httpx.AsyncClient, url: str, params: Dict[str, Any]) -> httpx.Response:
    global _last_call_ts
    async with _om_lock:
        wait = _MIN_GAP_S - (time.monotonic() - _last_call_ts)
        if wait > 0:
            await asyncio.sleep(wait)
        try:
            return await client.get(url, params=params)
        finally:
            _last_call_ts = time.monotonic()


async def fetch_daily_tmax(
    lat: float,
    lon: float,
    start: date,
    end: date,
    client: Optional[httpx.AsyncClient] = None,
) -> Dict[str, Any]:
    s = get_settings()
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "daily": "temperature_2m_max",
        "timezone": "UTC",
    }
    own = client is None
    if own:
        client = httpx.AsyncClient(timeout=s.OPENMETEO_TIMEOUT_S, follow_redirects=False)
    assert client is not None
    last_err: Exception | None = None
    for attempt in range(4):
        try:
            r = await _throttled_get(client, s.OPENMETEO_BASE_URL, params=params)
            r.raise_for_status()
            if len(r.content) > 8_000_000:
                raise ValueError("upstream response too large")
            data = r.json()
            daily = data.get("daily") or {}
            times = daily.get("time") or []
            vals = daily.get("temperature_2m_max") or []
            if len(times) != len(vals):
                raise ValueError("mismatched time/value arrays from provider")
            series = []
            rejected = 0
            for t, v in zip(times, vals):
                try:
                    d = date.fromisoformat(t[:10])
                except ValueError:
                    rejected += 1
                    continue
                series.append({"date": d.isoformat(), "tmax_c": None if v is None else float(v)})
            return {
                "provider": PROVIDER_ID,
                "dataset_version": DATASET_VERSION,
                "latitude": lat, "longitude": lon,
                "start": start.isoformat(), "end": end.isoformat(),
                "count": len(series), "rejected": rejected, "series": series,
                "units": "celsius",
                "fields_consumed": ["temperature_2m_max"],
            }
        except httpx.HTTPStatusError as e:
            last_err = e
            if e.response.status_code == 429:
                # Minutely budget exceeded: back off for the provider's
                # requested window instead of retrying into the limit.
                retry_after = e.response.headers.get("retry-after")
                try:
                    delay = max(float(retry_after), 60.0) if retry_after else 65.0
                except ValueError:
                    delay = 65.0
                await asyncio.sleep(delay + random.uniform(0, 5))
            else:
                await asyncio.sleep((0.6 * (2 ** attempt)) + random.uniform(0, 0.4))
        except Exception as e:  # bounded retry with jitter
            last_err = e
            await asyncio.sleep((0.6 * (2 ** attempt)) + random.uniform(0, 0.4))
    raise RuntimeError(f"open-meteo fetch failed after retries: {last_err}")


def sample_points(bbox: List[float], centroid: List[float], max_points: int = 5) -> List[List[float]]:
    """Small deterministic sample inside bbox for regional estimation.

    Returns [lon, lat] points: centroid + up to 4 interior offsets.
    Documented as regional estimation; coarse-resolution limitation disclosed.
    """
    lon, lat = centroid[0], centroid[1]
    minx, miny, maxx, maxy = bbox
    dx = (maxx - minx) * 0.22
    dy = (maxy - miny) * 0.22
    pts = [[lon, lat]]
    for ox, oy in [(-dx, -dy), (dx, -dy), (-dx, dy), (dx, dy)]:
        if len(pts) >= max_points:
            break
        px, py = lon + ox, lat + oy
        if minx <= px <= maxx and miny <= py <= maxy:
            pts.append([px, py])
    return pts


def area_weighted_mean(series_list: List[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Equal-weight mean across sampled grid points per date (documented)."""
    by_date: Dict[str, List[float]] = {}
    for series in series_list:
        for row in series:
            if row.get("tmax_c") is None:
                continue
            by_date.setdefault(row["date"], []).append(float(row["tmax_c"]))
    out = []
    for d in sorted(by_date):
        vals = by_date[d]
        out.append({"date": d, "tmax_c": round(sum(vals) / len(vals), 2), "n_points": len(vals)})
    return out
