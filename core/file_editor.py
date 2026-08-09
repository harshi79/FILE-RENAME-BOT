"""
Reply-based text file editor core (pure logic, no Telegram I/O).

Implements the bounded line operations behind /paste, /replace and /detail:

* /paste  — return EXACT lines (single / inclusive 1-based range / full).
* /replace — replace EXACT lines with user-supplied lines, writing the result
  to a SEPARATE output file (the original is never modified).
* /detail — file analysis: name, size, line count, detected encoding plus
  conservative Telegram / channel branding detection.

Memory is bounded: files are processed line-by-line (streamed), the whole
file is never loaded into RAM, and paste output is capped so Telegram
message limits are respected instead of crashing. For /replace, the range is
fully validated (syntax, bounds, replacement count) BEFORE any output is
written, and untouched lines are copied byte-for-byte.

Security: this module only reads/writes text; uploaded content is never
interpreted as code, and output filenames are sanitised so a malicious
document name can never escape the temp directory.
"""
from __future__ import annotations

import codecs
import html
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Sequence

# ──────────────────────────────────────────────────────────────────────────
# Output budgets (Telegram hard limit is 4096 chars per message).
# ──────────────────────────────────────────────────────────────────────────
MAX_MSG_CHARS = 3500          # safe per-message budget (header + <pre> overhead)
MAX_MSG_COUNT = 5             # at most this many messages per /paste
MAX_PASTE_CHARS = MAX_MSG_CHARS * MAX_MSG_COUNT
MAX_SELECTOR_LINES = 2000     # /replace range width cap (replacement lines must fit in chat)


class EditorError(Exception):
    """
    User-facing editor error.

    The message is already formatted in the bot's small-caps HTML style and
    is safe to reply with directly. An empty string means "already replied"
    (e.g. a rate-limit response) and must not be sent again.
    """


# ──────────────────────────────────────────────────────────────────────────
# Line selector parsing ("1", "1-10", "full") – 1-based, inclusive.
# ──────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class LineSelector:
    kind: str        # "single" | "range" | "full"
    start: int = 0   # 1-based
    end: int = 0     # 1-based inclusive

    @property
    def width(self) -> int:
        if self.kind == "single":
            return 1
        if self.kind == "range":
            return self.end - self.start + 1
        return -1


def parse_line_selector(arg: str) -> LineSelector:
    """Parse ``1``, ``1-10`` or ``full`` into a validated selector."""
    raw = (arg or "").strip()
    if not raw:
        raise EditorError(
            "⚠️ <b>ɪɴᴠᴀʟɪᴅ ʟɪɴᴇ.</b> ᴜsᴇ: <code>1</code>, <code>1-10</code> ᴏʀ <code>full</code>."
        )
    if raw.lower() == "full":
        return LineSelector("full")
    if "-" in raw:
        start_raw, _, end_raw = raw.partition("-")
        if not start_raw.isdigit() or not end_raw.isdigit():
            raise EditorError(
                "⚠️ <b>ɪɴᴠᴀʟɪᴅ ʀᴀɴɢᴇ.</b> ᴜsᴇ: <code>1-10</code>."
            )
        start, end = int(start_raw), int(end_raw)
        if start < 1:
            raise EditorError("⚠️ <b>ɪɴᴠᴀʟɪᴅ ʀᴀɴɢᴇ.</b> ʟɪɴᴇ ɴᴜᴍʙᴇʀs ᴀʀᴇ 1-ʙᴀsᴇᴅ.")
        if end < start:
            raise EditorError(
                f"⚠️ <b>ɪɴᴠᴀʟɪᴅ ʀᴀɴɢᴇ.</b> <code>{start}-{end}</code>: "
                "ᴇɴᴅ ᴍᴜsᴛ ʙᴇ ≥ sᴛᴀʀᴛ."
            )
        if end - start + 1 > MAX_SELECTOR_LINES:
            raise EditorError(
                f"⚠️ <b>ʀᴀɴɢᴇ ᴛᴏᴏ ʟᴀʀɢᴇ.</b> ᴍᴀx {MAX_SELECTOR_LINES} ʟɪɴᴇs ᴘᴇʀ ᴏᴘᴇʀᴀᴛɪᴏɴ."
            )
        return LineSelector("range", start, end)
    if raw.isdigit():
        n = int(raw)
        if n < 1:
            raise EditorError("⚠️ <b>ɪɴᴠᴀʟɪᴅ ʟɪɴᴇ.</b> ʟɪɴᴇ ɴᴜᴍʙᴇʀs ᴀʀᴇ 1-ʙᴀsᴇᴅ.")
        return LineSelector("single", n, n)
    raise EditorError(
        "⚠️ <b>ɪɴᴠᴀʟɪᴅ ʟɪɴᴇ.</b> ᴜsᴇ: <code>1</code>, <code>1-10</code> ᴏʀ <code>full</code>."
    )


