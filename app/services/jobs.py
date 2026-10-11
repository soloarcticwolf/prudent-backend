"""Background job manager: bounded asyncio workers, persistent job docs."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, Dict, List, Optional

from app.config import get_settings
from app.db import DB
from app.utils.hashing import utcnow

log = logging.getLogger("prudent.jobs")

TERMINAL = {"completed", "completed_with_warnings", "failed", "cancelled"}


class JobManager:
    def __init__(self):
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._workers: List[asyncio.Task] = []
        self._running = False
        self._dedup: Dict[str, str] = {}  # request-fingerprint -> job_id

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        # Recreate the queue on every start so it is bound to the current
        # running loop (asyncio queues are loop-bound; a global queue created
        # under a previous loop breaks worker delivery after restarts/tests).
        self._queue = asyncio.Queue()
        n = max(1, get_settings().JOB_CONCURRENCY)
        for i in range(n):
            self._workers.append(asyncio.create_task(self._loop(f"worker-{i}")))
        await self._requeue_interrupted()
        log.info("jobs.start workers=%d", n)

    async def stop(self) -> None:
        self._running = False
        for w in self._workers:
            w.cancel()
        self._workers = []

    async def submit(self, request: Dict[str, Any], fingerprint: str) -> Dict[str, Any]:
        # Duplicate-job prevention: reuse active job for identical fingerprint.
        existing = self._dedup.get(fingerprint)
        if existing:
            doc = await DB.collection("analysis_jobs").find_one({"job_id": existing})
            if doc and doc.get("status") not in TERMINAL:
                return doc
        job_id = "job_" + uuid.uuid4().hex[:12]
        now = utcnow().isoformat()
        doc = {
            "job_id": job_id, "status": "queued", "progress": ["Queued"],
            "progress_pct": 5.0, "request": request, "fingerprint": fingerprint,
            "analysis_id": None, "warnings": [], "error": None,
            "created_at": now, "updated_at": now, "retries": 0,
        }
        await DB.collection("analysis_jobs").insert_one({"_id": job_id, **doc})
        self._dedup[fingerprint] = job_id
        await self._queue.put(job_id)
        return doc

    async def get(self, job_id: str) -> Optional[Dict[str, Any]]:
        doc = await DB.collection("analysis_jobs").find_one({"job_id": job_id})
        if doc:
            doc.pop("_id", None)
        return doc

    async def cancel(self, job_id: str) -> Optional[Dict[str, Any]]:
        doc = await self.get(job_id)
        if doc is None or doc["status"] in TERMINAL:
            return doc
        await DB.collection("analysis_jobs").update_one(
            {"job_id": job_id}, {"$set": {"status": "cancelled", "updated_at": utcnow().isoformat()}})
        return await self.get(job_id)

    async def _set(self, job_id: str, **fields: Any) -> None:
        fields["updated_at"] = utcnow().isoformat()
        await DB.collection("analysis_jobs").update_one({"job_id": job_id}, {"$set": fields})

    async def _requeue_interrupted(self) -> None:
        """Recovery: requeue jobs left queued/processing by a previous worker."""
        try:
            cursor = DB.collection("analysis_jobs").find()
            async for doc in cursor:  # type: ignore[union-attr]
                if doc.get("status") in ("queued", "processing"):
                    if doc.get("status") == "processing":
                        await self._set(doc["job_id"], status="queued",
                                        progress=[*doc.get("progress", []), "Requeued after restart"])
                    await self._queue.put(doc["job_id"])
        except Exception as e:
            log.warning("jobs.requeue-skipped reason=%s", e)

    async def _loop(self, name: str) -> None:
        from app.services.analysis import run_pipeline  # deferred to avoid cycles
        while self._running:
            try:
                job_id = await self._queue.get()
            except asyncio.CancelledError:
                return
            try:
                doc = await self.get(job_id)
                if doc is None or doc["status"] in TERMINAL:
                    continue
                await self._set(job_id, status="processing", progress=["Queued", "Processing"], progress_pct=15.0)
                req = doc["request"]
                progress: List[str] = ["Queued", "Processing"]
                result = await asyncio.wait_for(
                    run_pipeline(
                        req["region_id"], req["start_year"], req["end_year"],
                        req.get("hazard", "heatwave"),
                        req.get("baseline_start", 1991), req.get("baseline_end", 2020),
                        progress=progress,
                    ),
                    timeout=get_settings().JOB_TIMEOUT_S,
                )
                status = "completed_with_warnings" if result.get("warnings") else "completed"
                await self._set(job_id, status=status, progress=progress,
                                progress_pct=100.0, analysis_id=result["analysis_id"],
                                warnings=result.get("warnings", []))
            except asyncio.TimeoutError:
                log.warning("jobs.timeout job=%s", job_id)
                await self._set(job_id, status="failed", error="Analysis timed out. Try a shorter interval.")
            except asyncio.CancelledError:
                return
            except Exception as e:  # sanitized failure, no stack traces to clients
                log.exception("jobs.failed job=%s", job_id)
                msg = str(e)[:300] or "Analysis failed."
                await self._set(job_id, status="failed", error=msg)


JOBS = JobManager()
