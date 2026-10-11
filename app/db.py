"""Database access: async PyMongo when reachable, in-memory fallback otherwise.

Collections + indexes documented in code (see ensure_indexes). All timestamps UTC.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

log = logging.getLogger("prudent.db")


class MemoryCollection:
    def __init__(self, name: str):
        self.name = name
        self.docs: List[Dict[str, Any]] = []

    async def find_one(self, filt: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        for d in self.docs:
            if all(d.get(k) == v for k, v in filt.items()):
                return dict(d)
        return None

    async def insert_one(self, doc: Dict[str, Any]):
        self.docs.append(dict(doc))
        class R: inserted_id = doc.get("_id")
        return R()

    async def update_one(self, filt, update, upsert=False):
        for d in self.docs:
            if all(d.get(k) == v for k, v in filt.items()):
                for k, v in update.get("$set", {}).items():
                    d[k] = v
                class R: matched_count = 1; modified_count = 1
                return R()
        if upsert:
            doc = dict(filt); doc.update(update.get("$set", {}))
            self.docs.append(doc)
            class R2: matched_count = 0; modified_count = 0
            return R2()
        class R3: matched_count = 0; modified_count = 0
        return R3()

    async def find(self, filt=None, sort=None, limit=0):
        filt = filt or {}
        rows = [d for d in self.docs if all(d.get(k) == v for k, v in filt.items())]
        if sort:
            for key, direction in reversed(sort):
                rows.sort(key=lambda d: d.get(key), reverse=(direction < 0))
        if limit:
            rows = rows[:limit]
        for r in rows:
            yield dict(r)


class Database:
    def __init__(self):
        self._client: Any = None
        self._db: Any = None
        self.mode = "memory"
        self.mem: Dict[str, MemoryCollection] = {}

    def collection(self, name: str):
        if self._db is not None:
            return self._db[name]
        if name not in self.mem:
            self.mem[name] = MemoryCollection(name)
        return self.mem[name]

    async def connect(self, uri: str, db_name: str) -> None:
        try:
            from pymongo import AsyncMongoClient  # type: ignore
            client = AsyncMongoClient(uri, serverSelectionTimeoutMS=2500)
            await client.admin.command("ping")
            self._client = client
            self._db = client[db_name]
            self.mode = "mongo"
            await self.ensure_indexes()
            log.info("db.connected mode=mongo db=%s", db_name)
        except Exception as e:
            log.warning("db.fallback-memory reason=%s", e)
            self._client = None
            self._db = None
            self.mode = "memory"

    async def ensure_indexes(self) -> None:
        if self._db is None:
            return
        # Purpose-documented indexes aligned with query patterns.
        await self._db["analysis_results"].create_index("cache_key", unique=True)  # idempotent cache lookup
        await self._db["analysis_results"].create_index([("region_id", 1), ("created_at", -1)])
        await self._db["analysis_jobs"].create_index("job_id", unique=True)  # stable job ids
        await self._db["analysis_jobs"].create_index([("status", 1), ("created_at", -1)])  # status polling
        await self._db["provenance_runs"].create_index("run_id", unique=True)  # immutable run records
        await self._db["geographic_regions"].create_index("region_id", unique=True)
        await self._db["annual_climate_metrics"].create_index([("region_id", 1), ("year", 1)])
        await self._db["climate_observations"].create_index([("region_id", 1), ("date", 1)])

    async def close(self) -> None:
        if self._client is not None:
            try:
                await self._client.close()
            except Exception:
                pass


DB = Database()