def selector_label(selector: LineSelector) -> str:
    """Short small-caps label: ``ʟɪɴᴇ 1`` / ``ʟɪɴᴇs 2–4`` / ``ғᴜʟʟ ғɪʟᴇ``."""
    if selector.kind == "single":
        return f"ʟɪɴᴇ {selector.start}"
    if selector.kind == "range":
        return f"ʟɪɴᴇs {selector.start}–{selector.end}"
    return "ғᴜʟʟ ғɪʟᴇ"


# ──────────────────────────────────────────────────────────────────────────
# /replace raw command parsing ("/replace 2 New Line", "/replace 2-4" + body).
# ──────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class ReplaceCommand:
    selector: LineSelector
    inline_lines: List[str]   # replacement lines already present in the message
    needs_followup: bool      # True when the replacement lines must be sent next


def split_message_lines(text: str) -> List[str]:
    """
    Split a message body into replacement lines.

    Splits ONLY on ``\\n`` so carriage returns, spaces and every other
    character are preserved exactly. A trailing newline does not create a
    phantom empty line (``"a\\n"`` → ``["a"]``); an empty (or newline-only)
    body counts as ONE empty replacement line so empty lines can be written
    where technically possible.
    """
    if text is None:
        return []
    if text == "" or text == "\n":
        return [""]
    parts = text.split("\n")
    if parts and parts[-1] == "":
        parts.pop()
    return parts


# Matches the /replace command line: optional @bot suffix, then the selector
# token, then EVERYTHING that follows verbatim (one separator char is consumed).
_CMD_LINE_RE = re.compile(r"^/(replace)(?:@[A-Za-z0-9_]+)?\s+(\S+)(.*)$", re.IGNORECASE)


def parse_replace_command(text: str) -> ReplaceCommand:
    """
    Parse a raw /replace message.

    Rules (documented, safe):
    * ``/replace 2 New World``  → line 2 becomes exactly ``New World``.
      Exactly one whitespace separator after the line number is consumed;
      everything after it (spaces included) is the complete new line.
    * ``/replace 2-4`` + body lines (same message or follow-up) → the body
      lines, one per message line, replace the range in order.
    * Extra text on the command line after a RANGE selector is an error
      (ambiguous) and nothing is modified.
    """
    text = text or ""
    first, _, body = text.partition("\n")
    match = _CMD_LINE_RE.match(first.strip())
    if not match:
        raise EditorError(
            "⚠️ <b>ɪɴᴠᴀʟɪᴅ ᴄᴏᴍᴍᴀɴᴅ.</b> ᴜsᴇ: <code>/replace 1 New Line</code> "
            "ᴏʀ <code>/replace 2-4</code> + ᴛʜᴇ ʀᴇᴘʟᴀᴄᴇᴍᴇɴᴛ ʟɪɴᴇs."
        )
    selector = parse_line_selector(match.group(2))
    trailing = match.group(3)
    if trailing.startswith((" ", "\t")):
        trailing = trailing[1:]
    if body:
        return ReplaceCommand(selector, split_message_lines(body), False)
    if selector.kind == "single":
        if trailing:
            return ReplaceCommand(selector, [trailing], False)
        return ReplaceCommand(selector, [], True)
    # Range selector without body lines.
    if trailing:
        raise EditorError(
            "⚠️ <b>ɪɴᴠᴀʟɪᴅ ᴄᴏᴍᴍᴀɴᴅ.</b> ᴘᴜᴛ ᴛʜᴇ ʀᴇᴘʟᴀᴄᴇᴍᴇɴᴛ ʟɪɴᴇs ᴏɴ ᴛʜᴇ "
            "ʟɪɴᴇs ʀɪɢʜᴛ ᴀғᴛᴇʀ ᴛʜᴇ ʀᴀɴɢᴇ (ᴏɴᴇ ᴘᴇʀ ʟɪɴᴇ)."
        )
    return ReplaceCommand(selector, [], True)


