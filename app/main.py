"""FastAPI application: security headers, CORS, limits, lifecycle."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.db import DB
from app.routes import analyses, climate, geography, system
from app.services.jobs import JOBS

log = logging.getLogger("prudent.app")
MAX_BODY = 64 * 1024


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    logging.basicConfig(level=getattr(logging, s.LOG_LEVEL.upper(), logging.INFO))
    await DB.connect(s.MONGODB_URI, s.MONGODB_DB)
    await JOBS.start()
    log.info("startup env=%s db=%s", s.APP_ENV, DB.mode)
    yield
    await JOBS.stop()
    await DB.close()


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(title="Prudent API", version="0.1.0", lifespan=lifespan,
                  docs_url="/docs", openapi_url="/openapi.json")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=s.frontend_origin_list(),
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "Accept"],
        max_age=600,
    )

    @app.middleware("http")
    async def guards(request: Request, call_next):
        if request.method in ("POST", "PUT", "PATCH"):
            length = request.headers.get("content-length")
            if length and int(length) > MAX_BODY:
                return JSONResponse({"detail": "Request body too large."}, status_code=413)
        resp = await call_next(request)
        # Operational security headers (CSP tuned for API + docs).
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "no-referrer"
        return resp

    @app.exception_handler(Exception)
    async def _sanitized(request: Request, exc: Exception):  # noqa: ARG001
        log.exception("unhandled")
        return JSONResponse({"detail": "Internal error."}, status_code=500)

    app.include_router(system.router)
    app.include_router(geography.router)
    app.include_router(climate.router)
    app.include_router(analyses.router)
    return app


app = create_app()
