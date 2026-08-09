"""
Telegram File Renamer Bot – entrypoint.

Wires configuration, database, StateStore, job manager, worker and the Pyrogram
client. Designed to run on a Render free web service with ~512 MB RAM.
No Redis – PostgreSQL + bounded in-process queue are the only dependencies.
"""
from __future__ import annotations

import asyncio
import os
import signal
import sys

from pyrogram import Client, idle
from pyrogram.enums import ParseMode
from pyrogram.errors import RPCError

from bot.callbacks import callbacks
from bot.handlers import commands, file_editor, files, text_input
from bot.handlers.common import HandlerContext
from config import ConfigurationError, get_config
from database.database import Database
from services.cleanup import startup_cleanup
from services.health import HealthServer
from services.jobs import JobManager
from services.rate_limit import RateLimiter
from services.state import StateStore
from services.storage import JobStorage
from utils.errors import install_exception_surface
from utils.logging import get_logger, setup_logging
from utils.sanitize import mask_credentials
from workers.processor import JobProcessor

log = get_logger("main")

# Session files live under the temp tree. Render's filesystem is ephemeral,
# which is fine for a bot-token session (re-auth is automatic via bot_token).
SESSION_NAME = "file_renamer_bot"
SESSION_SUBDIR = "session"


def session_workdir(config) -> str:
    """Return (and create) the Pyrogram session workdir under temp_dir."""
    path = str(config.temp_dir / SESSION_SUBDIR)
    os.makedirs(path, exist_ok=True)
    return path


def build_pyrogram_client(config) -> Client:
    """
    Construct the Pyrogram Client with a proper session backend.

    JobStorage must NEVER be passed as ``storage=`` or assigned to
    ``Client.storage`` — that attribute is Pyrogram's session store and
    must expose ``open()`` / ``save()`` / ``close()``.
    """
    return Client(
        name=SESSION_NAME,
        api_id=config.api_id,
        api_hash=config.api_hash,
        bot_token=config.bot_token,
        workdir=session_workdir(config),
        workers=8,  # bounded update handlers – low memory
        max_concurrent_transmissions=config.max_global_active_jobs,
        plugins=dict(enabled=False),
        parse_mode=ParseMode.HTML,
        sleep_threshold=60,
    )


def attach_app_services(app: Client, *, jobs, storage: JobStorage, state,
                        db, config) -> None:
    """
    Hang application services off the Client for handler convenience.

    Critical: JobStorage is attached as ``app.job_storage``, NEVER as
    ``app.storage``. Overwriting ``app.storage`` replaces Pyrogram's
    session backend and crashes at ``await app.start()`` with:
        AttributeError: 'JobStorage' object has no attribute 'open'
    """
    # Cache for the start-video file_id (never stores user files).
    app.start_video_file_id = None  # type: ignore[attr-defined]
    app.job_manager = jobs  # type: ignore[attr-defined]
    app.job_storage = storage  # type: ignore[attr-defined]
    app.state_store = state  # type: ignore[attr-defined]
    app.db = db  # type: ignore[attr-defined]
    app.config = config  # type: ignore[attr-defined]


