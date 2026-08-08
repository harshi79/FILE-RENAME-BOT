"""
Pure filename helpers.

No I/O, no Telegram imports here – this is the single source of truth for
splitting / building names and sanitising user input so that invalid or
dangerous characters never reach the filesystem.
"""
from __future__ import annotations

import os
import re
import unicodedata
from typing import Tuple

# Characters not allowed on common filesystems. We strip them defensively.
_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
# Control-style characters and reserved Windows device names.
_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
_MAX_STEM_LEN = 180
_MAX_NAME_LEN = 220


def split_extension(filename: str) -> Tuple[str, str]:
    """
    Split a filename into (stem, extension).

    Uses os.path.splitext so a file like ``my.file.py`` returns
    ``("my.file", ".py")``. The extension is always lower-cased for matching
    but kept as-is for output via the caller.
    """
    name = (filename or "").strip()
    if not name:
        return "", ""
    stem, ext = os.path.splitext(name)
    return stem, ext


def get_extension(filename: str) -> str:
    """Return the lower-cased extension including the dot, e.g. '.txt'."""
    _, ext = split_extension(filename)
    return ext.lower()


def normalise_extension(raw: str) -> str:
    """
    Normalise a user-supplied extension.

    Accepts ``md`` and ``.md``; returns ``.md``. Strips whitespace and a
    leading dot, rejects anything that is not a simple alphanumeric token.
    """
    ext = (raw or "").strip().lower()
    if not ext:
        raise ValueError("extension cannot be empty")
    if ext.startswith("."):
        ext = ext[1:]
    # Reject anything that isn't a plain extension token.
    if not re.fullmatch(r"[a-z0-9]{1,16}", ext):
        raise ValueError("extension must contain only letters and digits (1-16 chars)")
    return "." + ext


def sanitise_filename(name: str, *, default_stem: str = "file") -> str:
    """
    Return a safe cross-platform filename.

    Removes path separators, control characters, reserved names and collapses
    whitespace. Does NOT add or remove an extension – callers pass the full
    desired name.
    """
    if name is None:
        name = ""
    # Normalise unicode to a compact form.
    name = unicodedata.normalize("NFKC", str(name))
    # Strip any directory components a user might try to inject.
    name = name.replace("\\", "/").split("/")[-1]
    name = _INVALID_CHARS.sub("", name)
    # Collapse internal whitespace and trim dots/spaces at the edges.
    name = re.sub(r"\s+", " ", name).strip(" .")
    if not name:
        name = default_stem

    stem, ext = split_extension(name)
    if len(ext) > 20:  # absurd extension – treat as part of stem
        stem = name
        ext = ""
    if stem.upper() in _RESERVED_NAMES:
        stem = f"_{stem}"
    if len(stem) > _MAX_STEM_LEN:
        stem = stem[:_MAX_STEM_LEN].rstrip()
    result = f"{stem}{ext}"
    if len(result) > _MAX_NAME_LEN:
        result = result[:_MAX_NAME_LEN].rstrip()
    return result or default_stem


def apply_stem(original_name: str, new_stem: str) -> str:
    """
    Build a new name preserving the ORIGINAL extension.

    The user-supplied name's extension is ignored. Example:
        original ``test.txt``, user input ``hello.py`` -> ``hello.txt``
    """
    _, original_ext = split_extension(original_name)
    user_stem, _ = split_extension(new_stem)
    user_stem = user_stem.strip()
    if not user_stem:
        raise ValueError("filename cannot be empty")
    return sanitise_filename(user_stem + original_ext, default_stem="renamed")


def apply_extension(original_name: str, new_ext: str) -> str:
    """
    Build a new name replacing the extension with ``new_ext`` (already
    normalised, e.g. '.md').
    """
    stem, _ = split_extension(original_name)
    if not stem.strip():
        stem = "file"
    return sanitise_filename(stem + new_ext, default_stem="renamed")


# ──────────────────────────────────────────────────────────────────────
# Advanced transforms (preserve extension unless caller changes it).
# ──────────────────────────────────────────────────────────────────────
def transform_find_replace(stem: str, find: str, replace: str) -> str:
    if not find:
        raise ValueError("search text cannot be empty")
    return stem.replace(find, replace)


def transform_prefix(stem: str, prefix: str) -> str:
    return f"{prefix}{stem}"


def transform_suffix(stem: str, suffix: str) -> str:
    return f"{stem}{suffix}"


def transform_remove_prefix(stem: str, prefix: str) -> str:
    if prefix and stem.startswith(prefix):
        return stem[len(prefix):]
    return stem


def transform_remove_suffix(stem: str, suffix: str) -> str:
    if suffix and stem.endswith(suffix):
        return stem[: -len(suffix)]
    return stem


def transform_whitespace(stem: str) -> str:
    return re.sub(r"\s+", " ", stem).strip()


def transform_case(stem: str, mode: str) -> str:
    mode = (mode or "none").lower()
    if mode == "lower":
        return stem.lower()
    if mode == "upper":
        return stem.upper()
    if mode == "title":
        return stem.title()
    return stem  # "none" / unknown -> unchanged


def apply_settings_to_stem(stem: str, settings: dict) -> str:
    """
    Apply a user's persisted preferences (whitespace cleanup + case mode) to a
    stem. Called for operations that aren't themselves case/whitespace actions.
    """
    if not settings:
        return stem
    if str(settings.get("ws_mode", "off")).lower() == "on":
        stem = transform_whitespace(stem)
    case_mode = str(settings.get("case_mode", "none")).lower()
    if case_mode in {"lower", "upper", "title"}:
        stem = transform_case(stem, case_mode)
    return stem


def transform_number(stem: str, index: int, start: int = 1, pad: int = 2) -> str:
    """
    Apply sequential numbering. The number is appended with a space.
    ``index`` is 1-based position in the batch.
    """
    number = str(start + index - 1).zfill(max(1, pad))
    return f"{stem} {number}" if stem else number
