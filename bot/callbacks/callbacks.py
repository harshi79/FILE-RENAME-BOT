"""
Callback query router.

Parses short callback data, validates ownership and dispatches to the right
operation. Malformed callbacks are answered gracefully and never crash.
"""
from __future__ import annotations

import math

from pyrogram import Client
from pyrogram.types import CallbackQuery

from bot import messages as M
from bot.handlers.common import HandlerContext
from bot.keyboards import keyboards as kb
from database import queries
from utils.errors import is_message_not_modified, log_handler_exception
from utils.logging import get_logger

log = get_logger(__name__)


async def safe_edit_message_text(cb: CallbackQuery, text: str, **kwargs):
    """
    Single edit path for every callback screen.

    Telegram answers ``400 MESSAGE_NOT_MODIFIED`` when the new content is
    identical to what is already displayed. That happens routinely on
    idempotent menus (``adm:stats``, ``adm:jobs:0``, pagination re-taps) and is
    harmless, so it is treated as a no-op: logged at DEBUG, never as ERROR, and
    never propagated back to the dispatcher.

    Every other Telegram/API error is re-raised unchanged so existing error
    handling keeps working.
    """
    try:
        return await cb.edit_message_text(text, **kwargs)
    except Exception as exc:  # noqa: BLE001 - only MESSAGE_NOT_MODIFIED is absorbed
        if is_message_not_modified(exc):
            log.debug("message_not_modified_ignored", extra={
                "user_id": getattr(getattr(cb, "from_user", None), "id", None),
                "callback_data": (cb.data or "")[:32] if getattr(cb, "data", None) else None,
            })
            return None
        raise


