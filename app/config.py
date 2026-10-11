"""Centralized validated configuration. Fails fast at startup on bad config."""

from __future__ import annotations

from functools import lru_cache
from typing import List

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    APP_ENV: str = "development"
    APP_NAME: str = "prudent-backend"
    API_V1_PREFIX: str = "/api/v1"
    MONGODB_URI: str = "mongodb://localhost:27017"
    MONGODB_DB: str = "prudent"
    FRONTEND_ORIGINS: str = "http://localhost:5173,http://localhost:3000"
    OPENMETEO_BASE_URL: str = "https://archive-api.open-meteo.com/v1/archive"
    OPENMETEO_TIMEOUT_S: float = 30.0
    NOMINATIM_BASE_URL: str = "https://nominatim.openstreetmap.org"
    GEOBOUNDARIES_BASE_URL: str = "https://www.geoboundaries.org/api/current"
    MAX_YEAR_SPAN: int = 60
    MIN_START_YEAR: int = 1940
    MAX_END_YEAR: int = 2025
    RATE_LIMIT_PER_MIN: int = 60
    JOB_TIMEOUT_S: int = 600
    JOB_CONCURRENCY: int = 2
    CACHE_TTL_DAYS: int = 90
    LOG_LEVEL: str = "INFO"

    @field_validator("MAX_YEAR_SPAN")
    @classmethod
    def _span_sane(cls, v: int) -> int:
        if v < 1 or v > 100:
            raise ValueError("MAX_YEAR_SPAN must be 1..100")
        return v

    def frontend_origin_list(self) -> List[str]:
        return [o.strip() for o in self.FRONTEND_ORIGINS.split(",") if o.strip()]

    @property
    def is_prod(self) -> bool:
        return self.APP_ENV.lower() == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
