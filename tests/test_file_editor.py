"""
Regression tests for the reply-based File Editor feature.

Covers the pure core (core.file_editor) plus handler-level flows with mocked
Telegram I/O: /paste, /replace (inline + follow-up), /detail, reply targeting,
unsupported-file rejection, Telegram branding detection (including Python
decorator false-positive guards), temp-file cleanup and unchanged originals.
"""
from __future__ import annotations

import asyncio
import dataclasses
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import file_editor as ed
from core.file_editor import (
    EditorError,
    LineSelector,
    detect_branding,
    detect_encoding,
    parse_line_selector,
    parse_replace_command,
    paste_lines,
    replace_lines,
    split_message_lines,
)


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
        api_hash="a" * 32,
        bot_token="123456:fake",
        database_url="postgresql://u:p@localhost/db",
        admin_ids=frozenset({1}),
        max_file_size=25 * 1024 * 1024,
        max_global_active_jobs=2,
        max_active_jobs_per_user=1,
        max_retries=1,
        job_timeout=300,
        max_queue_size=5,
        history_page_size=8,
        admin_page_size=10,
        temp_dir=tmp,
        health_host="127.0.0.1",
        health_port=0,
        start_video_url="",
        rate_limit=RateLimitConfig(),
    )


# ──────────────────────────────────────────────────────────────────────────
# Core: selector parsing
# ──────────────────────────────────────────────────────────────────────────
class SelectorTests(unittest.TestCase):
    def test_single(self):
        self.assertEqual(parse_line_selector("1"), LineSelector("single", 1, 1))
        self.assertEqual(parse_line_selector("42"), LineSelector("single", 42, 42))

    def test_range_inclusive(self):
        self.assertEqual(parse_line_selector("1-10"), LineSelector("range", 1, 10))
        self.assertEqual(parse_line_selector("10-20"), LineSelector("range", 10, 20))
        self.assertEqual(parse_line_selector("1-1"), LineSelector("range", 1, 1))

    def test_full_case_insensitive(self):
        self.assertEqual(parse_line_selector("full"), LineSelector("full"))
        self.assertEqual(parse_line_selector("FULL"), LineSelector("full"))
        self.assertEqual(parse_line_selector("  Full  "), LineSelector("full"))

    def test_invalid(self):
        for bad in ("", "0", "-1", "5-2", "abc", "1-", "-3", "1 2", "1.5", "1-2-3"):
            with self.assertRaises(EditorError):
                parse_line_selector(bad)

    def test_width(self):
        self.assertEqual(parse_line_selector("3").width, 1)
        self.assertEqual(parse_line_selector("2-4").width, 3)
        self.assertEqual(parse_line_selector("full").width, -1)


# ──────────────────────────────────────────────────────────────────────────
# Core: /paste
# ──────────────────────────────────────────────────────────────────────────
class PasteTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, content: bytes, name: str = "sample.txt") -> Path:
        p = self.root / name
        p.write_bytes(content)
        return p

    def test_paste_single_line_exact(self):
        path = self._write(b"line one\nline two\nline three\n")
        res = paste_lines(path, parse_line_selector("2"))
        self.assertEqual(res.lines, ["line two"])
        self.assertFalse(res.truncated)
        self.assertEqual(res.total_lines, 3)

    def test_paste_range_exact_order(self):
        path = self._write(b"line one\nline two\nline three\nline four\n")
        res = paste_lines(path, parse_line_selector("1-3"))
        self.assertEqual(res.lines, ["line one", "line two", "line three"])

    def test_paste_mid_range(self):
        path = self._write(b"a\nb\nc\nd\ne\n")
        res = paste_lines(path, parse_line_selector("2-4"))
        self.assertEqual(res.lines, ["b", "c", "d"])

    def test_paste_full(self):
        path = self._write(b"alpha\nbeta\ngamma\n")
        res = paste_lines(path, parse_line_selector("full"))
        self.assertEqual(res.lines, ["alpha", "beta", "gamma"])

    def test_paste_preserves_spaces_and_unicode(self):
        path = self._write("  indented  \nplain\n  spaced out  \n".encode("utf-8"))
        res = paste_lines(path, parse_line_selector("1-3"))
        self.assertEqual(res.lines, ["  indented  ", "plain", "  spaced out  "])

    def test_paste_preserves_unicode_and_case(self):
        path = self._write("Ünïcödé 中文 🚀\nMiXeD CaSe\n".encode("utf-8"))
        res = paste_lines(path, parse_line_selector("1"))
        self.assertEqual(res.lines, ["Ünïcödé 中文 🚀"])

    def test_paste_empty_lines(self):
        path = self._write(b"a\n\n\nb\n")
        res = paste_lines(path, parse_line_selector("1-4"))
        self.assertEqual(res.lines, ["a", "", "", "b"])

    def test_paste_single_out_of_bounds(self):
        path = self._write(b"a\nb\nc\n")
        with self.assertRaises(EditorError) as ctx:
            paste_lines(path, parse_line_selector("5"))
        self.assertIn("ʟɪɴᴇ ᴏᴜᴛ ᴏғ ʙᴏᴜɴᴅs", str(ctx.exception))

    def test_paste_range_out_of_bounds(self):
        path = self._write(b"a\nb\nc\n")
        with self.assertRaises(EditorError):
            paste_lines(path, parse_line_selector("2-9"))

    def test_paste_huge_output_truncated_safely(self):
        # A file far larger than the Telegram budget must truncate, not crash.
        path = self._write((b"x" * 1000 + b"\n") * 3000)
        res = paste_lines(path, parse_line_selector("full"))
        self.assertTrue(res.truncated)
        msgs = ed.render_paste("📄 ғᴜʟʟ ᴄᴏɴᴛᴇɴᴛ", res.lines, res.truncated)
        self.assertLessEqual(len(msgs), ed.MAX_MSG_COUNT)
        for m in msgs:
            self.assertLessEqual(len(m), 4096)
        self.assertTrue(any("ᴛʀᴜɴᴄᴀᴛᴇᴅ" in m for m in msgs))

    def test_render_paste_escapes_html(self):
        path = self._write(b"<script>alert('x')</script> & more\n")
        res = paste_lines(path, parse_line_selector("1"))
        msgs = ed.render_paste("h", res.lines, False)
        self.assertIn("&lt;script&gt;", msgs[0])
        self.assertNotIn("<script>", msgs[0])

    def test_paste_empty_file(self):
        path = self._write(b"")
        res = paste_lines(path, parse_line_selector("full"))
        self.assertEqual(res.lines, [])
        with self.assertRaises(EditorError):
            paste_lines(path, parse_line_selector("1"))