def register(app: Client, ctx: HandlerContext) -> None:

    @app.on_callback_query()
    async def on_callback(_client: Client, cb: CallbackQuery) -> None:
        if cb.from_user is None:
            await cb.answer()
            return
        user_id = cb.from_user.id

        try:
            log.info("handler_entry", extra={
                "handler": "on_callback",
                "user_id": user_id,
                "chat_id": getattr(getattr(cb, "message", None), "chat", None) and getattr(cb.message.chat, "id", None) or None,
                "callback_data": (cb.data or "")[:32] if cb.data else None,  # prefix only, never full secrets
            })
        except Exception:
            pass

        # Global callback rate limit (admins exempt).
        if not ctx.config.is_admin(user_id):
            rl = await ctx.rate_limiter.check(user_id, "callback")
            if not rl.allowed:
                await cb.answer(M.ERR_RATE_LIMIT.format(seconds=rl.retry_after), show_alert=True)
                return

        data = (cb.data or "").strip()
        try:
            await _dispatch(app, ctx, cb, data, user_id)
        except Exception as exc:
            # CallbackQuery errors are handled here (the user gets an alert), so
            # surface the ORIGINAL exception type/message/traceback right where
            # it is caught — otherwise the real cause would never be logged.
            log_handler_exception(
                exc, logger=log, event="callback_error",
                handler="on_callback", user_id=user_id, data=data,
            )
            try:
                await cb.answer(M.ERR_BAD_CALLBACK, show_alert=True)
            except Exception:
                pass

    async def _dispatch(app: Client, ctx: HandlerContext, cb: CallbackQuery,
                        data: str, user_id: int) -> None:
        # No-op button (e.g. page indicator)
        if data == "noop":
            await cb.answer()
            return

        if data == "main":
            await _show_main(cb, ctx, user_id)
            return

        # Close deletes the menu message.
        if data.startswith("close:"):
            job_id = data.split(":", 1)[1]
            await _assert_owner(ctx, job_id, user_id)
            try:
                if cb.message:
                    await cb.message.delete()
            except Exception:
                pass
            await cb.answer()
            return

        if data.startswith("cancel:"):
            job_id = data.split(":", 1)[1]
            jm = getattr(app, "job_manager", None)
            if jm is not None:
                result = await jm.cancel_job(job_id, user_id)
                if result == "active":
                    await cb.answer(M.CANCEL_IN_PROGRESS, show_alert=False)
                else:
                    await cb.answer(M.JOB_CANCELLED, show_alert=False)
                    try:
                        await safe_edit_message_text(cb, M.JOB_CANCELLED)
                    except Exception:
                        pass
            return

        if data == "cancel_state":
            await ctx.state.clear_user_state(user_id)
            await safe_edit_message_text(cb, M.STATE_CLEARED)
            return

        # ── Single-file actions ───────────────────────────────────────
        if data.startswith("rename:"):
            job_id = data.split(":", 1)[1]
            await _assert_owner(ctx, job_id, user_id)
            job = await queries.get_job(ctx.db, job_id)
            state = await ctx.state.get_user_state(user_id) or {}
            state["action"] = "single_rename"
            state["single"] = {"job_id": job_id, "filename": job["original_name"]}
            await ctx.state.set_user_state(user_id, state)
            await safe_edit_message_text(
                cb,
                M.RENAME_PROMPT.format(current=job["original_name"]),
                reply_markup=kb.cancel_keyboard(),
            )
            await cb.answer()
            return

        if data.startswith("ext:"):
            job_id = data.split(":", 1)[1]
            await _assert_owner(ctx, job_id, user_id)
            job = await queries.get_job(ctx.db, job_id)
            state = await ctx.state.get_user_state(user_id) or {}
            state["action"] = "single_ext"
            state["single"] = {"job_id": job_id, "filename": job["original_name"]}
            await ctx.state.set_user_state(user_id, state)
            await safe_edit_message_text(
                cb,
                M.EXTENSION_PROMPT.format(current=job["original_name"]),
                reply_markup=kb.cancel_keyboard(),
            )
            await cb.answer()
            return

        # ── Batch actions ─────────────────────────────────────────────
        batch_map = {
            "b_rename": ("batch_rename", M.BATCH_RENAME_PROMPT.format(base="Episode", ext=".txt")),
            "b_ext": ("batch_ext", "🔄 <b>ꜱᴇɴᴅ ᴛʜᴇ ɴᴇᴡ ᴇxᴛᴇɴsɪᴏɴ ꜰᴏʀ ᴀʟʟ ꜰɪʟᴇs</b>\nᴇxᴀᴍᴘʟᴇ: <code>.md</code>"),
            "b_prefix": ("batch_prefix", M.PREFIX_PROMPT),
            "b_suffix": ("batch_suffix", M.SUFFIX_PROMPT),
            "b_replace": ("batch_replace", M.ADVANCED_PROMPT_FIND),
            "b_number": ("batch_number", M.ZEROPAD_PROMPT),
            "b_ws": ("batch_ws", None),  # immediate
            "b_case": ("batch_case", None),  # asks text
        }
        if data in batch_map:
            action, prompt = batch_map[data]
            state = await ctx.state.get_user_state(user_id) or {}
            items = state.get("items", [])
            if not items:
                await cb.answer(M.ERR_NO_FILE, show_alert=True)
                return
            if action == "batch_ws":
                # Immediate operation – build and enqueue directly.
                from core import rename as rn
                plans = [rn.plan_whitespace(it["filename"]) for it in items]
                await _enqueue_batch_from_cb(app, ctx, cb, items, plans, "whitespace")
                return
            if action == "batch_case":
                # Easier: ask for lower/upper/title by using a tiny inline.
                await safe_edit_message_text(
                    cb,
                    "🔠 ꜱᴇɴᴅ <code>lower</code>, <code>upper</code> ᴏʀ <code>title</code>:",
                    reply_markup=kb.cancel_keyboard(),
                )
                state["action"] = "batch_case"
                await ctx.state.set_user_state(user_id, state)
                await cb.answer()
                return

            state["action"] = action
            await ctx.state.set_user_state(user_id, state)
            if prompt:
                await safe_edit_message_text(cb, prompt, reply_markup=kb.cancel_keyboard())
            await cb.answer()
            return

        if data == "b_cancel":
            state = await ctx.state.get_user_state(user_id) or {}
            items = state.get("items", [])
            jm = getattr(app, "job_manager", None)
            for item in items:
                try:
                    if jm is not None:
                        await jm.cancel_job(item["job_id"], user_id)
                    else:
                        from database.models import JobStatus
                        await queries.set_job_status(
                            ctx.db, item["job_id"], JobStatus.CANCELLED, error="cancelled"
                        )
                except Exception:
                    pass
            await ctx.state.clear_user_state(user_id)
            await safe_edit_message_text(cb, M.STATE_CLEARED)
            return

        # Admin ban/unban toggle: "ban:<user_id>"
        if data.startswith("ban:"):
            if not ctx.config.is_admin(user_id):
                await cb.answer(M.ERR_NOT_ADMIN, show_alert=True)
                return
            target = int(data.split(":", 1)[1])
            u = await queries.get_user(ctx.db, target)
            new_state = not bool(u and u.get("is_banned"))
            await queries.set_banned(ctx.db, target, new_state)
            await cb.answer(("🚫 banned" if new_state else "✅ unbanned"), show_alert=False)
            # Refresh the users list.
            await _admin_dispatch(cb, ctx, "adm:users:0")
            return

        # ── Settings / history / admin ────────────────────────────────
        if data == "settings":
            s = await queries.get_settings(ctx.db, user_id)
            await safe_edit_message_text(
                cb,
                M.SETTINGS_MENU.format(
                    case_mode=s.get("case_mode", "none"),
                    ws_mode=s.get("ws_mode", "off"),
                    num_mode=s.get("num_mode", "2"),
                ),
                reply_markup=kb.settings_keyboard(),
            )
            await cb.answer()
            return

        if data.startswith("set:"):
            field = data.split(":", 1)[1]
            await _toggle_setting(ctx, user_id, field)
            s = await queries.get_settings(ctx.db, user_id)
            await safe_edit_message_text(
                cb,
                M.SETTINGS_MENU.format(
                    case_mode=s.get("case_mode", "none"),
                    ws_mode=s.get("ws_mode", "off"),
                    num_mode=s.get("num_mode", "2"),
                ),
                reply_markup=kb.settings_keyboard(),
            )
            await cb.answer()
            return

        if data == "history" or data.startswith("hist:"):
            page = int(data.split(":", 1)[1]) if ":" in data else 1
            await _show_history(cb, ctx, user_id, page)
            return

        if data == "admin" or data.startswith("adm:"):
            if not ctx.config.is_admin(user_id):
                await cb.answer(M.ERR_NOT_ADMIN, show_alert=True)
                return
            await _admin_dispatch(cb, ctx, data)
            return

        await cb.answer(M.ERR_BAD_CALLBACK, show_alert=True)


