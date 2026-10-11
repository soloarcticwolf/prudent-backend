from __future__ import annotations

from fastapi import APIRouter

from app.config import get_settings
from app.db import DB

router = APIRouter(tags=["system"])


@router.get("/health")
async def health() -> dict:
    return {"status": "ok", "app": get_settings().APP_NAME}


@router.get("/ready")
async def ready() -> dict:
    # No credentials or infra details exposed.
    return {"ready": True, "db_mode": DB.mode, "jobs": "running"}