# ──────────────────────────────────────────────────────────────────────────
# Core: /replace
# ──────────────────────────────────────────────────────────────────────────
class ReplaceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, content: bytes, name: str = "sample.txt") -> Path:
        p = self.root / name
        p.write_bytes(content)
        return p

    def _read(self, path: Path) -> bytes:
        return path.read_bytes()

    def test_replace_single_line(self):
        src = self._write(b"Hello\nWorld\nTest\n")
        out = self.root / "out.txt"
        res = replace_lines(src, parse_line_selector("2"), ["New World"], out)
        self.assertEqual(res.replaced, 1)
        self.assertEqual(self._read(out), b"Hello\nNew World\nTest\n")

    def test_replace_range(self):
        src = self._write(b"l1\nl2\nl3\nl4\nl5\n")
        out = self.root / "out.txt"
        replace_lines(src, parse_line_selector("2-4"), ["AAA", "BBB", "CCC"], out)
        self.assertEqual(self._read(out), b"l1\nAAA\nBBB\nCCC\nl5\n")

    def test_replace_preserves_spaces_and_special_chars(self):
        src = self._write(b"a\nb\nc\n")
        out = self.root / "out.txt"
        replace_lines(src, parse_line_selector("2"), ["  © Yori Team  <3  "], out)
        self.assertEqual(self._read(out), "a\n  © Yori Team  <3  \nc\n".encode("utf-8"))

    def test_replace_empty_replacement_line(self):
        src = self._write(b"a\nb\nc\n")
        out = self.root / "out.txt"
        replace_lines(src, parse_line_selector("2"), [""], out)
        self.assertEqual(self._read(out), b"a\n\nc\n")

    def test_replace_empty_lines_range(self):
        src = self._write(b"a\nb\nc\nd\n")
        out = self.root / "out.txt"
        replace_lines(src, parse_line_selector("2-3"), ["", ""], out)
        self.assertEqual(self._read(out), b"a\n\n\nd\n")

    def test_replace_original_never_modified(self):
        src = self._write(b"Hello\nWorld\nTest\n")
        original = self._read(src)
        out = self.root / "out.txt"
        replace_lines(src, parse_line_selector("2"), ["New World"], out)
        self.assertEqual(self._read(src), original)

    def test_replace_count_mismatch_no_modification(self):
        src = self._write(b"a\nb\nc\nd\n")
        original = self._read(src)
        out = self.root / "out.txt"
        with self.assertRaises(EditorError) as ctx:
            replace_lines(src, parse_line_selector("2-4"), ["only one"], out)
        self.assertIn("ᴍɪsᴍᴀᴛᴄʜ", str(ctx.exception))
        self.assertEqual(self._read(src), original)
        self.assertFalse(out.exists())

    def test_replace_invalid_range_no_modification(self):
        src = self._write(b"a\nb\nc\n")
        original = self._read(src)
        out = self.root / "out.txt"
        with self.assertRaises(EditorError):
            replace_lines(src, parse_line_selector("3-1"), ["x", "y"], out)
        with self.assertRaises(EditorError):
            replace_lines(src, parse_line_selector("0"), ["x"], out)
        self.assertEqual(self._read(src), original)
        self.assertFalse(out.exists())

    def test_replace_out_of_bounds_no_modification(self):
        src = self._write(b"a\nb\nc\n")
        original = self._read(src)
        out = self.root / "out.txt"
        with self.assertRaises(EditorError) as ctx:
            replace_lines(src, parse_line_selector("2-9"), ["x"] * 8, out)
        self.assertIn("ʟɪɴᴇ ᴏᴜᴛ ᴏғ ʙᴏᴜɴᴅs", str(ctx.exception))
        self.assertEqual(self._read(src), original)
        self.assertFalse(out.exists())

    def test_replace_full_rejected(self):
        src = self._write(b"a\n")
        out = self.root / "out.txt"
        with self.assertRaises(EditorError):
            replace_lines(src, parse_line_selector("full"), ["x"], out)
        self.assertFalse(out.exists())

    def test_replace_keeps_crlf_and_terminator(self):
        src = self._write(b"a\r\nb\r\nc\r\n")
        out = self.root / "out.txt"
        replace_lines(src, parse_line_selector("2"), ["NEW"], out)
        self.assertEqual(self._read(out), b"a\r\nNEW\r\nc\r\n")

    def test_replace_last_line_without_trailing_newline(self):
        src = self._write(b"a\nb\nc")
        out = self.root / "out.txt"
        replace_lines(src, parse_line_selector("3"), ["END"], out)
        self.assertEqual(self._read(out), b"a\nb\nEND")

    def test_replace_first_line(self):
        src = self._write(b"a\nb\nc\n")
        out = self.root / "out.txt"
        replace_lines(src, parse_line_selector("1"), ["START"], out)
        self.assertEqual(self._read(out), b"START\nb\nc\n")

    def test_verify_output(self):
        out = self.root / "out.txt"
        out.write_bytes(b"a\nb\n")
        ed.verify_output(out, 2)  # no raise
        with self.assertRaises(EditorError):
            ed.verify_output(out, 3)
        with self.assertRaises(EditorError):
            ed.verify_output(self.root / "missing.txt", 0)

    def test_split_message_lines(self):
        self.assertEqual(split_message_lines("a\nb\nc"), ["a", "b", "c"])
        self.assertEqual(split_message_lines("a\nb\n"), ["a", "b"])
        self.assertEqual(split_message_lines("  a  \nb  "), ["  a  ", "b  "])
        self.assertEqual(split_message_lines(""), [""])
        self.assertEqual(split_message_lines("\n"), [""])
        self.assertEqual(split_message_lines("a\rb"), ["a\rb"])  # \r preserved
        self.assertEqual(split_message_lines("a\n\nb"), ["a", "", "b"])

    def test_parse_replace_command(self):
        cmd = parse_replace_command("/replace 2 New World")
        self.assertEqual(cmd.selector, LineSelector("single", 2, 2))
        self.assertEqual(cmd.inline_lines, ["New World"])
        self.assertFalse(cmd.needs_followup)

        cmd = parse_replace_command("/replace 1   spaced   out")
        self.assertEqual(cmd.inline_lines, ["  spaced   out"])

        cmd = parse_replace_command("/replace 2-4\nAAA\nBBB\nCCC")
        self.assertEqual(cmd.selector, LineSelector("range", 2, 4))
        self.assertEqual(cmd.inline_lines, ["AAA", "BBB", "CCC"])
        self.assertFalse(cmd.needs_followup)

        cmd = parse_replace_command("/replace 2-4")
        self.assertTrue(cmd.needs_followup)
        self.assertEqual(cmd.inline_lines, [])

        cmd = parse_replace_command("/replace 1")
        self.assertTrue(cmd.needs_followup)

        with self.assertRaises(EditorError):
            parse_replace_command("/replace 2-4 trailing text")
        with self.assertRaises(EditorError):
            parse_replace_command("/replace")
        with self.assertRaises(EditorError):
            parse_replace_command("/replace nope x")

    def test_editor_output_name_safe(self):
        self.assertEqual(ed.editor_output_name("notes.txt"), "notes.txt")
        self.assertEqual(ed.editor_output_name("../../etc/passwd"), "passwd")
        self.assertEqual(ed.editor_output_name("..\\..\\evil.exe"), "evil.exe")
        self.assertEqual(ed.editor_output_name("a/b/c.py"), "c.py")
        self.assertEqual(ed.editor_output_name(".."), "edited.txt")
        self.assertEqual(ed.editor_output_name(""), "edited.txt")
        self.assertNotIn("/", ed.editor_output_name("../x/../y.txt"))
        self.assertNotIn("\\", ed.editor_output_name("..\\x\\y.txt"))


