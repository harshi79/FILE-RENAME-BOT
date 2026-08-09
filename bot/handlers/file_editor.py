"""
Reply-based File Editor command handlers: /paste, /replace, /detail.

Every command targets a file by REPLYING to its document message; users never
paste file ids and never re-upload the file. The replied-to document is
resolved, validated with the existing validation rules, downloaded into a
fresh UUID temp directory (reusing services.storage), processed with bounded
streaming logic from core.file_editor, and — for /replace — re-uploaded as a
separate edited copy. ALL temp files are removed in ``finally``.

No Redis, no new queues, no new dependencies: concurrency is bounded by a
module-level semaphore and the existing per-user rate limiter.
"""
from __future__ import annotations

import asyncio
import html
import os
import shutil
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

from pyrogram import Client, filters
from pyrogram.types import Message

from bot import messages as M
from bot.handlers.common import HandlerContext
from core import file_editor as ed
from core.media import MediaInfo, extract_media_info
from core.validation import ValidationResult, validate_file
from services.storage import JobStorage
from utils.logging import get_logger
from utils.retry import retry_async

log = get_logger(__name__)

# Bounded concurrency for editor operations (download + process + upload).
# The bot runs on a ~512 MB instance, so at most two of these at a time.
_EDITOR_SLOTS = asyncio.Semaphore(2)


# ──────────────────────────────────────────────────────────────────────────
# Shared flow helpers
# ──────────────────────────────────────────────────────────────────────────
def _log_entry(handler: str, message: Message) -> None:
    try:
        uid = getattr(message.from_user, "id", None) if message.from_user else None
        cid = getattr(message.chat, "id", None) if message.chat else None
        log.info("handler_entry", extra={"handler": handler, "user_id": uid, "chat_id": cid})
    except Exception:
        pass


def _command_arg(message: Message, idx: int) -> str:
    """Return the ``idx``-th argument token from the command's first line."""
    first = (message.text or message.caption or "").partition("\n")[0]
    parts = first.split()
    return parts[idx + 1] if len(parts) > idx + 1 else ""


