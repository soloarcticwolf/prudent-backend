from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.db import DB
from app.schemas import AnalysisRequest, JobAccepted, JobStatus
from app.services.analysis import build_cache_key, validate_request
from app.services.geography import resolve_region
from app.services.jobs import JOBS
from app.utils.hashing import sha256_hex

router = APIRouter(prefix="/api/v1", tags=["analyses"])

# Simple in-process rate limiting for expensive endpoints (per deployment relies on reverse proxy too).
_hits: dict[str, list[float]] = {}


def _limited(key: str, per_min: int) -> bool:
    import time
    now = time.time()
    arr = _hits.setdefault(key, [])
    arr[:] = [t for t in arr if now - t < 60]
    if len(arr) >= per_min:
        return True
    arr.append(now)
    return False


@router.post("/analyses", response_model=JobAccepted, status_code=202)
async def submit_analysis(body: AnalysisRequest, request: Request):
    from app.config import get_settings
    client = request.client.host if request.client else "unknown"
    if _limited(f"analyses:{client}", get_settings().RATE_LIMIT_PER_MIN):
        raise HTTPException(429, "Rate limit exceeded. Try again shortly.")
    errors = await validate_request(body.region_id, body.start_year, body.end_year, body.hazard)
    if errors:
        raise HTTPException(422, "; ".join(errors))
    region = await resolve_region(body.region_id)
    assert region is not None
    params = {
        "region_id": body.region_id,
        "boundary_version": region["boundary_version"],
        "geometry_fingerprint": region.get("geometry_fingerprint"),
        "start_year": body.start_year, "end_year": body.end_year,
        "baseline_start": body.baseline_start, "baseline_end": body.baseline_end,
        "hazard": body.hazard,
    }
    cache_key = build_cache_key(params)
    cached = await DB.collection("analysis_results").find_one({"cache_key": cache_key})
    if cached:
        res = cached["result"]
        fp = sha256_hex({"cache_key": cache_key})
        job = await JOBS.submit(
            {"region_id": body.region_id, "start_year": body.start_year,
             "end_year": body.end_year, "hazard": body.hazard,
             "baseline_start": body.baseline_start, "baseline_end": body.baseline_end,
             "cache_hit": True, "analysis_id": res["analysis_id"]}, fp + ":hit")
        # Mark immediately completed for cache hits.
        await DB.collection("analysis_jobs").update_one(
            {"job_id": job["job_id"]},
            {"$set": {"status": "completed", "progress": ["Queued", "Cache hit"],
                      "progress_pct": 100.0, "analysis_id": res["analysis_id"]}})
        return JobAccepted(status="completed", job_id=job["job_id"],
                           analysis_id=res["analysis_id"], cache_hit=True,
                           message="Compatible cached result returned.")
    fp = sha256_hex(params)
    job = await JOBS.submit(
        {"region_id": body.region_id, "start_year": body.start_year, "end_year": body.end_year,
         "hazard": body.hazard, "baseline_start": body.baseline_start, "baseline_end": body.baseline_end}, fp)
    return JobAccepted(status="queued", job_id=job["job_id"], cache_hit=False,
                       message="Analysis queued. Poll the job endpoint for progress.")


@router.get("/analyses/{analysis_id}")
async def get_analysis(analysis_id: str):
    doc = await DB.collection("analysis_results").find_one({"_id": analysis_id})
    if doc is None:
        # Fallback: lookup by analysis_id inside result (memory docs).
        async for d in DB.collection("analysis_results").find():
            if isinstance(d.get("result"), dict) and d["result"].get("analysis_id") == analysis_id:
                doc = d; break
    if doc is None:
        raise HTTPException(404, "analysis not found")
    return doc["result"]


@router.get("/jobs/{job_id}", response_model=JobStatus)
async def job_status(job_id: str):
    doc = await JOBS.get(job_id)
    if doc is None:
        raise HTTPException(404, "job not found")
    return JobStatus(**{k: doc.get(k) for k in JobStatus.model_fields})


@router.post("/jobs/{job_id}/cancel", response_model=JobStatus)
async def job_cancel(job_id: str):
    doc = await JOBS.cancel(job_id)
    if doc is None:
        raise HTTPException(404, "job not found")
    return JobStatus(**{k: doc.get(k) for k in JobStatus.model_fields})
