"""Command handlers: /start, /help, /cancel, /history, /settings, /admin."""
from __future__ import annotations

import math

from pyrogram import Client, filters
from pyrogram.types import Message

from bot import messages as M
from bot.handlers.common import HandlerContext
from bot.keyboards import keyboards as kb
from database import queries


def register(app: Client, ctx: HandlerContext) -> None:

    @app.on_message(filters.command("start"))
    async def start_cmd(_client: Client, message: Message) -> None:
        user = await ctx.ensure_user(message)
        if user and user.get("is_banned"):
            await message.reply(M.ERR_BANNED)
            return
        text = M.WELCOME.format(max_mb=ctx.config.max_file_size // (1024 * 1024))
        is_admin = bool(user and user.get("is_admin"))
        markup = kb.main_menu_keyboard(is_admin)

        video_url = ctx.config.start_video_url
        if video_url:
            try:
                # Reuse a cached file_id after the first send to avoid
                # re-downloading the welcome video every time.
                cached = getattr(app, "start_video_file_id", None)
                sent = await message.reply_video(
                    cached or video_url,
                    caption=text,
                    reply_markup=markup,
                    supports_streaming=True,
                )
                if sent and getattr(sent, "video", None) and not cached:
                    app.start_video_file_id = sent.video.file_id  # type: ignore[attr-defined]
                return
            except Exception as exc:
                from utils.logging import get_logger
                get_logger(__name__).warning("start_video_failed", extra={"error": str(exc)})
        await message.reply(text, reply_markup=markup, disable_web_page_preview=True)

    @app.on_message(filters.command("help"))
    async def help_cmd(_client: Client, message: Message) -> None:
        await ctx.ensure_user(message)
        await message.reply(M.HELP, disable_web_page_preview=True)

    @app.on_message(filters.command("cancel"))
    async def cancel_cmd(_client: Client, message: Message) -> None:
        user = message.from_user
        await ctx.state.clear_user_state(user.id)
        jm = getattr(app, "job_manager", None)
        cancelled = 0
        try:
            active = await queries.list_user_active_jobs(ctx.db, user.id)
            for job in active:
                if jm is not None:
                    await jm.cancel_job(str(job["id"]), user.id)
                    cancelled += 1
        except Exception:
            pass
        if cancelled:
            await message.reply(M.JOB_CANCELLED if cancelled == 1 else M.STATE_CLEARED)
        else:
            await message.reply(M.NOTHING_TO_CANCEL)

    @app.on_message(filters.command("history"))
    async def history_cmd(_client: Client, message: Message) -> None:
        if await ctx.rate_limited(message, "callback"):
            return
        user = message.from_user
        total = await queries.count_history(ctx.db, user.id)
        pages = max(1, math.ceil(total / ctx.config.history_page_size))
        rows = await queries.list_history(ctx.db, user.id, 0, ctx.config.history_page_size)
        text = M.render_history(rows, 1, pages)
        await message.reply(
            text,
            reply_markup=kb.pagination_keyboard("hist", 1, pages),
            disable_web_page_preview=True,
        )

    @app.on_message(filters.command("settings"))
    async def settings_cmd(_client: Client, message: Message) -> None:
        user = message.from_user
        s = await queries.get_settings(ctx.db, user.id)
        text = M.SETTINGS_MENU.format(
            case_mode=s.get("case_mode", "none"),
            ws_mode=s.get("ws_mode", "off"),
            num_mode=s.get("num_mode", "2"),
        )
        await message.reply(text, reply_markup=kb.settings_keyboard())

    @app.on_message(filters.command("admin"))
    async def admin_cmd(_client: Client, message: Message) -> None:
        user = message.from_user
        if not ctx.config.is_admin(user.id):
            await message.reply(M.ERR_NOT_ADMIN)
            return
        stats = await queries.admin_stats(ctx.db)
        text = M.ADMIN_MENU.format(
            users=stats.get("users", 0),
            jobs=stats.get("jobs", 0),
            completed=stats.get("completed", 0),
            failed=stats.get("failed", 0),
            queued=stats.get("queued", 0),
            active=stats.get("active", 0),
        )
        await message.reply(text, reply_markup=kb.admin_keyboard())
