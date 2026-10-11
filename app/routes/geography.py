from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.schemas import BoundaryResponse, SearchResult
from app.services.geography import boundary_for, capabilities, resolve_region, search_regions

router = APIRouter(prefix="/api/v1/geography", tags=["geography"])


@router.get("/search", response_model=list[SearchResult])
async def search(q: str = Query(min_length=2, max_length=100), limit: int = Query(default=8, ge=1, le=20)):
    return await search_regions(q, limit)


@router.get("/regions/{region_id}", response_model=SearchResult)
async def region_detail(region_id: str):
    r = await resolve_region(region_id)
    if r is None:
        raise HTTPException(404, "unknown region_id; search first and use a canonical region_id")
    r = dict(r); r.pop("aliases", None)
    return r


@router.get("/regions/{region_id}/boundary", response_model=BoundaryResponse)
async def region_boundary(region_id: str):
    b = await boundary_for(region_id)
    if b is None:
        raise HTTPException(404, "A verified boundary is not available for this location.")
    return b


@router.get("/metadata")
async def metadata():
    return capabilities()