# ──────────────────────────────────────────────────────────────────────────
# Core: /detail, encoding, branding
# ──────────────────────────────────────────────────────────────────────────
class DetailTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_count_lines(self):
        p = self.root / "empty"
        p.write_bytes(b"")
        self.assertEqual(ed.count_lines(p), 0)
        p = self.root / "f"; p.write_bytes(b"a\nb\n")
        self.assertEqual(ed.count_lines(p), 2)
        p.write_bytes(b"a\nb\nc")
        self.assertEqual(ed.count_lines(p), 3)
        p.write_bytes(b"a\n\n")
        self.assertEqual(ed.count_lines(p), 2)

    def test_detect_encoding(self):
        import codecs
        p = self.root / "f"
        p.write_bytes("héllo".encode("utf-8"))
        self.assertEqual(detect_encoding(p), "UTF-8")
        p.write_bytes(b"\xff\xfe" + "héllo".encode("utf-16-le"))
        self.assertEqual(detect_encoding(p), "UTF-16-LE")
        p.write_bytes(codecs.BOM_UTF8 + "hi".encode("utf-8"))
        self.assertEqual(detect_encoding(p), "UTF-8 (BOM)")
        p.write_bytes(b"caf\xe9")  # é in latin-1, valid cp1252 too
        self.assertEqual(detect_encoding(p), "CP1252")
        p.write_bytes(b"caf\x81")  # 0x81 invalid in cp1252 -> latin-1
        self.assertEqual(detect_encoding(p), "latin-1")

    def test_file_detail_fields(self):
        p = self.root / "example.py"
        p.write_bytes(b"# hi\nimport os\nprint('x')\n")
        d = ed.file_detail(p, "example.py")
        self.assertEqual(d["filename"], "example.py")
        self.assertEqual(d["size"], p.stat().st_size)
        self.assertEqual(d["total_lines"], 3)
        self.assertEqual(d["encoding"], "UTF-8")
        self.assertIsInstance(d["branding"], list)

    def test_render_detail(self):
        p = self.root / "example.py"
        p.write_bytes(b"line one\nline two\n")
        d = ed.file_detail(p, "example.py")
        text = ed.render_detail(d)
        self.assertIn("ᴅᴇᴛᴀɪʟs", text)
        self.assertIn("example.py", text)
        self.assertIn("2", text)
        self.assertIn("UTF-8", text)


