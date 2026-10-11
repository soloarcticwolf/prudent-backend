"""Background worker entrypoint: `python -m app.workers.runner` (same-process asyncio)."""
from __future__ import annotations

import asyncio
import logging

from app.config import get_settings
from app.db import DB
from app.services.jobs import JOBS

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("prudent.worker")


async def main() -> None:
    s = get_settings()
    await DB.connect(s.MONGODB_URI, s.MONGODB_DB)
    await JOBS.start()
    log.info("worker running (db=%s). Ctrl-C to stop.", DB.mode)
    try:
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        await JOBS.stop()
        await DB.close()


if __name__ == "__main__":
    asyncio.run(main())
