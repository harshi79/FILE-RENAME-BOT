"""
File type / size validation.

Validation uses three signals and rejects on any of them:

1. Telegram media type (photo / video / audio / animation / voice / sticker)
2. Filename extension
3. MIME type where available

Archives and media are rejected explicitly; ambiguous files fall back to the
extension allow-list. This runs BEFORE any download so oversized or
unsupported files never consume bandwidth or disk.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Set, Tuple

from core import filename as fn

# Extensions that are ALWAYS rejected regardless of MIME type.
MEDIA_EXTENSIONS: Set[str] = {
    ".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".svg", ".ico", ".tiff", ".tif",
    ".mp4", ".mkv", ".webm", ".mov", ".avi", ".flv", ".wmv", ".m4v", ".mpg", ".mpeg", ".3gp",
    ".mp3", ".wav", ".flac", ".m4a", ".ogg", ".opus", ".aac", ".wma", ".amr",
    ".ai", ".psd",
}

ARCHIVE_EXTENSIONS: Set[str] = {
    ".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz", ".tgz", ".tbz2",
    ".zst", ".lz", ".lzma", ".cab", ".iso", ".dmg",
}

# Multi-suffix compound archives (e.g. .tar.gz, .tar.bz2).
COMPOUND_ARCHIVE_SUFFIXES: Tuple[str, ...] = (
    ".tar.gz", ".tar.bz2", ".tar.xz", ".tar.zst", ".tar.lz",
)

# Strict allow-list of ordinary text / code / config / document-like files.
ALLOWED_EXTENSIONS: Set[str] = {
    # plain text
    ".txt", ".text", ".log", ".md", ".markdown", ".rst", ".rtf",
    # code
    ".py", ".pyw", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx",
    ".java", ".c", ".h", ".cpp", ".cc", ".cxx", ".hpp", ".cs",
    ".go", ".rs", ".rb", ".php", ".pl", ".pm", ".kt", ".kts", ".swift",
    ".scala", ".sh", ".bash", ".zsh", ".fish", ".ps1", ".bat", ".cmd",
    ".lua", ".r", ".dart", ".groovy", ".gradle", ".sql", ".vue", ".svelte",
    ".yml", ".yaml", ".toml", ".ini", ".cfg", ".conf", ".properties",
    ".env", ".gitignore", ".dockerignore",
    # data / structured
    ".json", ".jsonl", ".xml", ".csv", ".tsv", ".html", ".htm", ".css",
    ".scss", ".sass", ".less", ".svg.txt",
    # docs
    ".pdf", ".epub", ".tex", ".bib",
}

# MIME prefixes that always indicate media.
MEDIA_MIME_PREFIXES = (
    "image/", "video/", "audio/",
)
ARCHIVE_MIMES = {
    "application/zip", "application/x-rar-compressed", "application/x-7z-compressed",
    "application/x-tar", "application/gzip", "application/x-gzip",
    "application/x-bzip2", "application/x-xz", "application/zstd",
    "application/x-iso9660-image",
}
# MIMEs that are considered document-like and safe even without an allow-listed
# extension (only used when extension is absent).
TEXTUAL_MIMES = {
    "text/plain", "text/csv", "text/markdown", "text/html", "text/xml",
    "application/json", "application/xml", "application/x-yaml", "application/yaml",
    "application/javascript",
}


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    reason: str = ""  # "media" | "archive" | "unsupported" | "too_large" | ""
    filename: str = ""
    extension: str = ""
    size: int = 0


def classify_telegram_media(message) -> str:
    """
    Inspect a Pyrogram message and return a coarse category:
    'document' | 'photo' | 'video' | 'audio' | 'animation' | 'voice' |
    'sticker' | 'video_note' | 'contact' | 'location' | 'none'.
    """
    for attr in (
        "document", "photo", "video", "audio", "animation",
        "voice", "sticker", "video_note", "contact", "location",
    ):
        if getattr(message, attr, None) is not None:
            return attr
    return "none"


def _is_compound_archive(name_lower: str) -> bool:
    return any(name_lower.endswith(suffix) for suffix in COMPOUND_ARCHIVE_SUFFIXES)


def validate_file(
    *,
    filename: Optional[str],
    size: int,
    mime_type: Optional[str],
    telegram_media_type: str,
    max_size: int,
) -> ValidationResult:
    """
    Decide whether a file may enter the rename pipeline.

    Performs ALL checks before returning so callers get a definitive reason.
    """
    name = (filename or "").strip()
    ext = fn.get_extension(name)
    name_lower = name.lower()
    mime = (mime_type or "").lower()

    # 1) Telegram-level media types are rejected outright.
    if telegram_media_type in {
        "photo", "video", "audio", "animation", "voice",
        "sticker", "video_note", "contact", "location",
    }:
        return ValidationResult(False, "media", name, ext, size)

    if telegram_media_type != "document":
        return ValidationResult(False, "unsupported", name, ext, size)

    # 2) Size check – happens before any download.
    if size is None or size <= 0:
        return ValidationResult(False, "unsupported", name, ext, size or 0)
    if size > max_size:
        return ValidationResult(False, "too_large", name, ext, size)

    # 3) Reject archives by compound suffix, extension and MIME.
    if _is_compound_archive(name_lower) or ext in ARCHIVE_EXTENSIONS or mime in ARCHIVE_MIMES:
        return ValidationResult(False, "archive", name, ext, size)

    # 4) Reject media by extension or MIME.
    if ext in MEDIA_EXTENSIONS or any(mime.startswith(p) for p in MEDIA_MIME_PREFIXES):
        return ValidationResult(False, "media", name, ext, size)

    # 5) The file must have a usable name.
    if not name:
        return ValidationResult(False, "unsupported", name, ext, size)

    # 6) Allow-list by extension. A missing extension is accepted only if the
    #    MIME type is clearly textual.
    if ext:
        if ext not in ALLOWED_EXTENSIONS:
            return ValidationResult(False, "unsupported", name, ext, size)
    else:
        if mime not in TEXTUAL_MIMES:
            return ValidationResult(False, "unsupported", name, ext, size)

    return ValidationResult(True, "", name, ext, size)


def validate_extension_change(ext_with_dot: str) -> bool:
    """Extension change must never land on a media/archive extension."""
    e = ext_with_dot.lower()
    if e in MEDIA_EXTENSIONS or e in ARCHIVE_EXTENSIONS:
        return False
    # Allow any textual / code / unknown plain extension token.
    return e in ALLOWED_EXTENSIONS
