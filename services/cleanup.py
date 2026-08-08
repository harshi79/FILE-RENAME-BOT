"""
Startup / shutdown cleanup helpers.

* Wipes stale temp directories on boot.
* Marks jobs that were actively processing at crash time as FAILED so they are
  never stuck in DOWNLOADING / UPLOADING forever.
* Re-enqueues QUEUED jobs that are not already in the Redis queue, so a Redis
  flush / restart does not orphan work (PostgreSQL is the source of truth).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from database.database import Database
from database import queries
from services.storage import JobStorage
from services.state import StateStore
from utils.logging import get_logger

log = get_logger(__name__)


async def _recover_queued_jobs(db: Database, state: StateStore) -> None:
    """Re-push any QUEUED jobs missing from the Redis queue."""
    try:
        queued = await queries.list_queued_jobs(db, limit=500)
    except Exception as exc:
        log.warning("recover_queued_query_failed", extra={"error": str(exc)})
        return

    requeued = 0
    for job in queued:
        jid = str(job["id"])
        # Avoid duplicate enqueues: remove first then push (idempotent).
        await state.remove_from_queue(jid)
        if await state.enqueue_job(jid):
            requeued += 1
    if requeued:
        log.info("requeued_orphan_jobs", extra={"count": requeued})


async def recover_stale_jobs(db: Database, storage: JobStorage, job_timeout: int) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=job_timeout)
    stale = await queries.get_stale_jobs(db, cutoff)
    for job in stale:
        jid = str(job["id"])
        log.warning("recovering_stale_job", extra={"job": jid, "status": job["status"]})
        storage.cleanup_job(jid)
    count = await queries.reset_stale_jobs(db, cutoff)
    if count:
        log.info("stale_jobs_reset", extra={"count": count})
    return count


async def startup_cleanup(db: Database, state: StateStore, storage: JobStorage,
                          job_timeout: int) -> None:
    storage.cleanup_all_stale()
    try:
        await recover_stale_jobs(db, storage, job_timeout)
    except Exception as exc:  # never crash the bot on recovery failure
        log.error("stale_recovery_failed", extra={"error": str(exc)})
    try:
        await _recover_queued_jobs(db, state)
    except Exception as exc:
        log.error("queue_recovery_failed", extra={"error": str(exc)})
