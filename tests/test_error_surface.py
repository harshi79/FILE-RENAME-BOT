"""
Regression tests for two production log defects.

ISSUE 1 — broken exception observability
    Production repeatedly emitted::

        {"level": "ERROR", "msg": "handler_exception_surface", "exc": "NoneType: None"}

    because ``main.py`` registered ``_on_error`` as a ``RawUpdateHandler`` in
    group 999. Pyrogram has no error-handler concept, so that callback ran for
    *every* update with no exception in flight; ``exc_info=True`` then fell back
    to ``sys.exc_info()`` -> ``(None, None, None)`` -> ``"NoneType: None"``.
    These tests pin that the ORIGINAL exception type, message and traceback are
    surfaced instead, for Message *and* CallbackQuery handlers, and that the
    exception is re-raised (never swallowed, replaced or fabricated).

ISSUE 2 — MESSAGE_NOT_MODIFIED
    Idempotent callback screens (``adm:stats``, ``adm:jobs:0``) re-render the
    same text, and Telegram answers ``[400 MESSAGE_NOT_MODIFIED]``. That was
    logged as ERROR and shown to the user as a failed callback. These tests pin
    that only this condition is absorbed as a no-op and that every other
    Telegram/API error still propagates and is logged.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _ensure_event_loop() -> None:
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())


_ensure_event_loop()


def _make_config(tmp: Path):
    from config import Config, RateLimitConfig
    return Config(
        api_id=12345,
        api_hash="0123456789abcdef0123456789abcdef",
        bot_token="123456:ABC-DEF_fake_token_for_tests",
        database_url="postgresql://u:p@localhost:5432/db",
        admin_ids=frozenset({777}),
        max_file_size=25 * 1024 * 1024,
        max_global_active_jobs=2,
        max_active_jobs_per_user=1,
        max_retries=3,
        job_timeout=300,
        max_queue_size=20,
        history_page_size=8,
        admin_page_size=10,
        temp_dir=tmp,
        health_host="127.0.0.1",
        health_port=0,
        start_video_url="",
        rate_limit=RateLimitConfig(),
    )


class _Collector(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@contextmanager
def capture_logs(logger_name: str = ""):
    """Collect every record reaching a logger (root by default)."""
    logger = logging.getLogger(logger_name)
    collector = _Collector()
    previous_level = logger.level
    logger.addHandler(collector)
    logger.setLevel(logging.DEBUG)
    try:
        yield collector
    finally:
        logger.removeHandler(collector)
        logger.setLevel(previous_level)


def _rendered(record: logging.LogRecord) -> dict:
    """Render a record exactly like production does (JSON line)."""
    from utils.logging import JsonFormatter
    return json.loads(JsonFormatter().format(record))


def _fake_message(user_id: int = 777, chat_id: int = 999, text: str = "secret user content"):
    msg = MagicMock()
    msg.text = text
    msg.from_user = MagicMock(id=user_id)
    msg.chat = MagicMock(id=chat_id)
    msg.reply = AsyncMock()
    return msg


def _fake_callback_query(data: str = "adm:stats", user_id: int = 777, chat_id: int = 999):
    cb = MagicMock()
    cb.data = data
    cb.from_user = MagicMock(id=user_id)
    cb.message = MagicMock()
    cb.message.chat = MagicMock(id=chat_id)
    cb.message.id = 42
    cb.answer = AsyncMock()
    cb.edit_message_text = AsyncMock()
    return cb


# ══════════════════════════════════════════════════════════════════════════
# ISSUE 1 — exception observability
# ══════════════════════════════════════════════════════════════════════════
class NoneTypeNoneRegressionTests(unittest.TestCase):
    """The exact production symptom must be impossible to produce again."""

    def test_old_bug_shape_no_longer_renders_nonetype_none(self):
        """exc_info=True with no live exception must not emit 'NoneType: None'."""
        record = logging.LogRecord(
            name="main", level=logging.ERROR, pathname=__file__, lineno=1,
            msg="handler_exception_surface", args=(), exc_info=sys.exc_info(),
        )
        self.assertEqual(record.exc_info, (None, None, None))
        payload = _rendered(record)
        self.assertNotIn("exc", payload)
        self.assertNotIn("NoneType: None", json.dumps(payload))

    def test_real_exception_still_rendered_with_traceback(self):
        try:
            raise ValueError("boom-from-handler")
        except ValueError:
            record = logging.LogRecord(
                name="main", level=logging.ERROR, pathname=__file__, lineno=1,
                msg="handler_exception_surface", args=(), exc_info=sys.exc_info(),
            )
        payload = _rendered(record)
        self.assertIn("Traceback", payload["exc"])
        self.assertIn("ValueError: boom-from-handler", payload["exc"])

    def test_main_no_longer_registers_fake_error_handler(self):
        src = (Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8")
        self.assertNotIn("_on_error", src)
        self.assertNotIn("RawUpdateHandler(_on_error)", src)
        self.assertIn("install_exception_surface", src)


class SurfaceExceptionsTests(unittest.TestCase):
    """surface_exceptions must log the real exception and re-raise it."""

    def test_message_handler_exception_is_surfaced_and_reraised(self):
        from utils.errors import surface_exceptions

        async def start_cmd(_client, message):
            raise ValueError("boom-in-message-handler")

        wrapped = surface_exceptions(start_cmd)
        msg = _fake_message()

        with capture_logs() as logs:
            with self.assertRaises(ValueError) as caught:
                asyncio.get_event_loop().run_until_complete(wrapped(MagicMock(), msg))

        self.assertEqual(str(caught.exception), "boom-in-message-handler")
        errors = [r for r in logs.records if r.levelno >= logging.ERROR]
        self.assertEqual(len(errors), 1)
        record = errors[0]
        self.assertEqual(record.getMessage(), "handler_exception_surface")
        self.assertEqual(record.exc_type, "ValueError")
        self.assertEqual(record.exc_message, "boom-in-message-handler")
        self.assertEqual(record.handler, "start_cmd")
        self.assertEqual(record.user_id, 777)
        self.assertEqual(record.chat_id, 999)

        payload = _rendered(record)
        self.assertNotIn("NoneType: None", json.dumps(payload))
        self.assertIn("Traceback", payload["exc"])
        self.assertIn("ValueError: boom-in-message-handler", payload["exc"])

    def test_callback_query_handler_exception_is_surfaced_and_reraised(self):
        from utils.errors import surface_exceptions

        async def on_callback(_client, cb):
            raise KeyError("boom-in-callback-handler")

        wrapped = surface_exceptions(on_callback)
        cb = _fake_callback_query()

        with capture_logs() as logs:
            with self.assertRaises(KeyError):
                asyncio.get_event_loop().run_until_complete(wrapped(MagicMock(), cb))

        errors = [r for r in logs.records if r.levelno >= logging.ERROR]
        self.assertEqual(len(errors), 1)
        record = errors[0]
        self.assertEqual(record.exc_type, "KeyError")
        self.assertIn("boom-in-callback-handler", record.exc_message)
        self.assertEqual(record.user_id, 777)
        self.assertEqual(record.chat_id, 999)
        payload = _rendered(record)
        self.assertIn("KeyError", payload["exc"])
        self.assertNotIn("NoneType: None", json.dumps(payload))

    def test_original_exception_object_is_not_replaced(self):
        from utils.errors import surface_exceptions

        original = RuntimeError("do-not-replace-me")

        async def handler(_client, message):
            raise original

        wrapped = surface_exceptions(handler)
        with capture_logs() as logs:
            with self.assertRaises(RuntimeError) as caught:
                asyncio.get_event_loop().run_until_complete(
                    wrapped(MagicMock(), _fake_message())
                )
        # The very same object comes back out — nothing is wrapped or rebuilt.
        self.assertIs(caught.exception, original)
        record = [r for r in logs.records if r.levelno >= logging.ERROR][0]
        self.assertIs(record.exc_info[1], original)
        self.assertIsNotNone(record.exc_info[2], "traceback must be preserved")

    def test_successful_handler_is_untouched(self):
        from utils.errors import surface_exceptions

        async def handler(_client, message):
            return "ok"

        wrapped = surface_exceptions(handler)
        with capture_logs() as logs:
            result = asyncio.get_event_loop().run_until_complete(
                wrapped(MagicMock(), _fake_message())
            )
        self.assertEqual(result, "ok")
        self.assertEqual([r for r in logs.records if r.levelno >= logging.ERROR], [])

    def test_flow_control_signals_pass_through_unlogged(self):
        from pyrogram import ContinuePropagation, StopPropagation
        from utils.errors import surface_exceptions

        for signal_cls in (StopPropagation, ContinuePropagation):
            async def handler(_client, message, _cls=signal_cls):
                raise _cls()

            wrapped = surface_exceptions(handler)
            with capture_logs() as logs:
                with self.assertRaises(signal_cls):
                    asyncio.get_event_loop().run_until_complete(
                        wrapped(MagicMock(), _fake_message())
                    )
            self.assertEqual([r for r in logs.records if r.levelno >= logging.ERROR], [])

    def test_handler_metadata_preserved_and_not_double_wrapped(self):
        from utils.errors import surface_exceptions

        async def start_cmd(_client, message):
            return None

        wrapped = surface_exceptions(start_cmd)
        self.assertEqual(wrapped.__name__, "start_cmd")
        self.assertTrue(asyncio.iscoroutinefunction(wrapped))
        self.assertIs(surface_exceptions(wrapped), wrapped)


class ExceptionSurfacePrivacyTests(unittest.TestCase):
    """Surfacing must not leak secrets or message contents."""

    def test_credentials_and_tokens_are_masked(self):
        from utils.errors import surface_exceptions

        async def handler(_client, message):
            raise RuntimeError(
                "connect failed postgresql://admin:sup3rs3cret@db.internal:5432/app "
                "token=123456789:AAF-thisisafakebottokenvalue0123456789xyz"
            )

        wrapped = surface_exceptions(handler)
        with capture_logs() as logs:
            with self.assertRaises(RuntimeError):
                asyncio.get_event_loop().run_until_complete(
                    wrapped(MagicMock(), _fake_message())
                )
        record = [r for r in logs.records if r.levelno >= logging.ERROR][0]
        rendered = json.dumps(_rendered(record))
        self.assertNotIn("sup3rs3cret", rendered)
        self.assertNotIn("AAF-thisisafakebottokenvalue0123456789xyz", rendered)
        self.assertIn("admin:***@db.internal", record.exc_message)
        self.assertIn("<bot_token>", record.exc_message)

    def test_message_text_is_never_logged(self):
        from utils.errors import surface_exceptions

        async def handler(_client, message):
            raise ValueError("failure without user content")

        wrapped = surface_exceptions(handler)
        msg = _fake_message(text="please do not log this private sentence")
        with capture_logs() as logs:
            with self.assertRaises(ValueError):
                asyncio.get_event_loop().run_until_complete(wrapped(MagicMock(), msg))
        rendered = json.dumps(_rendered(
            [r for r in logs.records if r.levelno >= logging.ERROR][0]
        ))
        self.assertNotIn("private sentence", rendered)


class InstallExceptionSurfaceTests(unittest.TestCase):
    """The surfacing layer must attach to the real, registered handlers."""

    def setUp(self) -> None:
        _ensure_event_loop()
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.config = _make_config(self.tmp)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _build_app(self):
        from bot.callbacks import callbacks
        from bot.handlers import commands, files, text_input
        from bot.handlers.common import HandlerContext
        from database.database import Database
        from main import attach_app_services, build_pyrogram_client
        from services.rate_limit import RateLimiter
        from services.state import StateStore

        app = build_pyrogram_client(self.config)
        db = MagicMock(spec=Database)
        db.execute = AsyncMock(return_value="")
        db.fetch_one = AsyncMock(return_value={"user_id": 777, "is_banned": False, "is_admin": True})
        db.fetch_all = AsyncMock(return_value=[])
        state = StateStore(max_queue_size=20)
        rate = RateLimiter(state, self.config)
        attach_app_services(app, jobs=MagicMock(), storage=MagicMock(),
                            state=state, db=db, config=self.config)
        ctx = HandlerContext(app, db, state, rate, self.config)
        commands.register(app, ctx)
        files.register(app, ctx)
        text_input.register(app, ctx)
        callbacks.register(app, ctx)
        return app

    def test_install_wraps_message_and_callback_handlers(self):
        from pyrogram.handlers import CallbackQueryHandler, MessageHandler
        from utils.errors import install_exception_surface

        app = self._build_app()

        async def scenario():
            await asyncio.sleep(0.15)
            wrapped = install_exception_surface(app)
            self.assertGreater(wrapped, 0)
            handlers = [h for grp in app.dispatcher.groups.values() for h in grp]
            kinds = {type(h).__name__ for h in handlers}
            self.assertIn("MessageHandler", kinds)
            self.assertIn("CallbackQueryHandler", kinds)
            for handler in handlers:
                if isinstance(handler, (MessageHandler, CallbackQueryHandler)):
                    self.assertTrue(getattr(handler.callback, "__exception_surface__", False))
            # Names stay intact so log/inspection tooling keeps working.
            names = [getattr(h.callback, "__name__", "") for h in handlers
                     if isinstance(h, MessageHandler)]
            self.assertIn("start_cmd", names)
            # Idempotent: a second install must not double-wrap.
            self.assertEqual(install_exception_surface(app), 0)

        asyncio.get_event_loop().run_until_complete(scenario())

    def test_wrapped_registered_handler_surfaces_real_exception(self):
        from pyrogram.handlers import MessageHandler
        from utils.errors import install_exception_surface

        app = self._build_app()

        async def scenario():
            await asyncio.sleep(0.15)
            install_exception_surface(app)
            handler = next(
                h for grp in app.dispatcher.groups.values() for h in grp
                if isinstance(h, MessageHandler)
                and getattr(h.callback, "__name__", "") == "start_cmd"
            )
            msg = _fake_message()
            msg.reply = AsyncMock(side_effect=OSError("telegram is down"))
            with capture_logs() as logs:
                with self.assertRaises(OSError):
                    await handler.callback(app, msg)
            errors = [r for r in logs.records
                      if r.levelno >= logging.ERROR
                      and r.getMessage() == "handler_exception_surface"]
            self.assertEqual(len(errors), 1)
            self.assertEqual(errors[0].exc_type, "OSError")
            self.assertEqual(errors[0].exc_message, "telegram is down")
            self.assertIn("OSError: telegram is down", _rendered(errors[0])["exc"])

        asyncio.get_event_loop().run_until_complete(scenario())


# ══════════════════════════════════════════════════════════════════════════
# ISSUE 2 — MESSAGE_NOT_MODIFIED
# ══════════════════════════════════════════════════════════════════════════
class MessageNotModifiedDetectionTests(unittest.TestCase):
    def test_detects_pyrogram_exception(self):
        from pyrogram.errors import MessageNotModified
        from utils.errors import is_message_not_modified
        self.assertTrue(is_message_not_modified(MessageNotModified()))

    def test_detects_generic_rpc_error_with_same_id(self):
        from utils.errors import is_message_not_modified

        class FakeRPCError(Exception):
            ID = "MESSAGE_NOT_MODIFIED"

        self.assertTrue(is_message_not_modified(FakeRPCError()))
        self.assertTrue(is_message_not_modified(Exception(
            "Telegram says: [400 MESSAGE_NOT_MODIFIED] - The message was not modified"
        )))

    def test_other_errors_are_not_confused(self):
        from pyrogram.errors import FloodWait, MessageIdInvalid
        from utils.errors import is_message_not_modified
        self.assertFalse(is_message_not_modified(MessageIdInvalid()))
        self.assertFalse(is_message_not_modified(FloodWait()))
        self.assertFalse(is_message_not_modified(ValueError("boom")))


class SafeEditMessageTextTests(unittest.TestCase):
    def test_message_not_modified_is_a_silent_noop(self):
        from pyrogram.errors import MessageNotModified
        from bot.callbacks.callbacks import safe_edit_message_text

        cb = _fake_callback_query()
        cb.edit_message_text = AsyncMock(side_effect=MessageNotModified())

        with capture_logs() as logs:
            result = asyncio.get_event_loop().run_until_complete(
                safe_edit_message_text(cb, "same text")
            )

        self.assertIsNone(result)
        self.assertEqual([r for r in logs.records if r.levelno >= logging.ERROR], [])
        debug = [r for r in logs.records if r.getMessage() == "message_not_modified_ignored"]
        self.assertEqual(len(debug), 1)
        self.assertEqual(debug[0].levelno, logging.DEBUG)

    def test_other_telegram_errors_still_propagate(self):
        from pyrogram.errors import MessageIdInvalid
        from bot.callbacks.callbacks import safe_edit_message_text

        cb = _fake_callback_query()
        cb.edit_message_text = AsyncMock(side_effect=MessageIdInvalid())

        with self.assertRaises(MessageIdInvalid):
            asyncio.get_event_loop().run_until_complete(
                safe_edit_message_text(cb, "new text")
            )

    def test_successful_edit_returns_value_and_forwards_kwargs(self):
        from bot.callbacks.callbacks import safe_edit_message_text

        cb = _fake_callback_query()
        cb.edit_message_text = AsyncMock(return_value="edited")
        result = asyncio.get_event_loop().run_until_complete(
            safe_edit_message_text(cb, "text", reply_markup="kb", disable_web_page_preview=True)
        )
        self.assertEqual(result, "edited")
        cb.edit_message_text.assert_awaited_once_with(
            "text", reply_markup="kb", disable_web_page_preview=True
        )

    def test_all_callback_edits_go_through_the_safe_helper(self):
        src = (Path(__file__).resolve().parents[1]
               / "bot" / "callbacks" / "callbacks.py").read_text(encoding="utf-8")
        # The only direct cb.edit_message_text call is the one inside the helper.
        self.assertEqual(src.count("cb.edit_message_text("), 1)


class AdminCallbackNotModifiedTests(unittest.TestCase):
    """End-to-end: adm:stats / adm:jobs:0 re-taps must not error out."""

    def setUp(self) -> None:
        _ensure_event_loop()
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.config = _make_config(self.tmp)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _register_callback_handler(self):
        from bot.callbacks import callbacks
        from bot.handlers.common import HandlerContext
        from database.database import Database
        from services.rate_limit import RateLimiter
        from services.state import StateStore

        app = MagicMock()
        app.on_callback_query = lambda *a, **k: (lambda fn: fn)
        db = MagicMock(spec=Database)
        state = StateStore(max_queue_size=20)
        rate = RateLimiter(state, self.config)
        ctx = HandlerContext(app, db, state, rate, self.config)

        captured = {}
        real_decorator = app.on_callback_query

        def decorator(*args, **kwargs):
            def wrap(fn):
                captured["on_callback"] = fn
                return fn
            return wrap

        app.on_callback_query = decorator
        callbacks.register(app, ctx)
        assert real_decorator is not None
        return captured["on_callback"]

    def _run_admin_callback(self, data: str, edit_side_effect):
        from database import queries

        on_callback = self._register_callback_handler()
        cb = _fake_callback_query(data=data)
        cb.edit_message_text = AsyncMock(side_effect=edit_side_effect)

        stats = {"users": 3, "jobs": 5, "completed": 4,
                 "failed": 1, "queued": 0, "active": 0}
        with mock.patch.object(queries, "admin_stats", AsyncMock(return_value=stats)), \
             mock.patch.object(queries, "list_recent_jobs", AsyncMock(return_value=[])), \
             mock.patch.object(queries, "count_jobs", AsyncMock(return_value=0)):
            with capture_logs() as logs:
                asyncio.get_event_loop().run_until_complete(on_callback(MagicMock(), cb))
        return cb, logs

    def test_adm_stats_message_not_modified_is_ignored(self):
        from pyrogram.errors import MessageNotModified

        cb, logs = self._run_admin_callback("adm:stats", MessageNotModified())

        self.assertEqual([r for r in logs.records if r.levelno >= logging.ERROR], [])
        # The user is answered normally, never with the "bad callback" alert.
        cb.answer.assert_awaited()
        for call in cb.answer.await_args_list:
            self.assertFalse(call.kwargs.get("show_alert", False))

    def test_adm_jobs_message_not_modified_is_ignored(self):
        from pyrogram.errors import MessageNotModified

        cb, logs = self._run_admin_callback("adm:jobs:0", MessageNotModified())

        self.assertEqual([r for r in logs.records if r.levelno >= logging.ERROR], [])
        cb.answer.assert_awaited()

    def test_other_api_error_is_still_logged_and_handled(self):
        from bot import messages as M
        from pyrogram.errors import MessageIdInvalid

        cb, logs = self._run_admin_callback("adm:stats", MessageIdInvalid())

        errors = [r for r in logs.records if r.levelno >= logging.ERROR]
        self.assertEqual(len(errors), 1)
        record = errors[0]
        self.assertEqual(record.getMessage(), "callback_error")
        self.assertEqual(record.exc_type, "MessageIdInvalid")
        self.assertEqual(record.data, "adm:stats")
        payload = _rendered(record)
        self.assertIn("MessageIdInvalid", payload["exc"])
        self.assertNotIn("NoneType: None", json.dumps(payload))
        cb.answer.assert_awaited_with(M.ERR_BAD_CALLBACK, show_alert=True)


if __name__ == "__main__":
    unittest.main()
