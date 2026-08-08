"""
Telegram File Renamer Bot – entrypoint.

Wires configuration, database, Redis, job manager, worker and the Pyrogram
client. Designed to run on a Render free web service with ~512 MB RAM.
"""
from __future__ import annotations

import asyncio
import os
import signal
import sys

from pyrogram import Client, idle
from pyrogram.errors import RPCError

from bot.callbacks import callbacks
from bot.handlers import commands, files, text_input
from bot.handlers.common import HandlerContext
from config import ConfigurationError, get_config
from database.database import Database
from services.cleanup import startup_cleanup
from services.health import HealthServer
from services.jobs import JobManager
from services.rate_limit import RateLimiter
from services.state import StateStore
from services.storage import JobStorage
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
        parse_mode="html",
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
        # Print directly – logging may not be configured yet.
        print(f"[FATAL CONFIG] {exc}", file=sys.stderr)
        sys.exit(2)

    setup_logging("INFO")
    log.info("starting", extra={"max_file_mb": config.max_file_size // (1024 * 1024)})

    # ── Health server starts early so Render sees the process is alive ──
    health = HealthServer(config.health_host, config.health_port)
    health.start()

    # ── Core services ──────────────────────────────────────────────────
    db = Database(config.database_url)
    state = StateStore(config.redis_url)
    storage = JobStorage(config)

    try:
        await db.connect()
    except Exception as exc:
        log.error("db_connect_failed", extra={"error": str(exc)})
        # Continue without DB? No – without DB nothing works. Exit so Render
        # restarts us rather than hanging unhealthy.
        health.stop()
        sys.exit(3)

    # Redis is a soft dependency: unavailable is logged (with the real,
    # credential-safe reason) and a background task keeps retrying with
    # backoff until Redis returns. The bot and health server start regardless.
    await state.connect()  # never raises; tolerated if unavailable

    # Clean stale dirs + recover interrupted/orphaned jobs.
    await startup_cleanup(db, state, storage, config.job_timeout)
    await state.reset_active_counters()

    jobs = JobManager(state, db, config)
    rate_limiter = RateLimiter(state, config)

    # ── Pyrogram client (proper session storage — not JobStorage) ──────
    app = build_pyrogram_client(config)
    attach_app_services(
        app, jobs=jobs, storage=storage, state=state, db=db, config=config,
    )

    ctx = HandlerContext(app, db, state, rate_limiter, config)

    # Register handlers explicitly (deterministic order, no plugin magic).
    commands.register(app, ctx)
    files.register(app, ctx)
    text_input.register(app, ctx)
    callbacks.register(app, ctx)

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
        health.set_healthy(True)

        # Wait until a stop signal is received. `idle()` keeps Pyrogram alive.
        await idle()
    except RPCError as exc:
        log.error("telegram_start_failed", extra={"error": str(exc)})
    except Exception as exc:
        # Log the real exception type/message (credential-safe) AND the full
        # traceback so a startup failure is never silently swallowed.
        log.error(
            "fatal_startup_error",
            extra={
                "error_type": type(exc).__name__,
                "error": mask_credentials(str(exc)),
            },
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
