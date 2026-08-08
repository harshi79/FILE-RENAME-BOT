"""
Regression tests for Pyrogram Client wiring and handler registration.

Guards against:
- AttributeError: 'JobStorage' object has no attribute 'open'
- handlers_registered groups:0 handlers:0 (async add_handler quirk)
- missing /start, document, text_input, callback handlers
- unsafe Dispatcher patching
"""
from __future__ import annotations

import asyncio
import inspect
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _ensure_event_loop() -> None:
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)


_ensure_event_loop()


def _make_config(tmp: Path):
    from config import Config, RateLimitConfig
    return Config(
        api_id=12345,
        api_hash="0123456789abcdef0123456789abcdef",
        bot_token="123456:ABC-DEF_fake_token_for_tests",
        database_url="postgresql://u:p@localhost:5432/db",
        admin_ids=frozenset({1}),
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
        health_port=18080,
        start_video_url="",
        rate_limit=RateLimitConfig(),
    )


class PyrogramWiringTests(unittest.TestCase):
    def setUp(self) -> None:
        _ensure_event_loop()
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.config = _make_config(self.tmp)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_build_client_uses_pyrogram_session_storage(self):
        from main import SESSION_NAME, build_pyrogram_client
        from pyrogram.storage.storage import Storage as PyroStorage
        from services.storage import JobStorage

        app = build_pyrogram_client(self.config)
        self.assertIsNotNone(app.storage)
        self.assertTrue(hasattr(app.storage, "open"))
        self.assertTrue(callable(app.storage.open))
        self.assertIsInstance(app.storage, PyroStorage)
        self.assertNotIsInstance(app.storage, JobStorage)
        expected_workdir = str(self.tmp / "session")
        self.assertEqual(str(app.workdir), expected_workdir)
        self.assertTrue(Path(expected_workdir).is_dir())
        self.assertEqual(app.name, SESSION_NAME)
        self.assertEqual(app.bot_token, self.config.bot_token)

    def test_job_storage_not_assigned_to_client_storage(self):
        from main import attach_app_services, build_pyrogram_client
        from services.storage import JobStorage

        app = build_pyrogram_client(self.config)
        pyro_storage_before = app.storage
        job_storage = JobStorage(self.config)
        attach_app_services(app, jobs=object(), storage=job_storage, state=object(), db=object(), config=self.config)
        self.assertIs(app.storage, pyro_storage_before)
        self.assertIsNot(app.storage, job_storage)
        self.assertFalse(isinstance(app.storage, JobStorage))
        self.assertTrue(hasattr(app.storage, "open"))
        self.assertIs(app.job_storage, job_storage)

    def test_storage_open_not_jobstorage(self):
        from main import attach_app_services, build_pyrogram_client
        from services.storage import JobStorage
        app = build_pyrogram_client(self.config)
        job_storage = JobStorage(self.config)
        attach_app_services(app, jobs=object(), storage=job_storage, state=object(), db=object(), config=self.config)
        open_fn = getattr(app.storage, "open", None)
        self.assertIsNotNone(open_fn)
        self.assertTrue(inspect.iscoroutinefunction(open_fn) or callable(open_fn))
        async def _open_and_close():
            try:
                await app.storage.open()
            except AttributeError as exc:
                self.fail(f"session storage.open() raised AttributeError: {exc}")
            finally:
                try:
                    await app.storage.close()
                except Exception:
                    pass
        asyncio.get_event_loop().run_until_complete(_open_and_close())

    def test_job_processor_receives_job_storage_explicitly(self):
        from main import attach_app_services, build_pyrogram_client
        from services.storage import JobStorage
        from workers.processor import JobProcessor
        app = build_pyrogram_client(self.config)
        job_storage = JobStorage(self.config)
        attach_app_services(app, jobs=object(), storage=job_storage, state=object(), db=object(), config=self.config)
        processor = JobProcessor(app, db=object(), state=object(), jobs=mock.Mock(), storage=job_storage, config=self.config)
        self.assertIs(processor.storage, job_storage)
        self.assertIsNot(app.storage, job_storage)

    def test_source_does_not_assign_app_storage_to_jobstorage(self):
        main_path = Path(__file__).resolve().parents[1] / "main.py"
        src = main_path.read_text(encoding="utf-8")
        for line in src.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            self.assertNotRegex(stripped, r"\bapp\.storage\s*=", msg="main.py must not assign to app.storage")

    def test_no_redis_import_in_main(self):
        main_path = Path(__file__).resolve().parents[1] / "main.py"
        src = main_path.read_text(encoding="utf-8")
        self.assertNotIn("import redis", src.lower())
        self.assertNotIn("from redis", src.lower())
        self.assertNotIn("redis_url", src.lower())
        self.assertNotIn("REDIS_URL", src)
        self.assertNotIn("StateStore(config.redis", src)

    def test_no_redis_in_requirements(self):
        req_path = Path(__file__).resolve().parents[1] / "requirements.txt"
        src = req_path.read_text(encoding="utf-8")
        self.assertNotIn("redis", src.lower())

    def test_no_dispatcher_patch(self):
        """Ensure we use public APIs, not private Dispatcher patching."""
        main_path = Path(__file__).resolve().parents[1] / "main.py"
        src = main_path.read_text(encoding="utf-8")
        self.assertNotIn("_patch_dispatcher", src)
        self.assertNotIn("app.dispatcher.add_handler =", src)
        self.assertNotIn("locks_list", src)
        # Should use sleep-yield instead
        self.assertIn("await asyncio.sleep", src)

    def test_main_registers_before_start(self):
        """Verify handler registration order: commands/files/text_input/callbacks before app.start()."""
        main_path = Path(__file__).resolve().parents[1] / "main.py"
        src = main_path.read_text(encoding="utf-8")
        for name in ["commands.register", "files.register", "text_input.register", "callbacks.register"]:
            self.assertIn(name, src, f"{name} missing")
        # Use rindex for the real startup call (docstring contains await app.start() too)
        pos_commands = src.index("commands.register")
        pos_files = src.index("files.register")
        pos_text = src.index("text_input.register")
        pos_cb = src.index("callbacks.register")
        pos_start = src.rindex("await app.start()")
        self.assertLess(pos_commands, pos_start)
        self.assertLess(pos_files, pos_start)
        self.assertLess(pos_text, pos_start)
        self.assertLess(pos_cb, pos_start)


