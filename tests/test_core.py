"""
Unit tests for core filename, validation and rename logic.

Run with:  python -m pytest tests/ -q
(or simply: python tests/test_core.py  as a fallback)
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import filename as fn
from core import rename as rn
from core.validation import validate_file, validate_extension_change


class FilenameTests(unittest.TestCase):
    def test_split_extension(self):
        self.assertEqual(fn.split_extension("hello.txt"), ("hello", ".txt"))
        self.assertEqual(fn.split_extension("my.file.py"), ("my.file", ".py"))
        self.assertEqual(fn.split_extension("noext"), ("noext", ""))

    def test_normalise_extension(self):
        self.assertEqual(fn.normalise_extension("md"), ".md")
        self.assertEqual(fn.normalise_extension(".md"), ".md")
        self.assertEqual(fn.normalise_extension("  YML "), ".yml")
        with self.assertRaises(ValueError):
            fn.normalise_extension("")
        with self.assertRaises(ValueError):
            fn.normalise_extension("mp4/../x")  # invalid token
        # "mp4" is a valid token syntactically; the media restriction is
        # enforced separately by validate_extension_change().
        self.assertEqual(fn.normalise_extension("mp4"), ".mp4")

    def test_sanitise_removes_traversal(self):
        self.assertNotIn("/", fn.sanitise_filename("../../etc/passwd.txt"))
        self.assertNotIn("\\", fn.sanitise_filename("..\\windows\\system32.txt"))
        self.assertNotIn("\x00", fn.sanitise_filename("bad\x00name.txt"))
        self.assertEqual(fn.sanitise_filename("   "), "file")

    def test_sanitise_collapses_whitespace(self):
        self.assertEqual(fn.sanitise_filename("hello   world.txt"), "hello world.txt")

    def test_apply_stem_preserves_original_ext(self):
        # Spec: test.py + hello.txt -> hello.py
        self.assertEqual(fn.apply_stem("test.py", "hello.txt"), "hello.py")
        self.assertEqual(fn.apply_stem("test.txt", "hello"), "hello.txt")
        self.assertEqual(fn.apply_stem("test.txt", "hello.py"), "hello.txt")

    def test_apply_extension(self):
        self.assertEqual(fn.apply_extension("notes.txt", ".md"), "notes.md")

    def test_number(self):
        self.assertEqual(fn.transform_number("Episode", 1, 1, 2), "Episode 01")
        self.assertEqual(fn.transform_number("Episode", 2, 1, 2), "Episode 02")


class RenamePlanTests(unittest.TestCase):
    def test_rename_ignores_user_extension(self):
        plan = rn.plan_rename("test.py", "hello.txt")
        self.assertEqual(plan.new_name, "hello.py")

    def test_extension_change(self):
        plan = rn.plan_extension("test.txt", "md")
        self.assertEqual(plan.new_name, "test.md")

    def test_find_replace(self):
        plan = rn.plan_find_replace("episode 1.txt", "episode", "Episode")
        self.assertEqual(plan.new_name, "Episode 1.txt")

    def test_prefix_suffix(self):
        self.assertEqual(rn.plan_prefix("a.txt", "pre_").new_name, "pre_a.txt")
        self.assertEqual(rn.plan_suffix("a.txt", "_suf").new_name, "a_suf.txt")

    def test_number_batch(self):
        names = ["episode 1.txt", "episode 2.txt", "episode 3.txt"]
        plans = rn.number_batch(names, "Episode", start=1, pad=2)
        self.assertEqual(plans[0].new_name, "Episode 01.txt")
        self.assertEqual(plans[1].new_name, "Episode 02.txt")
        self.assertEqual(plans[2].new_name, "Episode 03.txt")

    def test_find_replace_batch(self):
        names = ["a.txt", "b.txt"]
        plans = rn.find_replace_batch(names, "a", "z")
        self.assertEqual(plans[0].new_name, "z.txt")
        self.assertEqual(plans[1].new_name, "b.txt")

    def test_case(self):
        self.assertEqual(rn.plan_case("hello world.txt", "title").new_name, "Hello World.txt")
        self.assertEqual(rn.plan_case("HELLO.txt", "lower").new_name, "hello.txt")

    def test_whitespace(self):
        self.assertEqual(rn.plan_whitespace("a   b.txt").new_name, "a b.txt")


class ValidationTests(unittest.TestCase):
    MAX = 25 * 1024 * 1024

    def _ok(self, name, size=100, mime="", mtype="document"):
        r = validate_file(filename=name, size=size, mime_type=mime,
                          telegram_media_type=mtype, max_size=self.MAX)
        return r

    def test_allowed_text_files(self):
        for name in ["hello.txt", "hello world.txt", "my.file.py",
                     "test_01.json", "données.csv", "doc (1).md",
                     "a-b_c.yaml", "conf.ini"]:
            r = self._ok(name)
            self.assertTrue(r.ok, f"{name} should be allowed: {r.reason}")

    def test_unicode_filename(self):
        r = self._ok("файл.txt")
        self.assertTrue(r.ok)
        r = self._ok("日本語.md")
        self.assertTrue(r.ok)

    def test_reject_media_extensions(self):
        for name in ["pic.jpg", "pic.png", "clip.mp4", "movie.mkv",
                     "song.mp3", "a.webp", "b.gif", "c.jpeg"]:
            r = self._ok(name)
            self.assertFalse(r.ok)
            self.assertEqual(r.reason, "media")

    def test_reject_archives(self):
        for name in ["a.zip", "b.rar", "c.7z", "d.tar", "e.tar.gz",
                     "f.tar.bz2", "g.gz", "h.bz2"]:
            r = self._ok(name)
            self.assertFalse(r.ok)
            self.assertEqual(r.reason, "archive", f"{name} -> {r.reason}")

    def test_reject_telegram_media_type(self):
        r = validate_file(filename="x.txt", size=10, mime_type="",
                          telegram_media_type="photo", max_size=self.MAX)
        self.assertFalse(r.ok)
        self.assertEqual(r.reason, "media")

    def test_reject_oversize_before_download(self):
        r = validate_file(filename="big.txt", size=self.MAX + 1, mime_type="",
                          telegram_media_type="document", max_size=self.MAX)
        self.assertFalse(r.ok)
        self.assertEqual(r.reason, "too_large")

    def test_extension_change_blocks_media(self):
        self.assertFalse(validate_extension_change(".mp4"))
        self.assertFalse(validate_extension_change(".zip"))
        self.assertTrue(validate_extension_change(".md"))
        self.assertTrue(validate_extension_change(".txt"))


class StoragePathTests(unittest.TestCase):
    def test_path_traversal_blocked(self):
        import tempfile
        from services.storage import JobStorage, StorageError
        import os, uuid
        os.environ.setdefault("TEMP_DIR", tempfile.mkdtemp())
        # Minimal config stub for storage.
        class _Cfg:
            temp_dir = __import__("pathlib").Path(tempfile.mkdtemp())
            max_file_size = 25 * 1024 * 1024
        s = JobStorage(_Cfg())
        with self.assertRaises(StorageError):
            s.job_dir("../etc")
        with self.assertRaises(StorageError):
            s.job_dir("not-a-uuid")
        jid = str(uuid.uuid4())
        d = s.create_job_dir(jid)
        self.assertTrue(d.is_dir())
        s.cleanup_job(jid)
        self.assertFalse(d.exists())


class FilenameSettingsTests(unittest.TestCase):
    def test_apply_settings(self):
        from core.filename import apply_settings_to_stem
        self.assertEqual(
            apply_settings_to_stem("hello   world", {"ws_mode": "on", "case_mode": "title"}),
            "Hello World",
        )
        self.assertEqual(apply_settings_to_stem("HELLO", {"case_mode": "lower"}), "hello")
        # none leaves unchanged
        self.assertEqual(apply_settings_to_stem("HeLLo", {}), "HeLLo")


if __name__ == "__main__":
    unittest.main(verbosity=2)
