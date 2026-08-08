"""
Regression tests for Pyrogram Client wiring.

Guards against the production crash:

    AttributeError: 'JobStorage' object has no attribute 'open'

which happened because ``app.storage = JobStorage(...)`` overwrote Pyrogram's
session backend. JobStorage is for filesystem job dirs and must never be used
as Client.storage.
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
    """Pyrogram's import path calls asyncio.get_event_loop(); keep one alive."""
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)


_ensure_event_loop()


def _make_config(tmp: Path):
    """Minimal Config-like object for wiring tests (no real env required)."""
    from config import Config, RateLimitConfig

    return Config(
        api_id=12345,
        api_hash="0123456789abcdef0123456789abcdef",
        bot_token="123456:ABC-DEF_fake_token_for_tests",
        database_url="postgresql://u:p@localhost:5432/db",
        redis_url="redis://localhost:6379/0",
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

        # Session backend must be a real Pyrogram Storage, not JobStorage.
        self.assertIsNotNone(app.storage)
        self.assertTrue(hasattr(app.storage, "open"), "session storage must expose open()")
        self.assertTrue(callable(app.storage.open))
        self.assertIsInstance(app.storage, PyroStorage)
        self.assertNotIsInstance(app.storage, JobStorage)

        # workdir points at temp_dir/session
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
        attach_app_services(
            app,
            jobs=object(),
            storage=job_storage,
            state=object(),
            db=object(),
            config=self.config,
        )

        # The critical regression: Client.storage must remain Pyrogram's backend.
        self.assertIs(app.storage, pyro_storage_before)
        self.assertIsNot(app.storage, job_storage)
        self.assertFalse(isinstance(app.storage, JobStorage))
        self.assertTrue(hasattr(app.storage, "open"))

        # JobStorage is available on the dedicated attribute + worker path.
        self.assertIs(app.job_storage, job_storage)

    def test_storage_open_not_jobstorage(self):
        """Simulate the connect path that crashed in production."""
        from main import attach_app_services, build_pyrogram_client
        from services.storage import JobStorage

        app = build_pyrogram_client(self.config)
        job_storage = JobStorage(self.config)
        attach_app_services(
            app,
            jobs=object(),
            storage=job_storage,
            state=object(),
            db=object(),
            config=self.config,
        )

        # This is exactly what Client.connect() does. Must not raise
        # AttributeError: 'JobStorage' object has no attribute 'open'
        open_fn = getattr(app.storage, "open", None)
        self.assertIsNotNone(open_fn)
        self.assertTrue(inspect.iscoroutinefunction(open_fn) or callable(open_fn))

        # Calling open on the real session storage should succeed (or at worst
        # raise a storage-level error, never AttributeError for missing open).
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
        attach_app_services(
            app,
            jobs=object(),
            storage=job_storage,
            state=object(),
            db=object(),
            config=self.config,
        )

        processor = JobProcessor(
            app,
            db=object(),
            state=object(),
            jobs=mock.Mock(),
            storage=job_storage,
            config=self.config,
        )
        self.assertIs(processor.storage, job_storage)
        self.assertIsNot(app.storage, job_storage)

    def test_source_does_not_assign_app_storage_to_jobstorage(self):
        """Static guard: main.py must not contain ``app.storage =`` assignments."""
        main_path = Path(__file__).resolve().parents[1] / "main.py"
        src = main_path.read_text(encoding="utf-8")
        # Allow comments mentioning the anti-pattern; forbid real assignments.
        for line in src.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            self.assertNotRegex(
                stripped,
                r"\bapp\.storage\s*=",
                msg="main.py must not assign to app.storage (Pyrogram session slot)",
            )


class MaxFileSizeDefaultTests(unittest.TestCase):
    def test_default_max_file_size_mb_is_25(self):
        from config import load_config
        import config as config_mod

        env = {
            "API_ID": "12345",
            "API_HASH": "hash",
            "BOT_TOKEN": "1:token",
            "DATABASE_URL": "postgresql://u:p@localhost/db",
            "REDIS_URL": "redis://localhost:6379/0",
            "ADMIN_IDS": "1",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            os.environ.pop("MAX_FILE_SIZE_MB", None)
            config_mod._settings = None
            try:
                cfg = load_config()
                self.assertEqual(cfg.max_file_size, 25 * 1024 * 1024)
            finally:
                config_mod._settings = None


if __name__ == "__main__":
    unittest.main(verbosity=2)