class BrandingTests(unittest.TestCase):
    def test_detects_handle(self):
        found = detect_branding(["check @OldChannel now", "plain"])
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].line, 1)
        self.assertEqual(found[0].value, "@OldChannel")

    def test_detects_url_forms(self):
        lines = [
            "see t.me/OldChannel",
            "see https://t.me/OldChannel",
            "see http://t.me/OldChannel",
            "see www.t.me/OldChannel",
            "markdown [link](https://t.me/OldChannel)",
            "deep tg://resolve?domain=OldChannel",
        ]
        found = detect_branding(lines)
        values = [m.value for m in found]
        self.assertIn("t.me/OldChannel", values)
        self.assertIn("https://t.me/OldChannel", values)
        self.assertIn("http://t.me/OldChannel", values)
        self.assertIn("www.t.me/OldChannel", values)
        self.assertIn("tg://resolve?domain=OldChannel", values)
        # Six occurrences (the markdown URL is a real second occurrence) and
        # no overlapping duplicate matches from the alternation.
        self.assertEqual(len(found), 6)

    def test_line_numbers_and_total(self):
        lines = ["clean", "@ChanOne", "clean", "https://t.me/ChanTwo", "@ChanOne"]
        found = detect_branding(lines)
        self.assertEqual([(m.line, m.value) for m in found], [
            (2, "@ChanOne"),
            (4, "https://t.me/ChanTwo"),
            (5, "@ChanOne"),
        ])

    def test_python_decorators_not_detected(self):
        lines = [
            "@staticmethod",
            "    @property",
            "@app.route",
            "    @app.route('/x')",
            "@cache",
            "def foo(): pass",
        ]
        found = detect_branding(lines)
        self.assertEqual(found, [])

    def test_emails_not_detected(self):
        found = detect_branding(["mail me at user@OldChannel.com please"])
        self.assertEqual(found, [])

    def test_short_handles_not_detected(self):
        found = detect_branding(["@ab", "see @xy1 here"])
        self.assertEqual(found, [])

    def test_handle_in_sentence_detected(self):
        found = detect_branding(["join @OldChannel for updates"])
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].value, "@OldChannel")