def editor_unsupported_text(result: ValidationResult, ctx: HandlerContext) -> str:
    """Clear small-caps rejection for files the editor cannot process."""
    if result.reason == "too_large":
        return M.ERR_TOO_LARGE.format(max_mb=ctx.config.max_file_size // (1024 * 1024))
    return M.EDITOR_UNSUPPORTED


async def editor_preflight(ctx: HandlerContext, message: Message) -> Tuple[Dict[str, Any], MediaInfo]:
    """
    Resolve the replied-to document and validate it with the EXISTING rules.

    Raises ed.EditorError with a ready-to-send reply (empty string when the
    rejection was already replied, e.g. a rate-limit response).
    """
    user = await ctx.ensure_user(message)
    if user and user.get("is_banned"):
        raise ed.EditorError(M.ERR_BANNED)
    if not user:
        raise ed.EditorError(M.ERR_GENERIC)
    if await ctx.rate_limited(message, "rename"):
        raise ed.EditorError("")

    reply = message.reply_to_message
    if reply is None:
        raise ed.EditorError(M.EDITOR_NO_REPLY)
    info = extract_media_info(reply)
    if info is None:
        raise ed.EditorError(M.EDITOR_NO_REPLY)

    result = validate_file(
        filename=info.filename,
        size=info.size,
        mime_type=info.mime_type,
        telegram_media_type=info.media_type,
        max_size=ctx.config.max_file_size,
    )
    if not result.ok:
        raise ed.EditorError(editor_unsupported_text(result, ctx))
    return user, info


def _resolve_downloaded(job_dir: Path, downloaded: Optional[str]) -> Optional[Path]:
    """Find the actual file Pyrogram produced inside the temp directory."""
    if downloaded:
        candidate = Path(downloaded)
        if candidate.is_file():
            return candidate
        if candidate.is_dir():
            files = [p for p in candidate.iterdir() if p.is_file()]
            if files:
                return max(files, key=lambda p: p.stat().st_size)
    if job_dir.exists():
        files = [p for p in job_dir.iterdir() if p.is_file()]
        if files:
            return max(files, key=lambda p: p.stat().st_size)
    return None


async def editor_download_and_run(
    app: Client,
    ctx: HandlerContext,
    info: MediaInfo,
    operation: Callable[[Path, Path], Any],
) -> Any:
    """
    Download ``info`` into a fresh UUID temp dir, run ``operation(dl_path,
    job_dir)``, and ALWAYS remove every temp file in ``finally``.

    Reuses the existing JobStorage (disk guard, UUID dirs, verification,
    cleanup) — the same safe handling the rename pipeline uses.
    """
    storage = getattr(app, "job_storage", None)
    tmp_id = str(uuid.uuid4())
    job_dir: Optional[Path] = None
    try:
        if isinstance(storage, JobStorage):
            if not storage.ensure_disk_space(info.size):
                raise ed.EditorError(M.ERR_NO_DISK)
            job_dir = storage.create_job_dir(tmp_id)
        else:
            job_dir = Path(ctx.config.temp_dir) / tmp_id
            job_dir.mkdir(parents=True, exist_ok=True)

        async def _download():
            return await app.download_media(info.file_id, file_name=str(job_dir) + os.sep)

        downloaded = await retry_async(_download, max_retries=ctx.config.max_retries)
        dl_path = _resolve_downloaded(job_dir, downloaded)
        if dl_path is None or not dl_path.is_file():
            raise ed.EditorError(
                "⚠️ <b>ᴅᴏᴡɴʟᴏᴀᴅ ғᴀɪʟᴇᴅ.</b> ᴘʟᴇᴀsᴇ ᴛʀʏ ᴀɢᴀɪɴ."
            )
        if isinstance(storage, JobStorage) and not storage.verify_file(dl_path, info.size):
            raise ed.EditorError(
                "⚠️ <b>ᴅᴏᴡɴʟᴏᴀᴅ ᴠᴇʀɪғɪᴄᴀᴛɪᴏɴ ғᴀɪʟᴇᴅ.</b> ᴘʟᴇᴀsᴇ ᴛʀʏ ᴀɢᴀɪɴ."
            )
        return await operation(dl_path, job_dir)
    finally:
        if isinstance(storage, JobStorage):
            try:
                storage.cleanup_job(tmp_id)
            except Exception:
                pass
        elif job_dir is not None:
            shutil.rmtree(job_dir, ignore_errors=True)


# ──────────────────────────────────────────────────────────────────────────
# Operations
# ──────────────────────────────────────────────────────────────────────────
async def _do_paste(message: Message, selector: ed.LineSelector, dl_path: Path, job_dir: Path) -> None:
    result = ed.paste_lines(dl_path, selector)
    for text in ed.render_paste(ed.paste_header(selector), result.lines, result.truncated):
        await message.reply(text, disable_web_page_preview=True)


async def _do_replace(
    app: Client,
    message: Message,
    info: MediaInfo,
    selector: ed.LineSelector,
    replacements: list,
    dl_path: Path,
    job_dir: Path,
) -> None:
    out_dir = job_dir / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_name = ed.editor_output_name(info.filename)
    out_path = out_dir / safe_name
    result = ed.replace_lines(dl_path, selector, replacements, out_path)
    ed.verify_output(out_path, result.total_lines)
    caption = M.EDITOR_REPLACED.format(
        label=ed.selector_label(selector),
        name=html.escape(safe_name),
    )
    await app.send_document(
        chat_id=message.chat.id,
        document=str(out_path),
        file_name=safe_name,
        caption=caption,
        force_document=True,
    )


async def _do_detail(message: Message, info: MediaInfo, dl_path: Path, job_dir: Path) -> None:
    detail = ed.file_detail(dl_path, info.filename)
    await message.reply(ed.render_detail(detail), disable_web_page_preview=True)


async def editor_run_replace(
    app: Client,
    ctx: HandlerContext,
    message: Message,
    info: MediaInfo,
    selector: ed.LineSelector,
    replacements: list,
) -> None:
    """Download + validate bounds + write separate output + upload + cleanup."""
    await editor_download_and_run(
        app, ctx, info,
        lambda dl_path, job_dir: _do_replace(app, message, info, selector, replacements, dl_path, job_dir),
    )


# ──────────────────────────────────────────────────────────────────────────
# Command registration (same architecture as the other handler modules)
# ──────────────────────────────────────────────────────────────────────────
def register(app: Client, ctx: HandlerContext) -> None:

    @app.on_message(filters.command("paste"))
    async def paste_cmd(_client: Client, message: Message) -> None:
        _log_entry("paste_cmd", message)
        try:
            _, info = await editor_preflight(ctx, message)
            selector = ed.parse_line_selector(_command_arg(message, 0))
            async with _EDITOR_SLOTS:
                await editor_download_and_run(
                    app, ctx, info,
                    lambda dl_path, job_dir: _do_paste(message, selector, dl_path, job_dir),
                )
        except ed.EditorError as exc:
            if str(exc):
                await message.reply(str(exc), disable_web_page_preview=True)
        except Exception as exc:
            log.error("editor_paste_error", extra={"error": str(exc)})
            await message.reply(M.ERR_GENERIC)

    @app.on_message(filters.command("replace"))
    async def replace_cmd(_client: Client, message: Message) -> None:
        _log_entry("replace_cmd", message)
        try:
            user, info = await editor_preflight(ctx, message)
            cmd = ed.parse_replace_command(message.text or message.caption or "")
            if not cmd.needs_followup and len(cmd.inline_lines) != cmd.selector.width:
                raise ed.EditorError(ed.count_mismatch_text(cmd.selector.width, len(cmd.inline_lines)))
            if cmd.needs_followup:
                state = await ctx.state.get_user_state(user["user_id"]) or {}
                state["action"] = "editor_replace"
                state["editor"] = {
                    "file_id": info.file_id,
                    "file_ref": info.file_ref,
                    "filename": info.filename,
                    "size": info.size,
                    "mime_type": info.mime_type,
                    "media_type": info.media_type,
                    "selector": _command_arg(message, 0),
                }
                await ctx.state.set_user_state(user["user_id"], state)
                await message.reply(
                    M.EDITOR_REPLACE_PROMPT.format(
                        label=ed.selector_label(cmd.selector),
                        n=cmd.selector.width,
                    )
                )
                return
            async with _EDITOR_SLOTS:
                await editor_run_replace(app, ctx, message, info, cmd.selector, cmd.inline_lines)
        except ed.EditorError as exc:
            if str(exc):
                await message.reply(str(exc), disable_web_page_preview=True)
        except Exception as exc:
            log.error("editor_replace_error", extra={"error": str(exc)})
            await message.reply(M.ERR_GENERIC)

    @app.on_message(filters.command("detail"))
    async def detail_cmd(_client: Client, message: Message) -> None:
        _log_entry("detail_cmd", message)
        try:
            _, info = await editor_preflight(ctx, message)
            async with _EDITOR_SLOTS:
                await editor_download_and_run(
                    app, ctx, info,
                    lambda dl_path, job_dir: _do_detail(message, info, dl_path, job_dir),
                )
        except ed.EditorError as exc:
            if str(exc):
                await message.reply(str(exc), disable_web_page_preview=True)
        except Exception as exc:
            log.error("editor_detail_error", extra={"error": str(exc)})
            await message.reply(M.ERR_GENERIC)