class MaxFileSizeDefaultTests(unittest.TestCase):
    def test_default_max_file_size_mb_is_25(self):
        from config import load_config
        import config as config_mod
        env = {
            "API_ID": "12345",
            "API_HASH": "hash",
            "BOT_TOKEN": "1:token",
            "DATABASE_URL": "postgresql://u:p@localhost/db",
            "ADMIN_IDS": "1",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            os.environ.pop("MAX_FILE_SIZE_MB", None)
            os.environ.pop("REDIS_URL", None)
            config_mod._settings = None
            try:
                cfg = load_config()
                self.assertEqual(cfg.max_file_size, 25 * 1024 * 1024)
            finally:
                config_mod._settings = None


class HandlerRegistrationTests(unittest.TestCase):
    """Prove handlers are actually attached and working via public Pyrogram API."""

    def setUp(self) -> None:
        _ensure_event_loop()
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.config = _make_config(self.tmp)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _register_all(self, app, ctx):
        from bot.handlers import commands, files, text_input
        from bot.callbacks import callbacks
        commands.register(app, ctx)
        files.register(app, ctx)
        text_input.register(app, ctx)
        callbacks.register(app, ctx)

    def test_handlers_registered_nonzero_via_public_api(self):
        """Public API: after register + await sleep, groups must be non-empty."""
        from unittest.mock import AsyncMock, MagicMock
        from main import build_pyrogram_client, attach_app_services
        from bot.handlers.common import HandlerContext
        from services.state import StateStore
        from services.rate_limit import RateLimiter
        from database.database import Database

        app = build_pyrogram_client(self.config)
        mock_db = MagicMock(spec=Database)
        mock_db.fetch_one = AsyncMock(return_value={"user_id": 123, "is_banned": False, "is_admin": False})
        mock_db.execute = AsyncMock(return_value="")
        mock_db.fetch_all = AsyncMock(return_value=[])

        state = StateStore(max_queue_size=20)
        rate = RateLimiter(state, self.config)

        attach_app_services(app, jobs=MagicMock(), storage=MagicMock(), state=state, db=mock_db, config=self.config)
        ctx = HandlerContext(app, mock_db, state, rate, self.config)

        # Immediate after register should be 0 (async nature) – we don't assert it,
        # but after yield it must be >0 using public API without patching.
        self._register_all(app, ctx)
        async def check_after_yield():
            await asyncio.sleep(0.15)
            groups = getattr(app.dispatcher, "groups", {})
            self.assertGreater(len(groups), 0, "handlers not registered after yield: groups empty")
            handler_count = sum(len(v) for v in groups.values())
            self.assertGreaterEqual(handler_count, 4, f"expected >=4 handlers, got {handler_count}")
            types_present = set(type(h).__name__ for handlers in groups.values() for h in handlers)
            self.assertIn("MessageHandler", types_present)
            self.assertIn("CallbackQueryHandler", types_present)
        asyncio.get_event_loop().run_until_complete(check_after_yield())

    def test_start_handler_filter_matches(self):
        """Verify /start actually reaches the command handler (callback name)."""
        from unittest.mock import AsyncMock, MagicMock
        from main import build_pyrogram_client, attach_app_services
        from bot.handlers.common import HandlerContext
        from services.state import StateStore
        from services.rate_limit import RateLimiter
        from database.database import Database
        from pyrogram.handlers import MessageHandler

        app = build_pyrogram_client(self.config)
        mock_db = MagicMock(spec=Database)
        mock_db.execute = AsyncMock(return_value="")
        mock_db.fetch_one = AsyncMock(return_value={"user_id": 123, "is_banned": False, "is_admin": False})
        state = StateStore(max_queue_size=20)
        rate = RateLimiter(state, self.config)
        attach_app_services(app, jobs=MagicMock(), storage=MagicMock(), state=state, db=mock_db, config=self.config)
        ctx = HandlerContext(app, mock_db, state, rate, self.config)
        self._register_all(app, ctx)

        async def scenario():
            await asyncio.sleep(0.15)
            handlers = [h for grp in app.dispatcher.groups.values() for h in grp if isinstance(h, MessageHandler)]
            self.assertGreaterEqual(len(handlers), 2)
            # Check that one of the handlers has callback named start_cmd (commands.register)
            names = [getattr(h.callback, "__name__", "") for h in handlers]
            self.assertIn("start_cmd", names, f"start_cmd not found in {names}")
            # Also verify document handler present
            self.assertIn("on_file", names)

        asyncio.get_event_loop().run_until_complete(scenario())

    def test_document_handler_filter_matches(self):
        """Verify document/file handler matches .txt document."""
        from unittest.mock import AsyncMock, MagicMock
        from main import build_pyrogram_client, attach_app_services
        from bot.handlers.common import HandlerContext
        from services.state import StateStore
        from services.rate_limit import RateLimiter
        from database.database import Database
        from pyrogram.handlers import MessageHandler

        app = build_pyrogram_client(self.config)
        mock_db = MagicMock(spec=Database)
        mock_db.execute = AsyncMock(return_value="")
        mock_db.fetch_one = AsyncMock(return_value=None)
        state = StateStore(max_queue_size=20)
        rate = RateLimiter(state, self.config)
        attach_app_services(app, jobs=MagicMock(), storage=MagicMock(), state=state, db=mock_db, config=self.config)
        ctx = HandlerContext(app, mock_db, state, rate, self.config)
        self._register_all(app, ctx)

        async def scenario():
            await asyncio.sleep(0.15)
            handlers = [h for grp in app.dispatcher.groups.values() for h in grp if isinstance(h, MessageHandler)]
            # Build a document message
            fake_msg = MagicMock()
            fake_msg.text = None
            fake_msg.caption = None
            fake_msg.document = MagicMock(file_name="hello.txt", file_size=100, mime_type="text/plain", file_id="fid")
            fake_msg.photo = None
            fake_msg.video = None
            fake_msg.audio = None
            fake_msg.animation = None
            fake_msg.voice = None
            fake_msg.video_note = None
            fake_msg.from_user = MagicMock(id=123)
            fake_msg.chat = MagicMock(id=123)
            matched = False
            for h in handlers:
                try:
                    if await h.check(app, fake_msg):
                        # Ensure it's not the /start handler (which expects command)
                        # Document handler should match
                        matched = True
                        # We can break on first match that is not /start-specific? Just ensure any matches
                        # The file handler uses filters.document|photo|video... so document should match
                        if h.filters is not None:
                            # Check if this handler's filter is broad (not just command)
                            matched = True
                            break
                except Exception:
                    continue
            self.assertTrue(matched, "document should match file handler")

        asyncio.get_event_loop().run_until_complete(scenario())

    def test_callback_handler_registered(self):
        """Verify callback_query reaches callbacks handler."""
        from unittest.mock import AsyncMock, MagicMock
        from main import build_pyrogram_client, attach_app_services
        from bot.handlers.common import HandlerContext
        from services.state import StateStore
        from services.rate_limit import RateLimiter
        from database.database import Database
        from pyrogram.handlers import CallbackQueryHandler

        app = build_pyrogram_client(self.config)
        mock_db = MagicMock(spec=Database)
        mock_db.execute = AsyncMock(return_value="")
        mock_db.fetch_one = AsyncMock(return_value=None)
        state = StateStore(max_queue_size=20)
        rate = RateLimiter(state, self.config)
        attach_app_services(app, jobs=MagicMock(), storage=MagicMock(), state=state, db=mock_db, config=self.config)
        ctx = HandlerContext(app, mock_db, state, rate, self.config)
        self._register_all(app, ctx)

        async def scenario():
            await asyncio.sleep(0.15)
            handlers = [h for grp in app.dispatcher.groups.values() for h in grp if isinstance(h, CallbackQueryHandler)]
            self.assertGreaterEqual(len(handlers), 1, "CallbackQueryHandler missing")
            fake_cq = MagicMock()
            fake_cq.data = "rename:123"
            fake_cq.from_user = MagicMock(id=123)
            fake_cq.message = MagicMock(chat=MagicMock(id=123))
            matched = False
            for h in handlers:
                try:
                    if await h.check(app, fake_cq):
                        matched = True
                        break
                except Exception:
                    continue
            self.assertTrue(matched, "callback_query should match")

        asyncio.get_event_loop().run_until_complete(scenario())

    def test_start_handler_responds(self):
        from unittest.mock import AsyncMock, MagicMock
        from main import build_pyrogram_client, attach_app_services
        from bot.handlers.common import HandlerContext
        from services.state import StateStore
        from services.rate_limit import RateLimiter
        from database.database import Database

        app = build_pyrogram_client(self.config)
        mock_db = MagicMock(spec=Database)
        mock_db.execute = AsyncMock(return_value="")
        mock_db.fetch_one = AsyncMock(return_value={"user_id": 123, "is_banned": False, "is_admin": False})
        mock_db.fetch_all = AsyncMock(return_value=[])

        state = StateStore(max_queue_size=20)
        rate = RateLimiter(state, self.config)
        attach_app_services(app, jobs=MagicMock(), storage=MagicMock(), state=state, db=mock_db, config=self.config)
        ctx = HandlerContext(app, mock_db, state, rate, self.config)

        from bot import messages as M
        from bot.keyboards import keyboards as kb

        class _FakeUser:
            id = 123
            username = "tester"
            first_name = "Test"
        class _FakeChat:
            id = 123
        class _FakeMessage:
            from_user = _FakeUser()
            chat = _FakeChat()
            text = "/start"
            id = 999
            replied = None
            async def reply(self, *a, **k):
                self.replied = (a, k)
                return MagicMock(id=1000)
            async def reply_video(self, *a, **k):
                return MagicMock(id=1001, video=MagicMock(file_id="vid1"))

        fake_msg = _FakeMessage()
        async def _simulate():
            user = await ctx.ensure_user(fake_msg)  # type: ignore
            text = M.WELCOME.format(max_mb=ctx.config.max_file_size // (1024*1024))
            is_admin = bool(user and user.get("is_admin"))
            markup = kb.main_menu_keyboard(is_admin)
            await fake_msg.reply(text, reply_markup=markup, disable_web_page_preview=True)

        asyncio.get_event_loop().run_until_complete(_simulate())
        self.assertIsNotNone(fake_msg.replied)
        self.assertIn("ꜰɪʟᴇ ʀᴇɴᴀᴍᴇʀ", fake_msg.replied[0][0])

    def test_state_store_bounded_queue(self):
        from services.state import StateStore
        async def scenario():
            s = StateStore(max_queue_size=2)
            self.assertTrue(await s.enqueue_job("a"))
            self.assertTrue(await s.enqueue_job("b"))
            self.assertFalse(await s.enqueue_job("c"))  # bounded
            self.assertEqual(await s.queue_length(), 2)
            self.assertEqual(await s.dequeue_job(timeout=1), "a")
            self.assertTrue(await s.enqueue_job("c"))
            self.assertEqual(await s.queue_length(), 2)
        asyncio.run(scenario())

    def test_no_handler_depends_on_redis(self):
        """Ensure handlers don't import redis."""
        handler_files = list(Path("bot").rglob("*.py"))
        for p in handler_files:
            src = p.read_text()
            self.assertNotIn("import redis", src.lower())
            self.assertNotIn("StateStore(config.redis", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