# ──────────────────────────────────────────────────────────────────────────
# Handler level (mocked Telegram)
# ──────────────────────────────────────────────────────────────────────────
class _FakeUser:
    id = 1
    username = "tester"
    first_name = "Test"


class _FakeChat:
    id = 123


def _fake_reply_doc(filename="example.py", size=64, mime="text/x-python",
                    file_id="FID", content=b"line one\nline two\nline three\n"):
    return SimpleNamespace(
        document=SimpleNamespace(
            file_name=filename, file_size=size, mime_type=mime,
            file_id=file_id, file_ref=None,
            attributes=[SimpleNamespace(file_name=filename)],
        ),
        photo=None, video=None, audio=None, animation=None, voice=None,
        sticker=None, video_note=None, contact=None, location=None,
        chat=SimpleNamespace(id=123), id=42,
    )


class _FakeMessage:
    def __init__(self, text, reply_to=None, uid=1, cid=123):
        self.text = text
        self.caption = None
        self.from_user = SimpleNamespace(id=uid, username="t", first_name="T")
        self.chat = SimpleNamespace(id=cid)
        self.reply_to_message = reply_to
        self.reply = AsyncMock(return_value=SimpleNamespace(id=1000))


class EditorHandlerTests(unittest.TestCase):
    def setUp(self):
        _ensure_event_loop()
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.config = _make_config(self.tmp)
        self.job_config = dataclasses.replace(self.config, temp_dir=self.tmp / "jobroot")
        self.app = None

    def tearDown(self):
        self._tmp.cleanup()

    def _build_app(self):
        from bot.callbacks import callbacks
        from bot.handlers import commands, file_editor, files, text_input
        from bot.handlers.common import HandlerContext
        from database.database import Database
        from main import attach_app_services, build_pyrogram_client
        from services.rate_limit import RateLimiter
        from services.state import StateStore
        from services.storage import JobStorage

        app = build_pyrogram_client(self.config)
        db = MagicMock(spec=Database)
        db.execute = AsyncMock(return_value="")
        db.fetch_one = AsyncMock(return_value={"user_id": 1, "is_banned": False, "is_admin": True})
        db.fetch_all = AsyncMock(return_value=[])
        state = StateStore(max_queue_size=20)
        rate = RateLimiter(state, self.config)
        job_storage = JobStorage(self.job_config)
        attach_app_services(app, jobs=MagicMock(), storage=job_storage,
                            state=state, db=db, config=self.config)
        ctx = HandlerContext(app, db, state, rate, self.config)
        commands.register(app, ctx)
        files.register(app, ctx)
        text_input.register(app, ctx)
        callbacks.register(app, ctx)
        file_editor.register(app, ctx)
        self.state = state
        self.ctx = ctx
        self.db = db
        self.job_storage = job_storage
        return app

    async def _get_handler(self, app, name):
        from pyrogram.handlers import MessageHandler
        await asyncio.sleep(0.15)
        for grp in app.dispatcher.groups.values():
            for h in grp:
                if isinstance(h, MessageHandler) and getattr(h.callback, "__name__", "") == name:
                    return h.callback
        raise AssertionError(f"handler {name} not found")

    def _install_download(self, app, content=b"line one\nline two\nline three\n"):
        async def fake_download(file_id, file_name="", progress=None):
            d = Path(file_name)
            target = d / "example.py"
            target.write_bytes(content)
            return str(target)
        app.download_media = fake_download
        return app

    def _assert_cleanup(self):
        children = list((self.tmp / "jobroot").iterdir()) if (self.tmp / "jobroot").exists() else []
        self.assertEqual(children, [], "temp job dirs must be cleaned up")

    # ── Reply targeting ──────────────────────────────────────────────
    def test_paste_requires_reply(self):
        app = self._build_app()

        async def scenario():
            paste = await self._get_handler(app, "paste_cmd")
            msg = _FakeMessage("/paste 1", reply_to=None)
            await paste(app, msg)
            msg.reply.assert_awaited_once()
            text = msg.reply.call_args.args[0]
            self.assertIn("ʀᴇᴘʟʏ ᴛᴏ ᴀ sᴜᴘᴘᴏʀᴛᴇᴅ ᴛᴇxᴛ ғɪʟᴇ", text)
            self.assertIn("ᴛʜɪs ᴄᴏᴍᴍᴀɴᴅ", text)
        asyncio.get_event_loop().run_until_complete(scenario())

    def test_replace_requires_reply(self):
        app = self._build_app()

        async def scenario():
            repl = await self._get_handler(app, "replace_cmd")
            msg = _FakeMessage("/replace 1 x")
            await repl(app, msg)
            text = msg.reply.call_args.args[0]
            self.assertIn("ʀᴇᴘʟʏ ᴛᴏ ᴀ sᴜᴘᴘᴏʀᴛᴇᴅ ᴛᴇxᴛ ғɪʟᴇ", text)
        asyncio.get_event_loop().run_until_complete(scenario())

    def test_detail_requires_reply(self):
        app = self._build_app()

        async def scenario():
            det = await self._get_handler(app, "detail_cmd")
            msg = _FakeMessage("/detail")
            await det(app, msg)
            text = msg.reply.call_args.args[0]
            self.assertIn("ʀᴇᴘʟʏ ᴛᴏ ᴀ sᴜᴘᴘᴏʀᴛᴇᴅ ᴛᴇxᴛ ғɪʟᴇ", text)
        asyncio.get_event_loop().run_until_complete(scenario())

    # ── Unsupported file rejection ───────────────────────────────────
    def test_paste_rejects_media(self):
        app = self._build_app()
        reply = SimpleNamespace(
            document=None,
            photo=SimpleNamespace(sizes=[SimpleNamespace(file_size=10)]),
            video=None, audio=None, animation=None, voice=None,
            sticker=None, video_note=None, contact=None, location=None,
            chat=SimpleNamespace(id=123), id=42,
        )

        async def scenario():
            paste = await self._get_handler(app, "paste_cmd")
            msg = _FakeMessage("/paste 1", reply_to=reply)
            await paste(app, msg)
            text = msg.reply.call_args.args[0]
            self.assertIn("ᴜɴsᴜᴘᴘᴏʀᴛᴇᴅ ғɪʟᴇ", text)
        asyncio.get_event_loop().run_until_complete(scenario())

    def test_paste_rejects_archive(self):
        app = self._build_app()

        async def scenario():
            paste = await self._get_handler(app, "paste_cmd")
            msg = _FakeMessage("/paste 1", reply_to=_fake_reply_doc("data.zip", mime="application/zip"))
            await paste(app, msg)
            text = msg.reply.call_args.args[0]
            self.assertIn("ᴜɴsᴜᴘᴘᴏʀᴛᴇᴅ ғɪʟᴇ", text)
        asyncio.get_event_loop().run_until_complete(scenario())

    def test_paste_rejects_unsupported_extension(self):
        app = self._build_app()

        async def scenario():
            paste = await self._get_handler(app, "paste_cmd")
            msg = _FakeMessage("/paste 1", reply_to=_fake_reply_doc("movie.mp4", mime="video/mp4"))
            await paste(app, msg)
            text = msg.reply.call_args.args[0]
            self.assertIn("ᴜɴsᴜᴘᴘᴏʀᴛᴇᴅ ғɪʟᴇ", text)
        asyncio.get_event_loop().run_until_complete(scenario())

    def test_paste_rejects_oversized(self):
        app = self._build_app()

        async def scenario():
            paste = await self._get_handler(app, "paste_cmd")
            big = self.config.max_file_size + 1
            msg = _FakeMessage("/paste 1", reply_to=_fake_reply_doc("big.txt", size=big, mime="text/plain"))
            await paste(app, msg)
            text = msg.reply.call_args.args[0]
            self.assertIn("ᴛᴏᴏ ʟᴀʀɢᴇ", text)
        asyncio.get_event_loop().run_until_complete(scenario())

    # ── Happy paths ──────────────────────────────────────────────────
    def test_paste_flow_exact_line(self):
        app = self._build_app()
        self._install_download(app)

        async def scenario():
            paste = await self._get_handler(app, "paste_cmd")
            msg = _FakeMessage("/paste 2", reply_to=_fake_reply_doc())
            await paste(app, msg)
            msg.reply.assert_awaited_once()
            text = msg.reply.call_args.args[0]
            self.assertIn("ᴘᴀsᴛᴇᴅ ʟɪɴᴇ 2", text)
            self.assertIn("<pre>line two</pre>", text)
        asyncio.get_event_loop().run_until_complete(scenario())
        self._assert_cleanup()

    def test_paste_flow_range(self):
        app = self._build_app()
        self._install_download(app)

        async def scenario():
            paste = await self._get_handler(app, "paste_cmd")
            msg = _FakeMessage("/paste 1-3", reply_to=_fake_reply_doc())
            await paste(app, msg)
            text = msg.reply.call_args.args[0]
            self.assertIn("ʟɪɴᴇs 1–3", text)
            self.assertIn("<pre>line one\nline two\nline three</pre>", text)
        asyncio.get_event_loop().run_until_complete(scenario())
        self._assert_cleanup()

    def test_paste_invalid_line_rejected(self):
        app = self._build_app()

        async def scenario():
            paste = await self._get_handler(app, "paste_cmd")
            msg = _FakeMessage("/paste 99-2", reply_to=_fake_reply_doc())
            await paste(app, msg)
            text = msg.reply.call_args.args[0]
            self.assertIn("ɪɴᴠᴀʟɪᴅ ʀᴀɴɢᴇ", text)
        asyncio.get_event_loop().run_until_complete(scenario())
        self._assert_cleanup()

    def _install_send_document(self, app, captured: dict):
        async def fake_send_document(chat_id=None, document=None, file_name=None,
                                     caption=None, progress=None, force_document=None):
            captured["bytes"] = Path(document).read_bytes()
            captured["file_name"] = file_name
            captured["caption"] = caption
            captured["path"] = document
            return SimpleNamespace(id=1)
        app.send_document = fake_send_document
        return captured

    def test_replace_flow_single_inline(self):
        app = self._build_app()
        self._install_download(app, content=b"Hello\nWorld\nTest\n")
        captured = self._install_send_document(app, {})

        async def scenario():
            repl = await self._get_handler(app, "replace_cmd")
            msg = _FakeMessage("/replace 2 New World", reply_to=_fake_reply_doc())
            await repl(app, msg)
            self.assertIn("out", Path(captured["path"]).parts)  # separate output dir
            self.assertEqual(captured["bytes"], b"Hello\nNew World\nTest\n")
            self.assertEqual(captured["file_name"], "example.py")
            self.assertIn("ʀᴇᴘʟᴀᴄᴇᴅ", captured["caption"])
        asyncio.get_event_loop().run_until_complete(scenario())
        self._assert_cleanup()

    def test_replace_flow_range_inline_body(self):
        app = self._build_app()
        self._install_download(app, content=b"l1\nl2\nl3\nl4\nl5\n")
        captured = self._install_send_document(app, {})

        async def scenario():
            repl = await self._get_handler(app, "replace_cmd")
            msg = _FakeMessage("/replace 2-4\nAAA\nBBB\nCCC", reply_to=_fake_reply_doc())
            await repl(app, msg)
            self.assertEqual(captured["bytes"], b"l1\nAAA\nBBB\nCCC\nl5\n")
        asyncio.get_event_loop().run_until_complete(scenario())
        self._assert_cleanup()

    def test_replace_count_mismatch_rejected(self):
        app = self._build_app()
        self._install_download(app)
        app.send_document = AsyncMock()

        async def scenario():
            repl = await self._get_handler(app, "replace_cmd")
            msg = _FakeMessage("/replace 2-4\nonly-one", reply_to=_fake_reply_doc())
            await repl(app, msg)
            text = msg.reply.call_args.args[0]
            self.assertIn("ᴍɪsᴍᴀᴛᴄʜ", text)
            app.send_document.assert_not_awaited()
        asyncio.get_event_loop().run_until_complete(scenario())
        self._assert_cleanup()

    def test_replace_out_of_bounds_rejected(self):
        app = self._build_app()
        self._install_download(app, content=b"a\nb\nc\n")

        async def scenario():
            repl = await self._get_handler(app, "replace_cmd")
            msg = _FakeMessage("/replace 5 x", reply_to=_fake_reply_doc())
            await repl(app, msg)
            text = msg.reply.call_args.args[0]
            self.assertIn("ʟɪɴᴇ ᴏᴜᴛ ᴏғ ʙᴏᴜɴᴅs", text)
        asyncio.get_event_loop().run_until_complete(scenario())
        self._assert_cleanup()

    def test_replace_followup_lines_flow(self):
        app = self._build_app()
        self._install_download(app, content=b"a\nb\nc\nd\n")
        captured = self._install_send_document(app, {})

        async def scenario():
            repl = await self._get_handler(app, "replace_cmd")
            msg = _FakeMessage("/replace 2-4", reply_to=_fake_reply_doc())
            await repl(app, msg)
            # Prompt asks for the replacement lines and stores state.
            prompt = msg.reply.call_args.args[0]
            self.assertIn("ʀᴇᴘʟᴀᴄᴇ", prompt)
            self.assertIn("3", prompt)
            state = await self.state.get_user_state(1)
            self.assertEqual(state["action"], "editor_replace")
            self.assertEqual(state["editor"]["selector"], "2-4")

            # Follow-up plain message supplies the lines.
            on_text = await self._get_handler(app, "on_text")
            follow = _FakeMessage("AAA\nBBB\nCCC")
            await on_text(app, follow)
            self.assertEqual(captured["bytes"], b"a\nAAA\nBBB\nCCC\n")
            self.assertIsNone(await self.state.get_user_state(1))
        asyncio.get_event_loop().run_until_complete(scenario())
        self._assert_cleanup()

    def test_detail_flow(self):
        app = self._build_app()
        self._install_download(app, content=b"# hi\nimport os\nprint('x')\n")

        async def scenario():
            det = await self._get_handler(app, "detail_cmd")
            msg = _FakeMessage("/detail", reply_to=_fake_reply_doc())
            await det(app, msg)
            text = msg.reply.call_args.args[0]
            self.assertIn("ᴅᴇᴛᴀɪʟs", text)
            self.assertIn("example.py", text)
            self.assertIn("ᴛᴏᴛᴀʟ ʟɪɴᴇs: 3", text)
            self.assertIn("UTF-8", text)
        asyncio.get_event_loop().run_until_complete(scenario())
        self._assert_cleanup()

    def test_detail_reports_branding(self):
        app = self._build_app()
        self._install_download(
            app,
            content=b"line1 @OldChannel\n@staticmethod\nsee https://t.me/OldChannel\n",
        )

        async def scenario():
            det = await self._get_handler(app, "detail_cmd")
            msg = _FakeMessage("/detail", reply_to=_fake_reply_doc())
            await det(app, msg)
            text = msg.reply.call_args.args[0]
            self.assertIn("ʙʀᴀɴᴅɪɴɢ ᴅᴇᴛᴇᴄᴛᴇᴅ", text)
            self.assertIn("ʟɪɴᴇ 1 → <code>@OldChannel</code>", text)
            self.assertIn("ʟɪɴᴇ 3 → <code>https://t.me/OldChannel</code>", text)
            self.assertNotIn("@staticmethod", text)
            self.assertIn("ᴛᴏᴛᴀʟ: 2 ᴍᴀᴛᴄʜᴇs", text)
        asyncio.get_event_loop().run_until_complete(scenario())
        self._assert_cleanup()

    def test_no_duplicate_command_handlers(self):
        app = self._build_app()

        async def scenario():
            from pyrogram.handlers import MessageHandler
            await asyncio.sleep(0.15)
            names = [getattr(h.callback, "__name__", "") for grp in app.dispatcher.groups.values() for h in grp
                     if isinstance(h, MessageHandler)]
            for cmd in ("paste_cmd", "replace_cmd", "detail_cmd"):
                self.assertEqual(names.count(cmd), 1, f"{cmd} must be registered exactly once")
        asyncio.get_event_loop().run_until_complete(scenario())


