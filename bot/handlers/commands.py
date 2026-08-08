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
        if ctx.config.start_video_url:
            try:
                await message.reply_video(
                    ctx.config.start_video_url,
                    caption=text,
                    reply_markup=kb.main_menu_keyboard(is_admin),
                )
                return
            except Exception:
                pass  # fall back to text
        await message.reply(text, reply_markup=kb.main_menu_keyboard(is_admin), disable_web_page_preview=True)

    @app.on_message(filters.command("help"))
    async def help_cmd(_client: Client, message: Message) -> None:
        await ctx.ensure_user(message)
        await message.reply(M.HELP, disable_web_page_preview=True)

    @app.on_message(filters.command("cancel"))
    async def cancel_cmd(_client: Client, message: Message) -> None:
        user = message.from_user
        await ctx.state.clear_user_state(user.id)
        # Find the user's most recent active job and cancel it.
        try:
            active = await queries.list_jobs_by_status(
                ctx.db,
                ["PENDING", "QUEUED", "DOWNLOADING", "RENAMING", "UPLOADING", "CLEANING"],
                limit=20,
            )
            for job in active:
                if int(job["user_id"]) == user.id:
                    from services.jobs import JobManager  # local import avoids cycle
                    # JobManager is injected via app; handled below via attribute.
                    jm: JobManager = getattr(app, "job_manager", None)  # type: ignore
                    if jm is not None:
                        await jm.cancel_job(str(job["id"]), user.id)
                    break
        except Exception:
            pass
        await message.reply(M.STATE_CLEARED)

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
