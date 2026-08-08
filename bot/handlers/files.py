"""
Incoming file handler.

Validates size and type BEFORE any download, creates a job in PENDING state
and shows the file-action keyboard. Multiple files from the same user
accumulate into a batch.
"""
from __future__ import annotations

from pyrogram import Client, filters
from pyrogram.types import Message

from bot import messages as M
from bot.handlers.common import HandlerContext
from bot.keyboards import keyboards as kb
from core.media import extract_media_info
from core.validation import validate_file
from database import queries
from database.models import JobStatus
from utils.logging import get_logger

log = get_logger(__name__)

# How many files a single batch may contain.
MAX_BATCH_FILES = 20


def register(app: Client, ctx: HandlerContext) -> None:

    @app.on_message(filters.document | filters.photo | filters.video |
                    filters.audio | filters.animation | filters.voice |
                    filters.video_note)
    async def on_file(_client: Client, message: Message) -> None:
        try:
            uid = getattr(message.from_user, "id", None) if message.from_user else None
            cid = getattr(message.chat, "id", None) if message.chat else None
            log.info("handler_entry", extra={"handler": "on_file", "user_id": uid, "chat_id": cid})
        except Exception:
            pass

        if await ctx.rate_limited(message, "file_submit"):
            return
        user = await ctx.ensure_user(message)
        if user and user.get("is_banned"):
            await message.reply(M.ERR_BANNED)
            return
        if not user:
            return

        info = extract_media_info(message)
        if info is None:
            await message.reply(M.ERR_NOT_DOCUMENT)
            return

        result = validate_file(
            filename=info.filename,
            size=info.size,
            mime_type=info.mime_type,
            telegram_media_type=info.media_type,
            max_size=ctx.config.max_file_size,
        )

        if not result.ok:
            if result.reason == "too_large":
                await message.reply(M.ERR_TOO_LARGE.format(max_mb=ctx.config.max_file_size // (1024 * 1024)))
            elif result.reason == "archive":
                await message.reply(M.ERR_ARCHIVE)
            elif result.reason == "media":
                await message.reply(M.ERR_MEDIA)
            else:
                await message.reply(M.ERR_UNSUPPORTED)
            return

        # Admission: queue full or user already has an active job.
        jm = getattr(app, "job_manager", None)
        if jm is not None:
            admission = await jm.check_admission(user["user_id"])
            if not admission.accepted:
                if admission.reason == "queue_full":
                    await message.reply(M.ERR_QUEUE_FULL)
                else:
                    await message.reply(M.ERR_USER_BUSY)
                return

        # Persist the job in PENDING (instruction not yet supplied).
        job_id = await queries.create_job(
            ctx.db,
            user_id=user["user_id"],
            chat_id=info.chat_id,
            original_name=result.filename,
            file_size=result.size,
            file_id=info.file_id,
            file_ref=info.file_ref,
            request_msg_id=info.message_id,
        )

        # Accumulate into per-user pending batch state. If the user is
        # mid-instruction we start a fresh batch.
        state = await ctx.state.get_user_state(user["user_id"])
        if not state or state.get("kind") != "batch" or state.get("action"):
            state = {"kind": "batch", "items": []}
        items = state["items"]

        if len(items) >= MAX_BATCH_FILES:
            await message.reply(M.ERR_QUEUE_FULL)
            await queries.set_job_status(ctx.db, job_id, JobStatus.CANCELLED)
            return

        # Avoid duplicate for the same file in quick succession.
        if not any(it["file_id"] == info.file_id for it in items):
            items.append({
                "job_id": job_id,
                "filename": result.filename,
                "size": result.size,
                "file_id": info.file_id,
            })
        state["single"] = {"job_id": job_id, "filename": result.filename}
        state.pop("action", None)
        await ctx.state.set_user_state(user["user_id"], state)

        if len(items) >= 2:
            text = M.BATCH_RECEIVED.format(count=len(items))
            await message.reply(text, reply_markup=kb.batch_actions_keyboard())
        else:
            text = M.FILE_RECEIVED.format(
                name=result.filename,
                size=M.human_size(result.size),
                ext=result.extension or "—",
            )
            await message.reply(text, reply_markup=kb.file_actions_keyboard(job_id))
