"""
Regression tests for the /start handler parse_mode bug.

Production symptom:
    start_video_failed: Invalid parse mode "html"
    ValueError: Invalid parse mode "html"

Root cause (Pyrogram 2.0.106): passing the *string* ``"html"`` to the
Client's ``parse_mode`` makes ``parse_text_entities`` raise
``ValueError: Invalid parse mode "html"``. Pyrogram 2.0.106 only accepts
the ``pyrogram.enums.ParseMode`` enum (e.g. ``ParseMode.HTML``).

These tests pin:
- the client is built with the supported ``ParseMode.HTML`` enum,
- the invalid ``"html"`` string cannot remain in the code,
- a real ``parse_text_entities`` call succeeds with the configured mode,
- /start replies successfully when video + text are valid,
- /start still sends the welcome *text* (and keyboard) when the optional
  start video fails — the video must NEVER abort the welcome message.
"""
from __future__ import annotations

import asyncio
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


def _make_config(tmp: Path, start_video_url: str = ""):
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
        health_port=0,
        start_video_url=start_video_url,
        rate_limit=RateLimitConfig(),
    )


def _build_running_client(tmp: Path, start_video_url: str):
    """Build a client + HandlerContext wired exactly like production startup."""
    from unittest.mock import AsyncMock, MagicMock
    from main import build_pyrogram_client, attach_app_services
    from bot.handlers.common import HandlerContext
    from services.state import StateStore
    from services.rate_limit import RateLimiter
    from database.database import Database

    config = _make_config(tmp, start_video_url)
    app = build_pyrogram_client(config)

    mock_db = MagicMock(spec=Database)
    mock_db.execute = AsyncMock(return_value="")
    mock_db.fetch_one = AsyncMock(
        return_value={"user_id": 123, "is_banned": False, "is_admin": False}
    )
    mock_db.fetch_all = AsyncMock(return_value=[])

    state = StateStore(max_queue_size=20)
    rate = RateLimiter(state, config)
    attach_app_services(
        app, jobs=MagicMock(), storage=MagicMock(),
        state=state, db=mock_db, config=config,
    )
    ctx = HandlerContext(app, mock_db, state, rate, config)

    # Register exactly like production.
    from bot.handlers import commands, files, text_input
    from bot.callbacks import callbacks
    commands.register(app, ctx)
    files.register(app, ctx)
    text_input.register(app, ctx)
    callbacks.register(app, ctx)
    return app, config


def _fake_start_message():
    from unittest.mock import MagicMock
    msg = MagicMock()
    msg.text = "/start"
    msg.from_user = MagicMock(id=123)
    msg.chat = MagicMock(id=123)
    msg.reply = mock.AsyncMock()
    msg.reply_video = mock.AsyncMock()
    return msg


class StartParseModeTests(unittest.TestCase):
    def setUp(self) -> None:
        _ensure_event_loop()
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # ── parse_mode configuration ─────────────────────────────────────
    def test_client_uses_supported_parse_mode_enum(self):
        """build_pyrogram_client must set the ParseMode enum, not a string."""
        from pyrogram.enums import ParseMode
        from main import build_pyrogram_client
        app = build_pyrogram_client(_make_config(self.tmp))
        self.assertIs(app.parse_mode, ParseMode.HTML)
        self.assertNotIsInstance(app.parse_mode, str)

    def test_parse_text_entities_succeeds_with_configured_mode(self):
        """The configured mode must be accepted by Pyrogram 2.0.106."""
        from pyrogram.utils import parse_text_entities
        from main import build_pyrogram_client
        app = build_pyrogram_client(_make_config(self.tmp))

        async def scenario():
            res = await parse_text_entities(
                app, "🎬 <b>ꜰɪʟᴇ</b> ᴛᴇxᴛ", None, None,
            )
            return res["message"]

        text = asyncio.get_event_loop().run_until_complete(scenario())
        self.assertIn("ꜰɪʟᴇ", text)

    def test_invalid_html_string_cannot_remain(self):
        """No 'html' string literal may be passed as parse_mode anywhere."""
        root = Path(__file__).resolve().parents[1]
        bad = []
        for py in root.rglob("*.py"):
            if "site-packages" in str(py) or ".git" in str(py):
                continue
            if py.name.startswith("test_"):
                continue  # tests may legitimately reference the bad literal
            src = py.read_text(encoding="utf-8")
            for i, line in enumerate(src.splitlines(), 1):
                if "parse_mode" in line and "'html'" in line or "parse_mode" in line and '"html"' in line:
                    bad.append(f"{py.relative_to(root)}:{i}: {line.strip()}")
        self.assertEqual(bad, [], "string 'html' parse_mode must not remain:\n" + "\n".join(bad))

    # ── /start flow ──────────────────────────────────────────────────
    def test_start_valid_config_replies_via_video(self):
        """/start with a working video sends video + caption and returns."""
        from pyrogram.handlers import MessageHandler
        from unittest.mock import MagicMock

        app, config = _build_running_client(self.tmp, "https://files.catbox.moe/5qz09e.mp4")
        sent = MagicMock(video=MagicMock(file_id="fid_123"))

        async def scenario():
            await asyncio.sleep(0.15)
            start = next(
                h for grp in app.dispatcher.groups.values()
                for h in grp
                if isinstance(h, MessageHandler)
                and getattr(h.callback, "__name__", "") == "start_cmd"
            )
            msg = _fake_start_message()
            msg.reply_video.return_value = sent
            await start.callback(app, msg)
            msg.reply_video.assert_awaited_once()
            # text-only fallback must NOT run when video succeeds
            msg.reply.assert_not_awaited()
            self.assertEqual(app.start_video_file_id, "fid_123")

        asyncio.get_event_loop().run_until_complete(scenario())

    def test_start_video_failure_still_sends_welcome_text(self):
        """A failed optional start video must NOT abort the welcome text."""
        from pyrogram.handlers import MessageHandler

        app, config = _build_running_client(self.tmp, "https://files.catbox.moe/5qz09e.mp4")

        async def scenario():
            await asyncio.sleep(0.15)
            start = next(
                h for grp in app.dispatcher.groups.values()
                for h in grp
                if isinstance(h, MessageHandler)
                and getattr(h.callback, "__name__", "") == "start_cmd"
            )
            msg = _fake_start_message()
            msg.reply_video.side_effect = ValueError("Invalid parse mode")  # simulated failure
            await start.callback(app, msg)
            # Welcome text must still be sent with the keyboard.
            msg.reply.assert_awaited_once()
            args, kwargs = msg.reply.call_args
            self.assertIn("ꜰɪʟᴇ", args[0])
            self.assertIsNotNone(kwargs.get("reply_markup"))

        asyncio.get_event_loop().run_until_complete(scenario())

    def test_start_without_video_sends_welcome_text(self):
        """/start with no configured video simply sends the welcome text."""
        from pyrogram.handlers import MessageHandler

        app, config = _build_running_client(self.tmp, "")

        async def scenario():
            await asyncio.sleep(0.15)
            start = next(
                h for grp in app.dispatcher.groups.values()
                for h in grp
                if isinstance(h, MessageHandler)
                and getattr(h.callback, "__name__", "") == "start_cmd"
            )
            msg = _fake_start_message()
            await start.callback(app, msg)
            msg.reply_video.assert_not_awaited()
            msg.reply.assert_awaited_once()

        asyncio.get_event_loop().run_until_complete(scenario())


if __name__ == "__main__":
    unittest.main()
