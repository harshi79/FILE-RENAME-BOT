"""
Job orchestration.

Responsibilities:
* admission control against global / per-user concurrency limits
* queue-full / user-busy decisions
* cancellation of queued or active jobs
* in-memory bounded semaphores to keep the Python process within RAM limits

The real download/rename/upload work is performed by workers/processor.py.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Optional

from config import Config
from database.database import Database
from database import queries
from database.models import JobStatus
from services.state import StateStore
from utils.logging import get_logger

log = get_logger(__name__)


@dataclass
class AdmissionResult:
    accepted: bool
    reason: str = ""  # "queue_full" | "user_busy" | ""
    queue_position: int = 0


class JobManager:
    def __init__(self, state: StateStore, db: Database, config: Config) -> None:
        self._state = state
        self._db = db
        self._config = config
        # Bounded in-process signals (free tier runs a single instance).
        self._global_slots = asyncio.Semaphore(config.max_global_active_jobs)
        self._user_slots: dict[int, asyncio.Semaphore] = {}
        self._user_lock = asyncio.Lock()
        # Track active task objects so they can be cancelled on shutdown.
        self._tasks: set[asyncio.Task] = set()

    async def _user_semaphore(self, user_id: int) -> asyncio.Semaphore:
        async with self._user_lock:
            sem = self._user_slots.get(user_id)
            if sem is None:
                sem = asyncio.Semaphore(self._config.max_active_jobs_per_user)
                self._user_slots[user_id] = sem
            return sem

    async def check_admission(self, user_id: int) -> AdmissionResult:
        """Decide whether a new job can be accepted right now."""
        queue_len = await self._state.queue_length()
        if queue_len >= self._config.max_queue_size:
            return AdmissionResult(False, "queue_full", queue_len)

        user_active = await self._state.user_active_count(user_id)
        if user_active >= self._config.max_active_jobs_per_user:
            return AdmissionResult(False, "user_busy", user_active)

        # Also consult the DB as a safety net in case Redis lost state.
        try:
            db_active = await queries.count_user_active_jobs(self._db, user_id)
            if db_active >= self._config.max_active_jobs_per_user:
                return AdmissionResult(False, "user_busy", db_active)
        except Exception as exc:
            log.warning("db_admission_check_failed", extra={"error": str(exc)})

        return AdmissionResult(True, "", queue_len + 1)

    async def acquire_slots(self, user_id: int) -> None:
        """Block until both global and per-user slots are free."""
        user_sem = await self._user_semaphore(user_id)
        # Acquire global first then user to keep ordering consistent.
        await self._global_slots.acquire()
        try:
            await user_sem.acquire()
        except Exception:
            self._global_slots.release()
            raise
        await self._state.incr_active(user_id)

    def release_slots(self, user_id: int) -> None:
        self._global_slots.release()
        sem = self._user_slots.get(user_id)
        if sem is not None:
            sem.release()
        # Decrement Redis counter best-effort.
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._state.decr_active(user_id))
        except RuntimeError:
            pass

    def track_task(self, task: asyncio.Task) -> None:
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def cancel_job(self, job_id: str, user_id: Optional[int] = None) -> str:
        """
        Cancel a queued job immediately, or signal an active job to stop.
        Returns one of: "queued" | "active" | "not_found".
        """
        job = await queries.get_job(self._db, job_id)
        if not job:
            return "not_found"
        if user_id is not None and int(job["user_id"]) != user_id and not self._config.is_admin(user_id):
            return "not_found"

        status = job["status"]
        if status == JobStatus.QUEUED.value or status == JobStatus.PENDING.value:
            removed = await self._state.remove_from_queue(job_id)
            await queries.set_job_status(
                self._db, job_id, JobStatus.CANCELLED,
                error="cancelled by user" if removed == 0 else None,
            )
            await self._state.clear_cancel(job_id)
            await queries.add_history(
                self._db, user_id=int(job["user_id"]), job_id=job_id,
                operation=job["operation"], original_name=job["original_name"],
                new_name=job["new_name"] or "", file_size=int(job["file_size"]),
                status=JobStatus.CANCELLED.value,
            )
            return "queued"

        if status in {JobStatus.DOWNLOADING.value, JobStatus.RENAMING.value,
                      JobStatus.UPLOADING.value, JobStatus.CLEANING.value}:
            await self._state.request_cancel(job_id)
            return "active"

        return "not_found"

    async def shutdown(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
