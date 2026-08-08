"""
Job processor / worker.

A single background consumer loop pulls job ids off the Redis queue. Each job
acquires bounded concurrency slots, streams the file to a unique temp
directory, renames on the filesystem, streams it back to Telegram, and always
cleans up. Every failure is caught and recorded; a bad job never crashes the
worker or the bot.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Optional

import pyrogram
from pyrogram.errors import RPCError, FloodWait
from pyrogram.types import Message

from config import Config
from bot import messages as M
from database.database import Database
from database import queries
from database.models import JobStatus
from services.jobs import JobManager
from services.state import StateStore
from services.storage import JobStorage, StorageError
from utils.logging import get_logger
from utils.retry import CancellationError, retry_async

log = get_logger(__name__)

# How often (in percent) progress messages are edited during transfer.
PROGRESS_STEP = 15


class JobProcessor:
    def __init__(
        self,
        app: "pyrogram.Client",
        db: Database,
        state: StateStore,
        jobs: JobManager,
        storage: JobStorage,
        config: Config,
    ) -> None:
        self.app = app
        self.db = db
        self.state = state
        self.jobs = jobs
        self.storage = storage
        self.config = config
        self._task: Optional[asyncio.Task] = None
        self._stopping = False

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="job-processor")
            self.jobs.track_task(self._task)

    async def stop(self) -> None:
        self._stopping = True
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass

    # ──────────────────────────────────────────────────────────────────
    # Consumer loop
    # ──────────────────────────────────────────────────────────────────
    async def _run(self) -> None:
        log.info("processor_started")
        while not self._stopping:
            job_id = None
            try:
                job_id = await self.state.dequeue_job(timeout=5)
                if not job_id:
                    continue
                await self._process_with_slots(job_id)
            except asyncio.CancelledError:
                break
            except Exception as exc:  # one bad job must not kill the loop
                log.error("processor_loop_error", extra={"error": str(exc)})
                if job_id:
                    try:
                        await self._mark_failed(job_id, f"internal error: {type(exc).__name__}")
                        self.storage.cleanup_job(job_id)
                    except Exception:
                        pass
                await asyncio.sleep(1)
        log.info("processor_stopped")

    async def _process_with_slots(self, job_id: str) -> None:
        job = await queries.get_job(self.db, job_id)
        if not job:
            return
        if job["status"] in (JobStatus.CANCELLED.value, JobStatus.COMPLETED.value):
            return

        user_id = int(job["user_id"])
        await self.jobs.acquire_slots(user_id)
        try:
            await self._process_job(job_id)
        finally:
            self.jobs.release_slots(user_id)
            self.storage.cleanup_job(job_id)
            await self.state.clear_cancel(job_id)

    # ──────────────────────────────────────────────────────────────────
    # Per-job pipeline
    # ──────────────────────────────────────────────────────────────────
    async def _process_job(self, job_id: str) -> None:
        job = await queries.get_job(self.db, job_id)
        if not job:
            return
        user_id = int(job["user_id"])
        chat_id = int(job["chat_id"])

        try:
            await self.state.clear_cancel(job_id)

            # 1) Disk guard
            if not self.storage.ensure_disk_space(int(job["file_size"])):
                await self._fail(job_id, M.ERR_NO_DISK, "no disk space", user_id=user_id)
                return

            job_dir = self.storage.create_job_dir(job_id)
            original_name = job["original_name"]
            new_name = job["new_name"] or original_name
            file_id = job["file_id"]

            if not file_id:
                await self._fail(job_id, M.ERR_GENERIC, "missing file_id", user_id=user_id)
                return

            # 2) Download (streamed to disk by Pyrogram). We pass the job
            #    DIRECTORY and let Pyrogram pick the filename inside it, then
            #    resolve the actual path from the returned value. This avoids
            #    path-handling differences across Pyrogram versions.
            await self._set_status(job_id, JobStatus.DOWNLOADING)
            await self._edit_status(chat_id, job, M.STATUS_DOWNLOADING.format(percent=0))

            last_pct = {"v": -1}

            async def dl_progress(current: int, total: int) -> None:
                if await self.state.is_cancelled(job_id):
                    raise CancellationError()
                if total:
                    pct = int(current * 100 / total)
                    if pct - last_pct["v"] >= PROGRESS_STEP or pct == 100:
                        last_pct["v"] = pct
                        await self._edit_status(
                            chat_id, job, M.STATUS_DOWNLOADING.format(percent=pct)
                        )

            async def _do_download():
                return await self.app.download_media(
                    file_id,
                    file_name=str(job_dir) + os.sep,
                    progress=dl_progress,
                )

            downloaded = await retry_async(
                _do_download,
                max_retries=self.config.max_retries,
                is_cancelled=lambda: self.state.is_cancelled(job_id),
            )

            # Resolve the actual file on disk.
            dl_path: Optional[Path] = None
            if downloaded:
                candidate = Path(downloaded)
                if candidate.is_file():
                    dl_path = candidate
                elif candidate.is_dir():
                    files = [p for p in candidate.iterdir() if p.is_file()]
                    if files:
                        dl_path = files[0]
            if dl_path is None:
                # Fallback: look for the only file in the job directory.
                files = [p for p in job_dir.iterdir() if p.is_file()] if job_dir.exists() else []
                if len(files) == 1:
                    dl_path = files[0]
            if dl_path is None or not dl_path.is_file():
                await self._fail(job_id, M.ERR_GENERIC, "download produced no file",
                                 user_id=user_id)
                return

            ok, actual_size = self.storage.verify_file(dl_path, int(job["file_size"]))
            if not ok:
                await self._fail(job_id, M.ERR_GENERIC, "download verification failed",
                                 user_id=user_id)
                return

            if await self.state.is_cancelled(job_id):
                raise CancellationError()

            # 3) Rename on filesystem
            await self._set_status(job_id, JobStatus.RENAMING)
            await self._edit_status(chat_id, job, M.STATUS_RENAMING)
            final_path = self.storage.perform_filesystem_rename(dl_path, new_name)

            if await self.state.is_cancelled(job_id):
                raise CancellationError()

            # 4) Upload (streamed from disk)
            await self._set_status(job_id, JobStatus.UPLOADING)
            await self._edit_status(chat_id, job, M.STATUS_UPLOADING.format(percent=0))
            up_last = {"v": -1}
            total_up = final_path.stat().st_size

            async def up_progress(current: int, total: int) -> None:
                if await self.state.is_cancelled(job_id):
                    raise CancellationError()
                pct = int(current * 100 / (total or total_up or 1))
                if pct - up_last["v"] >= PROGRESS_STEP or pct == 100:
                    up_last["v"] = pct
                    await self._edit_status(
                        chat_id, job, M.STATUS_UPLOADING.format(percent=pct)
                    )

            async def _do_upload():
                return await self.app.send_document(
                    chat_id=chat_id,
                    document=str(final_path),
                    file_name=new_name,
                    caption=f"✅ <code>{new_name}</code>",
                    progress=up_progress,
                    force_document=True,
                )

            sent: Message = await retry_async(
                _do_upload,
                max_retries=self.config.max_retries,
                is_cancelled=lambda: self.state.is_cancelled(job_id),
            )

            # 5) Complete
            await self._set_status(
                job_id, JobStatus.COMPLETED,
                new_name=new_name,
                result_msg_id=sent.id if sent else None,
            )
            await queries.add_history(
                self.db, user_id=user_id, job_id=job_id,
                operation=job["operation"], original_name=original_name,
                new_name=new_name, file_size=actual_size,
                status=JobStatus.COMPLETED.value,
            )

            # Delete the status message; the sent document is the final output.
            await self._delete_status(chat_id, job)

        except CancellationError:
            await self._mark_cancelled(job_id, user_id=int(job["user_id"]), chat_id=chat_id, job=job)
        except FloodWait as fw:
            wait = int(getattr(fw, "value", 5))
            log.warning("flood_wait", extra={"job": job_id, "wait": wait})
            await self._fail(job_id, M.ERR_GENERIC, f"flood wait {wait}s",
                             user_id=int(job["user_id"]), chat_id=chat_id, job=job)
        except RPCError as exc:
            log.error("rpc_error", extra={"job": job_id, "error": str(exc)})
            await self._fail(job_id, M.ERR_GENERIC, f"telegram error: {type(exc).__name__}",
                             user_id=int(job["user_id"]), chat_id=chat_id, job=job)
        except (OSError, StorageError) as exc:
            log.error("io_error", extra={"job": job_id, "error": str(exc)})
            await self._fail(job_id, M.ERR_GENERIC, "storage error",
                             user_id=int(job["user_id"]), chat_id=chat_id, job=job)
        except Exception as exc:  # ultimate safety net
            log.error("job_failed", extra={"job": job_id, "error": str(exc)})
            await self._fail(job_id, M.ERR_GENERIC, type(exc).__name__,
                             user_id=int(job["user_id"]), chat_id=chat_id, job=job)

    # ──────────────────────────────────────────────────────────────────
    # Status / DB helpers
    # ──────────────────────────────────────────────────────────────────
    async def _set_status(self, job_id: str, status: JobStatus, **kw) -> None:
        try:
            await queries.set_job_status(self.db, job_id, status, increment_attempts=(status == JobStatus.DOWNLOADING), **kw)
        except Exception as exc:
            log.warning("status_update_failed", extra={"job": job_id, "error": str(exc)})

    async def _edit_status(self, chat_id: int, job: dict, text: str) -> None:
        msg_id = job.get("status_msg_id")
        if not msg_id:
            return
        try:
            await self.app.edit_message_text(chat_id, msg_id, text)
        except Exception:
            pass

    async def _delete_status(self, chat_id: int, job: dict) -> None:
        msg_id = job.get("status_msg_id")
        if not msg_id:
            return
        try:
            await self.app.delete_messages(chat_id, msg_id)
        except Exception:
            pass

    async def _fail(self, job_id: str, user_text: str, internal_reason: str,
                    *, user_id: int, chat_id: Optional[int] = None,
                    job: Optional[dict] = None) -> None:
        await self._mark_failed(job_id, internal_reason)
        await queries.add_history(
            self.db, user_id=user_id, job_id=job_id,
            operation=(job or {}).get("operation", "rename"),
            original_name=(job or {}).get("original_name", ""),
            new_name=(job or {}).get("new_name", ""),
            file_size=int((job or {}).get("file_size", 0) or 0),
            status=JobStatus.FAILED.value,
        )
        if chat_id and job and job.get("status_msg_id"):
            try:
                await self.app.edit_message_text(
                    chat_id, job["status_msg_id"],
                    M.JOB_FAILED.format(reason=internal_reason[:80]),
                )
            except Exception:
                pass

    async def _mark_failed(self, job_id: str, reason: str) -> None:
        try:
            await queries.set_job_status(self.db, job_id, JobStatus.FAILED, error=reason[:500])
        except Exception as exc:
            log.error("mark_failed_db_error", extra={"job": job_id, "error": str(exc)})

    async def _mark_cancelled(self, job_id: str, *, user_id: int, chat_id: int, job: dict) -> None:
        try:
            await queries.set_job_status(self.db, job_id, JobStatus.CANCELLED, error="cancelled")
            await queries.add_history(
                self.db, user_id=user_id, job_id=job_id,
                operation=job.get("operation", "rename"),
                original_name=job.get("original_name", ""),
                new_name=job.get("new_name", ""),
                file_size=int(job.get("file_size", 0) or 0),
                status=JobStatus.CANCELLED.value,
            )
            if job.get("status_msg_id"):
                await self.app.edit_message_text(chat_id, job["status_msg_id"], M.JOB_CANCELLED)
        except Exception as exc:
            log.error("mark_cancelled_failed", extra={"job": job_id, "error": str(exc)})