async def _assert_owner(ctx: HandlerContext, job_id: str, user_id: int) -> None:
    job = await queries.get_job(ctx.db, job_id)
    if not job:
        raise ValueError("job not found")
    if int(job["user_id"]) != user_id and not ctx.config.is_admin(user_id):
        raise PermissionError("not your job")


async def _show_main(cb: CallbackQuery, ctx: HandlerContext, user_id: int) -> None:
    text = M.WELCOME.format(max_mb=ctx.config.max_file_size // (1024 * 1024))
    await safe_edit_message_text(
        cb,
        text,
        reply_markup=kb.main_menu_keyboard(ctx.config.is_admin(user_id)),
        disable_web_page_preview=True,
    )
    await cb.answer()


async def _toggle_setting(ctx: HandlerContext, user_id: int, field: str) -> None:
    s = await queries.get_settings(ctx.db, user_id)
    if field == "case":
        order = ["none", "title", "lower", "upper"]
        cur = s.get("case_mode", "none")
        nxt = order[(order.index(cur) + 1) % len(order)] if cur in order else "none"
        await queries.update_setting(ctx.db, user_id, "case_mode", nxt)
    elif field == "ws":
        cur = s.get("ws_mode", "off")
        nxt = "on" if cur == "off" else "off"
        await queries.update_setting(ctx.db, user_id, "ws_mode", nxt)
    elif field == "num":
        cur = str(s.get("num_mode", "2"))
        nxt = "2" if cur != "2" else "3"
        await queries.update_setting(ctx.db, user_id, "num_mode", nxt)


async def _show_history(cb: CallbackQuery, ctx: HandlerContext, user_id: int, page: int) -> None:
    total = await queries.count_history(ctx.db, user_id)
    pages = max(1, math.ceil(total / ctx.config.history_page_size))
    page = max(1, min(page, pages))
    offset = (page - 1) * ctx.config.history_page_size
    rows = await queries.list_history(ctx.db, user_id, offset, ctx.config.history_page_size)
    text = M.render_history(rows, page, pages)
    await safe_edit_message_text(
        cb,
        text, reply_markup=kb.pagination_keyboard("hist", page, pages),
        disable_web_page_preview=True,
    )
    await cb.answer()


async def _enqueue_batch_from_cb(app, ctx, cb, items, plans, operation: str) -> None:
    queued = 0
    for item, plan in zip(items, plans):
        await queries.set_job_plan(ctx.db, item["job_id"], operation, plan.new_name,
                                   status_msg_id=cb.message.id)
        if await ctx.state.enqueue_job(item["job_id"]):
            queued += 1
    await ctx.state.clear_user_state(cb.from_user.id)
    try:
        await safe_edit_message_text(cb, M.JOB_BATCH_DONE.format(ok=queued, total=len(items)))
    except Exception:
        pass
    await cb.answer()


async def _admin_dispatch(cb: CallbackQuery, ctx: HandlerContext, data: str) -> None:
    parts = data.split(":")
    section = parts[1] if len(parts) > 1 else "menu"
    page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0

    if section == "menu" or data == "admin":
        stats = await queries.admin_stats(ctx.db)
        text = M.ADMIN_MENU.format(
            users=stats.get("users", 0), jobs=stats.get("jobs", 0),
            completed=stats.get("completed", 0), failed=stats.get("failed", 0),
            queued=stats.get("queued", 0), active=stats.get("active", 0),
        )
        await safe_edit_message_text(cb, text, reply_markup=kb.admin_keyboard())
        await cb.answer()
        return

    if section == "stats":
        stats = await queries.admin_stats(ctx.db)
        text = M.ADMIN_MENU.format(**{
            "users": stats.get("users", 0), "jobs": stats.get("jobs", 0),
            "completed": stats.get("completed", 0), "failed": stats.get("failed", 0),
            "queued": stats.get("queued", 0), "active": stats.get("active", 0),
        })
        await safe_edit_message_text(cb, text, reply_markup=kb.admin_keyboard())
        await cb.answer()
        return

    limit = ctx.config.admin_page_size
    offset = page * limit

    if section == "users":
        rows = await queries.list_users(ctx.db, offset, limit)
        total = await queries.count_users(ctx.db)
        body = "👥 <b>ᴜsᴇʀs</b>\n\n"
        for r in rows:
            name = (r.get("first_name") or r.get("username") or "").replace("<", "").replace(">", "")
            flag = " 🚫" if r.get("is_banned") else ""
            body += f"• <code>{r['user_id']}</code> {name}{flag}\n"
        pages = max(1, math.ceil(total / limit))
        # Add ban/unban toggle buttons per user.
        ban_kb = kb.users_admin_keyboard(rows, page, pages)
        await safe_edit_message_text(cb, body or "ɴᴏ ᴜsᴇʀs.", reply_markup=ban_kb)
    elif section == "jobs":
        rows = await queries.list_recent_jobs(ctx.db, offset, limit)
        total = await queries.count_jobs(ctx.db)
        body = "📊 <b>ʀᴇᴄᴇɴᴛ ᴊᴏʙs</b>\n\n"
        for r in rows:
            body += f"• <code>{r['original_name']}</code> [{r['status']}]\n"
        pages = max(1, math.ceil(total / limit))
        await safe_edit_message_text(
            cb, body or "ɴᴏ ᴊᴏʙs.",
            reply_markup=kb.pagination_keyboard("adm:jobs", page + 1, pages))
    elif section == "failed":
        rows = await queries.list_failed_jobs(ctx.db, offset, limit)
        total = await queries.count_jobs(ctx.db, "FAILED")
        body = "❌ <b>ꜰᴀɪʟᴇᴅ ᴊᴏʙs</b>\n\n"
        for r in rows:
            err = (r.get("error") or "")[:40].replace("<", "")
            body += f"• <code>{r['original_name']}</code> – {err}\n"
        pages = max(1, math.ceil(total / limit))
        await safe_edit_message_text(
            cb, body or "ɴᴏ ꜰᴀɪʟᴇᴅ ᴊᴏʙs.",
            reply_markup=kb.pagination_keyboard("adm:failed", page + 1, pages))
    await cb.answer()