async def _run() -> None:
    try:
        config = get_config()
    except ConfigurationError as exc:
        print(f"[FATAL CONFIG] {exc}", file=sys.stderr)
        sys.exit(2)

    setup_logging("INFO")
    import logging as _logging
    _logging.getLogger("pyrogram").setLevel(_logging.WARNING)
    _logging.getLogger("pyrogram.dispatcher").setLevel(_logging.ERROR)
    _logging.getLogger("pyrogram.session").setLevel(_logging.ERROR)
    log.info("starting", extra={"max_file_mb": config.max_file_size // (1024 * 1024)})

    # ── Health server starts early so Render sees the process is alive ──
    health = HealthServer(config.health_host, config.health_port)
    health.start()

    # ── Core services ──────────────────────────────────────────────────
    db = Database(config.database_url)
    state = StateStore(max_queue_size=config.max_queue_size)
    storage = JobStorage(config)

    try:
        await db.connect()
    except Exception as exc:
        log.error("db_connect_failed", extra={"error": str(exc)})
        health.stop()
        sys.exit(3)

    # No Redis – StateStore is in-process, always available.
    await state.connect()

    # Clean stale dirs + recover interrupted/orphaned jobs (PostgreSQL source of truth).
    await startup_cleanup(db, state, storage, config.job_timeout)
    await state.reset_active_counters()

    jobs = JobManager(state, db, config)
    rate_limiter = RateLimiter(state, config, db)

    # ── Pyrogram client (proper session storage — not JobStorage) ──────
    app = build_pyrogram_client(config)

    attach_app_services(
        app, jobs=jobs, storage=storage, state=state, db=db, config=config,
    )

    ctx = HandlerContext(app, db, state, rate_limiter, config)

    # Register handlers explicitly (deterministic order, no plugin magic).
    # Pyrogram's Dispatcher.add_handler is async via loop.create_task, so
    # immediate inspection would show 0. A short yield makes the count
    # reliable without patching private internals.
    commands.register(app, ctx)
    files.register(app, ctx)
    text_input.register(app, ctx)
    callbacks.register(app, ctx)
    file_editor.register(app, ctx)

    # Debug-safe raw update tracer (records ONLY metadata, never message text / secrets).
    from pyrogram.handlers import RawUpdateHandler

    async def _debug_update(_client, update, _users, _chats):
        try:
            utype = type(update).__name__
            uid = None
            cid = None
            if hasattr(update, "message") and update.message:
                m = update.message
                if getattr(m, "from_user", None):
                    uid = getattr(m.from_user, "id", None)
                if getattr(m, "chat", None):
                    cid = getattr(m.chat, "id", None)
            elif hasattr(update, "from_user") and update.from_user:
                uid = update.from_user.id
            elif hasattr(update, "user_id"):
                uid = update.user_id
            if "Callback" in utype or "callback" in utype.lower():
                if hasattr(update, "from_user") and update.from_user:
                    uid = update.from_user.id
                if hasattr(update, "message") and update.message and getattr(update.message, "chat", None):
                    cid = update.message.chat.id
            log.info(
                "telegram_update_received",
                extra={"update_type": utype, "user_id": uid, "chat_id": cid},
            )
        except Exception:
            pass

    app.add_handler(RawUpdateHandler(_debug_update), group=-999)

    # Yield to let pending add_handler tasks complete, then log reliable count.
    # This uses public API (dispatcher.groups) after an event-loop tick, without
    # mutating private Dispatcher internals.
    try:
        await asyncio.sleep(0.1)
        group_count = len(getattr(app.dispatcher, "groups", {}))
        handler_count = sum(len(v) for v in getattr(app.dispatcher, "groups", {}).values())
        log.info("handlers_registered", extra={"groups": group_count, "handlers": handler_count})
        if handler_count == 0:
            log.warning("handlers_registered_zero", extra={"groups": group_count, "handlers": handler_count})
    except Exception:
        log.info("handlers_registered")

    # Real exception surfacing: each registered callback (Message, CallbackQuery
    # and raw) is wrapped so a failure logs the ORIGINAL exception type, message
    # and traceback before being re-raised. Registration itself is unchanged.
    try:
        wrapped = install_exception_surface(app)
        log.info("exception_surface_installed", extra={"wrapped": wrapped})
    except Exception as exc:
        log.warning("exception_surface_install_failed", extra={"error": str(exc)})

    # JobStorage is passed explicitly to the worker — never via Client.storage.
    processor = JobProcessor(app, db, state, jobs, storage, config)

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _request_stop(*_args) -> None:
        log.info("shutdown_signal_received")
        stop_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _request_stop)
        except NotImplementedError:
            pass

    try:
        await app.start()
        me = await app.get_me()
        log.info("bot_started", extra={"username": me.username})

        processor.start()
        log.info("processor_started", extra={"consumers": len(processor._consumers)})
        health.set_healthy(True)

        await idle()
    except RPCError as exc:
        log.error("telegram_start_failed", extra={"error": str(exc)})
    except Exception as exc:
        log.error(
            "fatal_startup_error",
            extra={"error_type": type(exc).__name__, "error": mask_credentials(str(exc))},
            exc_info=True,
        )
    finally:
        log.info("shutting_down")
        health.set_healthy(False)
        try:
            await processor.stop()
        except Exception:
            pass
        try:
            await jobs.shutdown()
        except Exception:
            pass
        try:
            connected = app.is_connected
            if asyncio.iscoroutine(connected):
                connected = await connected
            if connected:
                await app.stop()
        except Exception:
            pass
        try:
            await state.close()
        except Exception:
            pass
        try:
            await db.close()
        except Exception:
            pass
        health.stop()
        log.info("shutdown_complete")


def main() -> None:
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
