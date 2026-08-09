"""
Free-text input handler.

When a user is in the middle of an operation (renaming, extension change,
batch action, etc.) the next text message is interpreted as the input for
that operation. We always validate, clear state, and either enqueue the
resulting jobs or ask for correction.
"""
from __future__ import annotations

from typing import Dict, List

from pyrogram import Client, filters
from pyrogram.types import Message

from bot import messages as M
from bot.handlers.common import HandlerContext
from bot.keyboards import keyboards as kb
from core import filename as fn
from core import file_editor as ed
from core import rename as rn
from core.media import MediaInfo
from core.validation import validate_extension_change, validate_file
from database import queries
from utils.logging import get_logger

log = get_logger(__name__)


def register(app: Client, ctx: HandlerContext) -> None:

    @app.on_message(filters.text & ~filters.command([
        "start", "help", "cancel", "history", "settings", "admin",
        "paste", "replace", "detail",
    ]) & filters.private)
    async def on_text(_client: Client, message: Message) -> None:
        try:
            uid = getattr(message.from_user, "id", None) if message.from_user else None
            cid = getattr(message.chat, "id", None) if message.chat else None
            log.info("handler_entry", extra={"handler": "on_text", "user_id": uid, "chat_id": cid})
        except Exception:
            pass

        user = message.from_user
        if user is None:
            return
        if await ctx.is_banned(user.id):
            await message.reply(M.ERR_BANNED)
            return
        if await ctx.rate_limited(message, "rename"):
            return

        state = await ctx.state.get_user_state(user.id)
        if not state:
            return  # ignore unrelated chatter

        action = state.get("action")
        raw = (message.text or "").strip()

        try:
            if action == "single_rename":
                await single_rename(message, state, raw)
            elif action == "single_ext":
                await single_ext(message, state, raw)
            elif action == "batch_rename":
                await batch_rename(message, state, raw)
            elif action == "batch_ext":
                await batch_ext(message, state, raw)
            elif action == "batch_prefix":
                await batch_transform(message, state, "prefix", raw)
            elif action == "batch_suffix":
                await batch_transform(message, state, "suffix", raw)
            elif action == "batch_replace":
                await batch_find_replace(message, state, raw)
            elif action == "batch_number":
                await batch_number(message, state, raw)
            elif action == "batch_ws":
                await batch_whitespace(message, state)
            elif action == "batch_case":
                await batch_case(message, state, raw)
            elif action == "editor_replace":
                await editor_replace_lines(message, state)
            else:
                await ctx.state.clear_user_state(user.id)
        except ValueError as exc:
            await message.reply(M.ERR_BAD_INPUT.format(reason=str(exc)))
        except Exception as exc:
            log.error("text_input_error", extra={"error": str(exc)})
            await message.reply(M.ERR_GENERIC)

    # ── Single-file operations ────────────────────────────────────────
    async def single_rename(message: Message, state: Dict, raw: str):
        single = state.get("single") or {}
        job_id = single.get("job_id")
        original = single.get("filename")
        if not job_id or not original:
            await message.reply(M.ERR_NO_FILE)
            await ctx.state.clear_user_state(message.from_user.id)
            return
        plan = rn.plan_rename(original, raw)
        # Apply the user's persisted whitespace/case preferences.
        try:
            settings = await queries.get_settings(ctx.db, message.from_user.id)
            plan.new_name = rn.apply_settings_to_name(plan.new_name, settings)
        except Exception:
            pass
        await enqueue_single(message, job_id, plan, "rename")

    async def single_ext(message: Message, state: Dict, raw: str):
        single = state.get("single") or {}
        job_id = single.get("job_id")
        original = single.get("filename")
        if not job_id or not original:
            await message.reply(M.ERR_NO_FILE)
            await ctx.state.clear_user_state(message.from_user.id)
            return
        ext = fn.normalise_extension(raw)
        if not validate_extension_change(ext):
            raise ValueError("that extension is not allowed (media/archive).")
        plan = rn.plan_extension(original, ext)
        await enqueue_single(message, job_id, plan, "extension")

    # ── File editor follow-up (/replace needs the lines in a next message) ──
    async def editor_replace_lines(message: Message, state: Dict):
        """
        Handle the follow-up message that supplies /replace replacement lines.

        Uses the RAW message text (never the stripped ``raw`` variable) so
        leading/trailing spaces inside replacement lines are preserved.
        """
        from bot.handlers.file_editor import editor_run_replace, editor_unsupported_text
        target = state.get("editor") or {}
        try:
            selector = ed.parse_line_selector(str(target.get("selector", "")))
            replacements = ed.split_message_lines(message.text or "")
            if len(replacements) != selector.width:
                await message.reply(ed.count_mismatch_text(selector.width, len(replacements)))
                return
            info = MediaInfo(
                filename=target.get("filename") or "",
                size=int(target.get("size", 0) or 0),
                mime_type=target.get("mime_type") or "",
                media_type=target.get("media_type") or "document",
                file_id=target.get("file_id") or "",
                file_ref=target.get("file_ref"),
                chat_id=message.chat.id,
                message_id=0,
            )
            result = validate_file(
                filename=info.filename,
                size=info.size,
                mime_type=info.mime_type,
                telegram_media_type=info.media_type,
                max_size=ctx.config.max_file_size,
            )
            if not result.ok:
                await ctx.state.clear_user_state(message.from_user.id)
                await message.reply(editor_unsupported_text(result, ctx))
                return
            await ctx.state.clear_user_state(message.from_user.id)
            await editor_run_replace(ctx.app, ctx, message, info, selector, replacements)
        except ed.EditorError as exc:
            await message.reply(str(exc), disable_web_page_preview=True)

    # ── Batch operations ──────────────────────────────────────────────
    async def batch_rename(message: Message, state: Dict, raw: str):
        items = state.get("items", [])
        if not raw:
            raise ValueError("base name cannot be empty")
        originals = [it["filename"] for it in items]
        plans = rn.number_batch(originals, raw, start=1, pad=2)
        await enqueue_batch(message, items, plans, "rename")

    async def batch_ext(message: Message, state: Dict, raw: str):
        items = state.get("items", [])
        ext = fn.normalise_extension(raw)
        if not validate_extension_change(ext):
            raise ValueError("that extension is not allowed (media/archive).")
        originals = [it["filename"] for it in items]
        plans = rn.extension_batch(originals, ext)
        await enqueue_batch(message, items, plans, "extension")

    async def batch_transform(message: Message, state: Dict, kind: str, raw: str):
        items = state.get("items", [])
        if not raw:
            raise ValueError("input cannot be empty")
        originals = [it["filename"] for it in items]
        if kind == "prefix":
            plans = rn.prefix_batch(originals, raw)
        else:
            plans = rn.suffix_batch(originals, raw)
        await enqueue_batch(message, items, plans, kind)

    async def batch_find_replace(message: Message, state: Dict, raw: str):
        items = state.get("items", [])
        if "|" not in raw:
            raise ValueError("use format: find|replace")
        find, _, replace = raw.partition("|")
        if not find:
            raise ValueError("search text cannot be empty")
        originals = [it["filename"] for it in items]
        plans = rn.find_replace_batch(originals, find, replace)
        await enqueue_batch(message, items, plans, "find_replace")

    async def batch_number(message: Message, state: Dict, raw: str):
        items = state.get("items", [])
        parts = raw.split("|")
        base = parts[0].strip()
        start = int(parts[1]) if len(parts) > 1 and parts[1].strip().isdigit() else 1
        pad = int(parts[2]) if len(parts) > 2 and parts[2].strip().isdigit() else 2
        if not base:
            raise ValueError("base name cannot be empty")
        originals = [it["filename"] for it in items]
        plans = rn.number_batch(originals, base, start=start, pad=pad)
        await enqueue_batch(message, items, plans, "number")

    async def batch_whitespace(message: Message, state: Dict):
        items = state.get("items", [])
        plans = [rn.plan_whitespace(it["filename"]) for it in items]
        await enqueue_batch(message, items, plans, "whitespace")

    async def batch_case(message: Message, state: Dict, raw: str):
        mode = raw.strip().lower()
        if mode not in {"lower", "upper", "title"}:
            raise ValueError("use: lower, upper or title")
        items = state.get("items", [])
        plans = [rn.plan_case(it["filename"], mode) for it in items]
        await enqueue_batch(message, items, plans, "case")

    # ── Enqueue helpers ───────────────────────────────────────────────
    async def enqueue_single(message: Message, job_id: str,
                             plan: rn.RenamePlan, operation: str):
        status = await message.reply(M.STATUS_QUEUED, reply_markup=kb.processing_keyboard(job_id))
        await queries.set_job_plan(
            ctx.db, job_id, operation, plan.new_name, status_msg_id=status.id
        )
        jm = getattr(app, "job_manager", None)
        if jm is not None:
            admission = await jm.check_admission(message.from_user.id)
            if not admission.accepted and admission.reason == "queue_full":
                await status.edit_text(M.ERR_QUEUE_FULL)
                return
        await ctx.state.enqueue_job(job_id)
        await ctx.state.clear_user_state(message.from_user.id)
    async def enqueue_batch(message: Message, items: List[Dict],
                            plans: List[rn.RenamePlan], operation: str):
        user = message.from_user
        status = await message.reply(M.STATUS_QUEUED)
        queued = 0
        for item, plan in zip(items, plans):
            job_id = item["job_id"]
            # The job is QUEUED in PostgreSQL (source of truth) and enqueued in the bounded
            # in-process queue; the reconciler re-pushes it if the queue was lost on restart.
            await queries.set_job_plan(
                ctx.db, job_id, operation, plan.new_name, status_msg_id=status.id,
            )
            await ctx.state.enqueue_job(job_id)
            queued += 1
        await ctx.state.clear_user_state(user.id)
        try:
            await status.edit_text(M.JOB_BATCH_DONE.format(ok=queued, total=len(items)))
        except Exception:
            pass