def count_mismatch_text(expected: int, got: int) -> str:
    return (
        "⚠️ <b>ʀᴇᴘʟᴀᴄᴇᴍᴇɴᴛ ᴄᴏᴜɴᴛ ᴍɪsᴍᴀᴛᴄʜ.</b> "
        f"ᴛʜᴇ ʀᴀɴɢᴇ ʀᴇǫᴜɪʀᴇs {expected} ʟɪɴᴇ(s), ʙᴜᴛ ʏᴏᴜ ᴘʀᴏᴠɪᴅᴇᴅ {got}."
    )


# ──────────────────────────────────────────────────────────────────────────
# Encoding detection (best-effort, no heavy dependencies).
# ──────────────────────────────────────────────────────────────────────────
def detect_encoding(path: Path) -> str:
    """Return a display label for the likely text encoding of ``path``."""
    with open(path, "rb") as fh:
        head = fh.read(4)
    if head.startswith(codecs.BOM_UTF8):
        return "UTF-8 (BOM)"
    if head.startswith(codecs.BOM_UTF16_LE):
        return "UTF-16-LE"
    if head.startswith(codecs.BOM_UTF16_BE):
        return "UTF-16-BE"

    def _decodes(codec: str) -> bool:
        try:
            with open(path, "rb") as fh:
                for raw in fh:
                    raw.decode(codec)
        except UnicodeDecodeError:
            return False
        return True

    if _decodes("utf-8"):
        return "UTF-8"
    if _decodes("cp1252"):
        return "CP1252"
    return "latin-1"


def _strip_terminator(raw: bytes) -> bytes:
    """Remove exactly one line terminator (``\\n``, ``\\r\\n`` or ``\\r``)."""
    if raw.endswith(b"\r\n"):
        return raw[:-2]
    if raw.endswith(b"\n") or raw.endswith(b"\r"):
        return raw[:-1]
    return raw


def _terminator(raw: bytes) -> bytes:
    """Return the line terminator of ``raw`` (may be empty for the last line)."""
    if raw.endswith(b"\r\n"):
        return b"\r\n"
    if raw.endswith(b"\n") or raw.endswith(b"\r"):
        return raw[-1:]
    return b""


def _strip_terminator_text(line: str) -> str:
    if line.endswith("\r\n"):
        return line[:-2]
    if line.endswith("\n") or line.endswith("\r"):
        return line[:-1]
    return line


def _iter_decoded_lines(path: Path, encoding: str) -> Iterable[str]:
    """
    Stream decoded lines (terminators removed). Bounded memory: one line at a
    time, never the whole file.
    """
    if encoding in ("UTF-16-LE", "UTF-16-BE"):
        # BOM was detected, so "utf-16" consumes it; newline="" preserves \r\n.
        with open(path, "r", encoding="utf-16", newline="") as fh:
            for line in fh:
                yield _strip_terminator_text(line)
        return
    if encoding == "UTF-8 (BOM)":
        codec = "utf-8-sig"
    elif encoding == "UTF-8":
        codec = "utf-8"
    else:
        codec = encoding
    with open(path, "rb") as fh:
        for raw in fh:
            yield _strip_terminator(raw).decode(codec, errors="replace")


def count_lines(path: Path) -> int:
    """Total line count (``readlines`` semantics: no phantom line for a
    trailing newline; an empty file has 0 lines)."""
    total = 0
    with open(path, "rb") as fh:
        for _ in fh:
            total += 1
    return total


