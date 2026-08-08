"""
Startup / shutdown cleanup helpers.

* Wipes stale temp directories on boot.
* Marks jobs that were active at the time of a crash as FAILED so they are
  never stuck in DOWNLOADING / UPLOADING forever.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from database.database import Database
from database import queries
from services.storage import JobStorage
from utils.logging import get_logger

log = get_logger(__name__)


async def recover_stale_jobs(db: Database, storage: JobStorage, job_timeout: int) -> None:
    # Any job that was active longer than the timeout is considered stale.
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=job_timeout)
    stale = await queries.get_stale_jobs(db, cutoff)
    for job in stale:
        jid = str(job["id"])
        log.warning("recovering_stale_job", extra={"job": jid, "status": job["status"]})
        storage.cleanup_job(jid)
    count = await queries.reset_stale_jobs(db, cutoff)
    if count:
        log.info("stale_jobs_reset", extra={"count": count})


async def startup_cleanup(db: Database, storage: JobStorage, job_timeout: int) -> None:
    storage.cleanup_all_stale()
    try:
        await recover_stale_jobs(db, storage, job_timeout)
    except Exception as exc:  # never crash the bot on recovery failure
        log.error("stale_recovery_failed", extra={"error": str(exc)})