# ──────────────────────────────────────────────────────────────────────────
# UI: Commands menu + Help pagination mention the new commands
# ──────────────────────────────────────────────────────────────────────────
class EditorUITests(unittest.TestCase):
    def setUp(self):
        _ensure_event_loop()
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.config = _make_config(self.tmp)

    def tearDown(self):
        self._tmp.cleanup()

    def test_help_pages_mention_editor_commands(self):
        from bot import messages as M
        self.assertEqual(len(M.HELP_PAGES), 6)  # pagination unchanged
        all_text = "\n".join(M.HELP_PAGES)
        for cmd in ("/paste", "/replace", "/detail"):
            self.assertIn(cmd, all_text)

    def test_help_one_liner_mentions_editor(self):
        from bot import messages as M
        self.assertIn("/paste", M.HELP)
        self.assertIn("/detail", M.HELP)

    def test_commands_menu_lists_editor_commands(self):
        from bot.callbacks import callbacks as cbmod
        from bot.handlers.common import HandlerContext
        from database.database import Database
        from services.rate_limit import RateLimiter
        from services.state import StateStore

        db = MagicMock(spec=Database)
        state = StateStore(max_queue_size=5)
        rate = RateLimiter(state, self.config)
        ctx = HandlerContext(None, db, state, rate, self.config)  # type: ignore[arg-type]
        cb = SimpleNamespace(
            from_user=SimpleNamespace(id=1),
            edit_message_text=AsyncMock(),
            answer=AsyncMock(),
            message=SimpleNamespace(chat=SimpleNamespace(id=1)),
            data="commands",
        )

        async def scenario():
            await cbmod._show_commands(cb, ctx, 1)
            text = cb.edit_message_text.call_args.args[0]
            for cmd in ("/paste", "/replace", "/detail", "/start", "/history"):
                self.assertIn(cmd, text)
        asyncio.get_event_loop().run_until_complete(scenario())

    def test_existing_main_menu_buttons_unchanged(self):
        from bot.keyboards import keyboards as kb
        markup = kb.main_menu_keyboard(False)
        flat = [b.callback_data for row in markup.inline_keyboard for b in row]
        self.assertIn("help", flat)
        self.assertIn("commands", flat)
        self.assertIn("history", flat)
        self.assertIn("settings", flat)


if __name__ == "__main__":
    unittest.main(verbosity=2)