# ──────────────────────────────────────────────────────────────────────────
# /paste
# ──────────────────────────────────────────────────────────────────────────
@dataclass
class PasteResult:
    lines: List[str]       # exact requested line contents, in original order
    total_lines: int       # file line count (accurate unless truncated early)
    truncated: bool        # output exceeds the Telegram budget


def paste_lines(path: Path, selector: LineSelector) -> PasteResult:
    """
    Return the exact requested lines (1-based, inclusive, original order).

    Reads the file line-by-line and stops as soon as the output budget is
    exhausted (truncated=True) — the whole file is never loaded into RAM.
    """
    encoding = detect_encoding(path)
    selected: List[str] = []
    total = 0
    used = 0
    truncated = False
    for line in _iter_decoded_lines(path, encoding):
        total += 1
        if selector.kind == "full" or selector.start <= total <= selector.end:
            selected.append(line)
            used += len(line) + 1
            if used > MAX_PASTE_CHARS:
                truncated = True
                break
    if selector.kind != "full" and total < selector.end:
        raise EditorError(
            f"⚠️ <b>ʟɪɴᴇ ᴏᴜᴛ ᴏғ ʙᴏᴜɴᴅs.</b> ᴛʜᴇ ғɪʟᴇ ʜᴀs {total} ʟɪɴᴇ(s), "
            f"ʙᴜᴛ ʟɪɴᴇ {selector.end} ᴡᴀs ʀᴇǫᴜᴇsᴛᴇᴅ."
        )
    return PasteResult(selected, total, truncated)


def paste_header(selector: LineSelector) -> str:
    """Small-caps header for a /paste reply."""
    if selector.kind == "single":
        return f"📄 ᴘᴀsᴛᴇᴅ ʟɪɴᴇ {selector.start}"
    if selector.kind == "range":
        return f"📋 ʟɪɴᴇs {selector.start}–{selector.end}"
    return "📄 ғᴜʟʟ ᴄᴏɴᴛᴇɴᴛ"


def render_paste(header: str, lines: Sequence[str], truncated: bool) -> List[str]:
    """
    Render paste output into ≤ MAX_MSG_COUNT messages, each ≤ MAX_MSG_CHARS.

    Content is HTML-escaped and placed inside <pre> so it renders exactly.
    If the output cannot fit, it is split across messages in order and a final
    truncation note is appended — Telegram limits are never exceeded.
    """
    body_budget = MAX_MSG_CHARS - 64  # room for header + <pre></pre>
    chunks: List[List[str]] = [[]]
    size = 0
    for line in lines:
        line_len = len(line)
        if line_len > body_budget:
            line = line[:body_budget] + "…"
            line_len = len(line)
        if chunks[-1] and size + line_len + 1 > body_budget:
            chunks.append([])
            size = 0
        chunks[-1].append(line)
        size += line_len + 1
        if len(chunks) >= MAX_MSG_COUNT:
            truncated = True
            break
    msgs: List[str] = []
    for i, chunk in enumerate(chunks):
        prefix = header if i == 0 else "📄 ᴘᴀsᴛᴇᴅ (ᴄᴏɴᴛɪɴᴜᴇᴅ)"
        msgs.append(f"{prefix}\n<pre>{html.escape(chr(10).join(chunk))}</pre>")
    if truncated:
        # The note rides on the last message so the message count stays
        # bounded at MAX_MSG_COUNT.
        msgs[-1] = msgs[-1] + "\n\n" + (
            "⚠️ <b>ᴏᴜᴛᴘᴜᴛ ᴛʀᴜɴᴄᴀᴛᴇᴅ.</b> ᴛʜᴇ ғɪʟᴇ ɪs ᴛᴏᴏ ʟᴀʀɢᴇ ғᴏʀ ᴛᴇʟᴇɢʀᴀᴍ — "
            "sʜᴏᴡɪɴɢ ᴛʜᴇ ғɪʀsᴛ ᴘᴀʀᴛ ᴏɴʟʏ. ᴜsᴇ ᴀ ɴᴀʀʀᴏᴡᴇʀ ʀᴀɴɢᴇ."
        )
    return msgs


# ──────────────────────────────────────────────────────────────────────────
# /replace
# ──────────────────────────────────────────────────────────────────────────
@dataclass
class ReplaceResult:
    total_lines: int
    replaced: int
    encoding: str


def replace_lines(
    path: Path,
    selector: LineSelector,
    replacements: Sequence[str],
    out_path: Path,
) -> ReplaceResult:
    """
    Write a NEW file at ``out_path`` with the selected lines replaced.

    Never touches ``path``. The requested range is validated (count and
    bounds) BEFORE any output is written. Untouched lines are copied
    byte-for-byte (including their exact line terminators); replacement lines
    are written as UTF-8 and inherit the replaced line's terminator.
    """
    if selector.kind == "full":
        raise EditorError(
            "⚠️ <b>ɪɴᴠᴀʟɪᴅ ʀᴀɴɢᴇ.</b> /replace ɴᴇᴇᴅs ᴀ ʟɪɴᴇ ɴᴜᴍʙᴇʀ ᴏʀ ʀᴀɴɢᴇ, "
            "ɴᴏᴛ <code>full</code>."
        )
    width = selector.width
    if len(replacements) != width:
        raise EditorError(count_mismatch_text(width, len(replacements)))

    # Pass 1 – validate bounds BEFORE modifying anything.
    total = count_lines(path)
    if total < selector.end:
        raise EditorError(
            f"⚠️ <b>ʟɪɴᴇ ᴏᴜᴛ ᴏғ ʙᴏᴜɴᴅs.</b> ᴛʜᴇ ғɪʟᴇ ʜᴀs {total} ʟɪɴᴇ(s), "
            f"ʙᴜᴛ ʟɪɴᴇ {selector.end} ᴡᴀs ʀᴇǫᴜᴇsᴛᴇᴅ."
        )

    encoding = detect_encoding(path)

    # Pass 2 – stream the copy with replacements (bounded memory).
    replaced = 0
    with open(path, "rb") as src, open(out_path, "wb") as dst:
        for lineno, raw in enumerate(src, start=1):
            if selector.start <= lineno <= selector.end:
                idx = lineno - selector.start
                dst.write(replacements[idx].encode("utf-8") + _terminator(raw))
                replaced += 1
            else:
                dst.write(raw)
    return ReplaceResult(total, replaced, encoding)


def verify_output(out_path: Path, expected_lines: int) -> None:
    """Verify the freshly written output file before it is uploaded."""
    if not out_path.is_file():
        raise EditorError("⚠️ <b>ᴏᴜᴛᴘᴜᴛ ᴡʀɪᴛᴇ ғᴀɪʟᴇᴅ.</b> ᴘʟᴇᴀsᴇ ᴛʀʏ ᴀɢᴀɪɴ.")
    if count_lines(out_path) != expected_lines:
        raise EditorError(
            "⚠️ <b>ᴏᴜᴛᴘᴜᴛ ᴠᴇʀɪғɪᴄᴀᴛɪᴏɴ ғᴀɪʟᴇᴅ.</b> ᴛʜᴇ ᴇᴅɪᴛᴇᴅ ғɪʟᴇ ᴡᴀs ɴᴏᴛ "
            "ᴡʀɪᴛᴛᴇɴ ᴄᴏʀʀᴇᴄᴛʟʏ — ᴛʜᴇ ᴏʀɪɢɪɴᴀʟ ᴡᴀs ɴᴏᴛ ᴛᴏᴜᴄʜᴇᴅ."
        )


def editor_output_name(original: str) -> str:
    """
    Path-safe filename for the edited copy (never the original path).

    Strips directory components, control characters and absurd lengths so a
    malicious document name can never traverse the temp directory.
    """
    name = (original or "edited.txt").replace("\\", "/").rsplit("/", 1)[-1]
    name = re.sub(r"[\x00-\x1f\x7f]", "", name).strip()
    if not name or name in (".", ".."):
        name = "edited.txt"
    if len(name) > 200:
        stem, ext = os.path.splitext(name)
        name = stem[: 200 - len(ext)] + ext
    return name


# ──────────────────────────────────────────────────────────────────────────
# /detail + Telegram branding detection (conservative).
# ──────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class BrandingMatch:
    line: int   # 1-based
    value: str  # detected value, e.g. "@OldChannel" / "https://t.me/OldChannel"


# Ordered so finditer always consumes the longest, most specific match.
_TG_URL_RE = re.compile(
    r"https?://(?:www\.)?t\.me/[A-Za-z0-9_]+"
    r"|www\.t\.me/[A-Za-z0-9_]+"
    r"|(?<![A-Za-z0-9_./:-])t\.me/[A-Za-z0-9_]+"
    r"|tg://resolve\?domain=[A-Za-z0-9_]+"
)
# @handle: 5-32 chars, must not sit inside a word or after a dot (emails,
# @app.route etc.). Decorator-like positions are filtered separately below.
_HANDLE_RE = re.compile(r"(?<![\w.])@[A-Za-z0-9_]{5,32}")


def _decorator_like(line: str, start: int, length: int) -> bool:
    """
    True when the @-token at ``line[start:start+length]`` looks like a Python
    decorator (``@staticmethod``, indented ``@property``, ``@cache``) rather
    than a Telegram handle.

    A token is treated as a decorator only when it sits at line start (only
    whitespace before), is followed by end / ( / . / [ (decorator shapes), and
    is written in the lowercase/snake_case style typical of code identifiers.
    Proper-noun-style handles (@OldChannel) are still reported.
    """
    if line[:start].strip():
        return False  # inside prose → a mention, not a decorator
    rest = line[start + length:]
    if rest and rest[0] not in "(.[":
        return False  # followed by prose → a mention
    token = line[start:start + length]
    return token == token.lower()


def detect_branding(lines: Iterable[str]) -> List[BrandingMatch]:
    """
    Find Telegram-specific branding (channels / usernames) in file lines.

    Conservative by design: plain ``@name`` tokens that look like code
    decorators are skipped, and only clearly Telegram-specific URL forms are
    reported. No results are invented.
    """
    found: List[BrandingMatch] = []
    for lineno, line in enumerate(lines, start=1):
        for m in _TG_URL_RE.finditer(line):
            found.append(BrandingMatch(lineno, m.group(0)))
        for m in _HANDLE_RE.finditer(line):
            value = m.group(0)
            if not _decorator_like(line, m.start(), len(value)):
                found.append(BrandingMatch(lineno, value))
    return found


def _human_size(num: int) -> str:
    try:
        n = float(num)
    except (TypeError, ValueError):
        return "0 B"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024.0:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} TB"


def file_detail(path: Path, filename: str) -> Dict[str, object]:
    """Analyse a text file for /detail (streamed, bounded memory)."""
    encoding = detect_encoding(path)
    branding = detect_branding(_iter_decoded_lines(path, encoding))
    return {
        "filename": filename,
        "size": path.stat().st_size,
        "total_lines": count_lines(path),
        "encoding": encoding,
        "branding": branding,
    }


def render_detail(detail: Dict[str, object]) -> str:
    """Small-caps /detail reply, including branding when found."""
    matches: List[BrandingMatch] = detail.get("branding") or []
    body = [
        "📄 <b>ᴅᴇᴛᴀɪʟs</b>",
        f"• ғɪʟᴇ: <code>{html.escape(str(detail.get('filename', '')))}</code>",
        f"• sɪᴢᴇ: {_human_size(int(detail.get('size', 0) or 0))}",
        f"• ᴛᴏᴛᴀʟ ʟɪɴᴇs: {int(detail.get('total_lines', 0) or 0)}",
        f"• ᴇɴᴄᴏᴅɪɴɢ: {detail.get('encoding', '?')}",
    ]
    if matches:
        body.append("")
        body.append("🏷️ <b>ʙʀᴀɴᴅɪɴɢ ᴅᴇᴛᴇᴄᴛᴇᴅ</b>")
        for m in matches:
            body.append(f"• ʟɪɴᴇ {m.line} → <code>{html.escape(m.value)}</code>")
        body.append("")
        body.append(f"ᴛᴏᴛᴀʟ: {len(matches)} ᴍᴀᴛᴄʜᴇs")
    return "\n".join(body)
