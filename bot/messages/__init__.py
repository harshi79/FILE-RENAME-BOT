"""All user-facing text lives here.

The UI uses a small-caps / decorative unicode style as requested.
"""
from __future__ import annotations

from typing import Iterable

# ──────────────────────────────────────────────────────────────────────────
# General
# ──────────────────────────────────────────────────────────────────────────
WELCOME = (
    "🎬 <b>ꜰɪʟᴇ ʀᴇɴᴀᴍᴇʀ ʙᴏᴛ</b>\n\n"
    "ꜱᴇɴᴅ ᴍᴇ ᴀɴʏ <b>ᴏʀᴅɪɴᴀʀʏ ꜱɪɴɢʟᴇ ꜰɪʟᴇ</b> ᴀɴᴅ ɪ ᴡɪʟʟ ʀᴇɴᴀᴍᴇ ɪᴛ ꜰᴏʀ ʏᴏᴜ.\n\n"
    "📄 ᴛᴇxᴛ / ᴄᴏᴅᴇ / ᴄᴏɴꜰɪɢ / ᴅᴏᴄᴜᴍᴇɴᴛ ꜰɪʟᴇꜱ\n"
    "🚫 ɴᴏ ᴍᴇᴅɪᴀ, ɴᴏ ᴀʀᴄʜɪᴠᴇꜱ, ɴᴏ ᴘʜᴏᴛᴏꜱ ᴏʀ ᴠɪᴅᴇᴏꜱ\n"
    "📦 ᴍᴀxɪᴍᴜᴍ ꜱɪᴢᴇ: <b>{max_mb} MB</b>\n\n"
    "ᴜꜱᴇ /help ꜰᴏʀ ᴄᴏᴍᴍᴀɴᴅꜱ."
)

HELP = (
    "📚 <b>ʜᴏᴡ ᴛᴏ ᴜꜱᴇ</b>\n\n"
    "1️⃣ ꜱᴇɴᴅ ᴀ ꜰɪʟᴇ (ᴅᴏᴄᴜᴍᴇɴᴛ)\n"
    "2️⃣ ᴛᴀᴘ <b>✏️ ʀᴇɴᴀᴍᴇ</b> ᴛᴏ ᴋᴇᴇᴘ ᴛʜᴇ ᴇxᴛᴇɴꜱɪᴏɴ\n"
    "3️⃣ ᴛᴀᴘ <b>🔄 ᴇxᴛᴇɴꜱɪᴏɴ</b> ᴛᴏ ᴄʜᴀɴɢᴇ ɪᴛ\n"
    "4️⃣ ꜱᴇɴᴅ ᴍᴜʟᴛɪᴘʟᴇ ꜰɪʟᴇꜱ ꜰᴏʀ <b>📦 ʙᴀᴛᴄʜ ᴍᴏᴅᴇ</b>\n\n"
    "⚙️ <b>ᴀᴅᴠᴀɴᴄᴇᴅ</b>: ꜰɪɴᴅ &amp; ʀᴇᴘʟᴀᴄᴇ, ᴘʀᴇꜰɪx / ꜱᴜꜰꜰɪx, "
    "ᴡʜɪᴛᴇꜱᴘᴀᴄᴇ ᴄʟᴇᴀɴᴜᴘ, ᴄᴀꜱᴇ ᴄᴏɴᴠᴇʀꜱɪᴏɴ, ꜱᴇQᴜᴇɴᴛɪᴀʟ ɴᴜᴍʙᴇʀɪɴɢ.\n\n"
    "📜 /history – ʏᴏᴜʀ ʀᴇɴᴀᴍᴇ ʜɪꜱᴛᴏʀʏ\n"
    "⚙️ /settings – ᴘʀᴇꜰᴇʀᴇɴᴄᴇꜱ\n"
    "❌ /cancel – ᴄᴀɴᴄᴇʟ ᴛʜᴇ ᴄᴜʀʀᴇɴᴛ ᴏᴘᴇʀᴀᴛɪᴏɴ"
)

# ──────────────────────────────────────────────────────────────────────────
# File received
# ──────────────────────────────────────────────────────────────────────────
FILE_RECEIVED = (
    "📁 <b>ꜰɪʟᴇ ʀᴇᴄᴇɪᴠᴇᴅ</b>\n\n"
    "📄 <b>ꜰɪʟᴇɴᴀᴍᴇ:</b> <code>{name}</code>\n"
    "📦 <b>ꜱɪᴢᴇ:</b> {size}\n"
    "🔤 <b>ᴇxᴛᴇɴꜱɪᴏɴ:</b> <code>{ext}</code>\n\n"
    "ᴄʜᴏᴏꜱᴇ ᴀɴ ᴀᴄᴛɪᴏɴ:"
)

BATCH_RECEIVED = (
    "📦 <b>ʙᴀᴛᴄʜ ᴍᴏᴅᴇ</b> – {count} ꜰɪʟᴇ(ꜱ) Qᴜᴇᴜᴇᴅ\n\n"
    "ꜱᴇʟᴇᴄᴛ ᴀɴ ᴀᴄᴛɪᴏɴ ᴛᴏ ᴀᴘᴘʟʏ ᴛᴏ <b>ᴀʟʟ</b> ꜰɪʟᴇꜱ:"
)

# ──────────────────────────────────────────────────────────────────────────
# Rename / extension prompts
# ──────────────────────────────────────────────────────────────────────────
RENAME_PROMPT = (
    "📝 <b>ꜱᴇɴᴅ ᴍᴇ ᴛʜᴇ ɴᴇᴡ ꜰɪʟᴇ ɴᴀᴍᴇ</b>\n\n"
    "📄 <b>ᴄᴜʀʀᴇɴᴛ ꜰɪʟᴇ:</b> <code>{current}</code>\n\n"
    "⚠️ <b>ᴏʀɪɢɪɴᴀʟ ᴇxᴛᴇɴꜱɪᴏɴ ᴡɪʟʟ ʙᴇ ᴘʀᴇꜱᴇʀᴠᴇᴅ.</b>"
)

EXTENSION_PROMPT = (
    "🔄 <b>ꜱᴇɴᴅ ᴛʜᴇ ɴᴇᴡ ᴇxᴛᴇɴꜱɪᴏɴ</b>\n\n"
    "📄 <b>ᴄᴜʀʀᴇɴᴛ ꜰɪʟᴇ:</b> <code>{current}</code>\n"
    "ᴇxᴀᴍᴘʟᴇ: <code>md</code> ᴏʀ <code>.md</code>\n\n"
    "⚠️ ᴛʜɪꜱ ᴏɴʟʏ ᴄʜᴀɴɢᴇꜱ ᴛʜᴇ ꜰɪʟᴇɴᴀᴍᴇ, ɴᴏᴛ ᴛʜᴇ ꜰɪʟᴇ ᴄᴏɴᴛᴇɴᴛꜱ."
)

BATCH_RENAME_PROMPT = (
    "📦 <b>ʙᴀᴛᴄʜ ʀᴇɴᴀᴍᴇ</b>\n\n"
    "ꜱᴇɴᴅ ᴛʜᴇ ʙᴀꜱᴇ ɴᴀᴍᴇ. ᴇᴀᴄʜ ꜰɪʟᴇ ᴡɪʟʟ ʙᴇ ɴᴜᴍʙᴇʀᴇᴅ:\n"
    "<code>{base} 01{ext}</code>, <code>{base} 02{ext}</code>, …\n\n"
    "⚠️ ᴏʀɪɢɪɴᴀʟ ᴇxᴛᴇɴꜱɪᴏɴꜱ ᴀʀᴇ ᴘʀᴇꜱᴇʀᴠᴇᴅ."
)

ADVANCED_PROMPT_FIND = (
    "🔎 <b>ꜰɪɴᴅ &amp; ʀᴇᴘʟᴀᴄᴇ</b>\n\n"
    "ꜱᴇɴᴅ ɪɴ ᴛʜᴇ ꜰᴏʀᴍᴀᴛ:\n"
    "<code>find|replace</code>\n\n"
    "ᴇxᴀᴍᴘʟᴇ: <code>episode|Episode</code>"
)

PREFIX_PROMPT = "🔹 <b>ꜱᴇɴᴅ ᴛʜᴇ ᴘʀᴇꜰɪx ᴛᴏ ᴀᴅᴅ:</b>"
SUFFIX_PROMPT = "🔸 <b>ꜱᴇɴᴅ ᴛʜᴇ ꜱᴜꜰꜰɪx ᴛᴏ ᴀᴅᴅ:</b>"
REMOVE_PREFIX_PROMPT = "🔹 <b>ꜱᴇɴᴅ ᴛʜᴇ ᴘʀᴇꜰɪx ᴛᴏ ʀᴇᴍᴏᴠᴇ:</b>"
REMOVE_SUFFIX_PROMPT = "🔸 <b>ꜱᴇɴᴅ ᴛʜᴇ ꜱᴜꜰꜰɪx ᴛᴏ ʀᴇᴍᴏᴠᴇ (ʙᴇꜰᴏʀᴇ ᴛʜᴇ ᴇxᴛ):</b>"
ZEROPAD_PROMPT = (
    "🔢 <b>ꜱᴇQᴜᴇɴᴛɪᴀʟ ɴᴜᴍʙᴇʀɪɴɢ</b>\n\n"
    "ꜱᴇɴᴅ: <code>base|start|pad</code>\n"
    "ᴇxᴀᴍᴘʟᴇ: <code>Episode|1|2</code>\n"
    "ʀᴇꜱᴜʟᴛ: <code>Episode 01.txt</code>"
)

# ──────────────────────────────────────────────────────────────────────────
# Progress / status
# ──────────────────────────────────────────────────────────────────────────
STATUS_DOWNLOADING = "⬇️ <b>ᴅᴏᴡɴʟᴏᴀᴅɪɴɢ…</b> {percent}%"
STATUS_RENAMING = "✏️ <b>ʀᴇɴᴀᴍɪɴɢ…</b>"
STATUS_UPLOADING = "⬆️ <b>ᴜᴘʟᴏᴀᴅɪɴɢ…</b> {percent}%"
STATUS_QUEUED = "🕒 <b>Qᴜᴇᴜᴇᴅ…</b> ᴡᴀɪᴛɪɴɢ ꜰᴏʀ ᴀ ᴡᴏʀᴋᴇʀ."
STATUS_CLEANING = "🧹 <b>ᴄʟᴇᴀɴɪɴɢ ᴜᴘ…</b>"

JOB_DONE = "✅ <b>ᴅᴏɴᴇ!</b>\n<code>{name}</code>"
JOB_BATCH_DONE = "✅ <b>ʙᴀᴛᴄʜ ᴄᴏᴍᴘʟᴇᴛᴇ</b> – {ok}/{total} ꜰɪʟᴇꜱ ʀᴇɴᴀᴍᴇᴅ."
JOB_FAILED = "❌ <b>ꜰᴀɪʟᴇᴅ</b> – {reason}"
JOB_CANCELLED = "🚫 <b>ᴄᴀɴᴄᴇʟʟᴇᴅ.</b>"

CANCEL_IN_PROGRESS = "🛑 ᴄᴀɴᴄᴇʟʟɪɴɢ…"
NOTHING_TO_CANCEL = "ℹ️ ɴᴏᴛʜɪɴɢ ᴛᴏ ᴄᴀɴᴄᴇʟ."

# ──────────────────────────────────────────────────────────────────────────
# Errors / rejections
# ──────────────────────────────────────────────────────────────────────────
ERR_TOO_LARGE = (
    "❌ <b>ꜰɪʟᴇ ᴛᴏᴏ ʟᴀʀɢᴇ</b>\n\n"
    "ᴍᴀxɪᴍᴜᴍ ꜱɪᴢᴇ: <b>{max_mb} MB</b>"
)
ERR_UNSUPPORTED = (
    "🚫 <b>ᴜɴꜱᴜᴘᴘᴏʀᴛᴇᴅ ꜰɪʟᴇ ᴛʏᴘᴇ</b>\n\n"
    "ᴛʜɪꜱ ʙᴏᴛ ʀᴇɴᴀᴍᴇꜱ ᴏʀᴅɪɴᴀʀʏ ꜱɪɴɢʟᴇ ꜰɪʟᴇꜱ ᴏɴʟʏ.\n"
    "ᴍᴇᴅɪᴀ, ᴘʜᴏᴛᴏꜱ, ᴠɪᴅᴇᴏꜱ, ᴀᴜᴅɪᴏ ᴀɴᴅ ᴀʀᴄʜɪᴠᴇꜱ ᴀʀᴇ ɴᴏᴛ ᴀᴄᴄᴇᴘᴛᴇᴅ."
)
ERR_ARCHIVE = (
    "🚫 <b>ᴀʀᴄʜɪᴠᴇꜱ ᴀʀᴇ ɴᴏᴛ ᴀʟʟᴏᴡᴇᴅ</b>\n\n"
    "ᴅᴏ ɴᴏᴛ ꜱᴇɴᴅ .ᴢɪᴘ, .ʀᴀʀ, .7ᴢ, .ᴛᴀʀ, .ɢᴢ, ᴇᴛᴄ."
)
ERR_MEDIA = (
    "🚫 <b>ᴍᴇᴅɪᴀ ɪꜱ ɴᴏᴛ ᴀʟʟᴏᴡᴇᴅ</b>\n\n"
    "ᴘʜᴏᴛᴏꜱ, ᴠɪᴅᴇᴏꜱ ᴀɴᴅ ᴀᴜᴅɪᴏ ᴍᴜꜱᴛ ʙᴇ ꜱᴇɴᴛ ᴀꜱ ᴏʀᴅɪɴᴀʀʏ ᴅᴏᴄᴜᴍᴇɴᴛꜱ.\n"
    "ᴍᴇᴅɪᴀ ʀᴇɴᴀᴍɪɴɢ ɪꜱ ɴᴏᴛ ꜱᴜᴘᴘᴏʀᴛᴇᴅ."
)
ERR_NOT_DOCUMENT = (
    "🚫 <b>ᴘʟᴇᴀꜱᴇ ꜱᴇɴᴅ ᴀ ꜰɪʟᴇ ᴀꜱ ᴀ ᴅᴏᴄᴜᴍᴇɴᴛ</b>\n\n"
    "ᴘʜᴏᴛᴏꜱ, ᴠɪᴅᴇᴏꜱ, ꜱᴛɪᴄᴋᴇʀꜱ, ᴠᴏɪᴄᴇ ᴍᴇꜱꜱᴀɢᴇꜱ ᴇᴛᴄ. ᴀʀᴇ ɴᴏᴛ ꜱᴜᴘᴇʀᴠɪꜱᴇᴅ."
)
ERR_NO_FILENAME = "⚠️ ᴄᴏᴜʟᴅ ɴᴏᴛ ᴅᴇᴛᴇʀᴍɪɴᴇ ᴀ ꜰɪʟᴇɴᴀᴍᴇ. ʀᴇ-ꜱᴇɴᴅ ᴛʜᴇ ꜰɪʟᴇ ᴀꜱ ᴀ ᴅᴏᴄᴜᴍᴇɴᴛ."
ERR_INVALID_NAME = "⚠️ <b>ɪɴᴠᴀʟɪᴅ ꜰɪʟᴇ ɴᴀᴍᴇ.</b> {reason}"
ERR_INVALID_EXT = "⚠️ <b>ɪɴᴠᴀʟɪᴅ ᴇxᴛᴇɴꜱɪᴏɴ.</b> {reason}"
ERR_QUEUE_FULL = (
    "🧧 <b>Qᴜᴇᴜᴇ ɪꜱ ꜰᴜʟʟ</b>\n\n"
    "ᴛʜᴇ ʙᴏᴛ ɪꜱ ʙᴜꜱʏ ʀɪɢʜᴛ ɴᴏᴡ. ᴘʟᴇᴀꜱᴇ ᴡᴀɪᴛ ᴀ ᴍᴏᴍᴇɴᴛ ᴀɴᴅ ᴛʀʏ ᴀɢᴀɪɴ."
)
ERR_USER_BUSY = (
    "🚫 <b>ʏᴏᴜ ᴀʟʀᴇᴀᴅʏ ʜᴀᴠᴇ ᴀɴ ᴀᴄᴛɪᴠᴇ ᴊᴏʙ.</b>\n\n"
    "ᴡᴀɪᴛ ꜰᴏʀ ɪᴛ ᴛᴏ ꜰɪɴɪꜱʜ ᴏʀ /cancel ɪᴛ ꜰɪʀꜱᴛ."
)
ERR_RATE_LIMIT = "⏳ <b>ꜱʟᴏᴡ ᴅᴏᴡɴ!</b> ᴛʀʏ ᴀɢᴀɪɴ ɪɴ {seconds}ꜱ."
ERR_NO_FILE = "⚠️ ɴᴏ ꜰɪʟᴇ ꜱᴇʟᴇᴄᴛᴇᴅ. ꜱᴇɴᴅ ᴀ ꜰɪʟᴇ ꜰɪʀꜱᴛ."
ERR_BAD_INPUT = "⚠️ ɪ ɪɴᴠᴀʟɪᴅ ɪɴᴘᴜᴛ. {reason}"
ERR_NO_DISK = "❌ <b>ɴᴏᴛ ᴇɴᴏᴜɢʜ ᴅɪꜱᴋ ꜱᴘᴀᴄᴇ</b> ᴛᴏ ᴘʀᴏᴄᴇꜱꜱ ᴛʜɪꜱ ꜰɪʟᴇ."
ERR_GENERIC = "❌ <b>ꜱᴏᴍᴇᴛʜɪɴɢ ᴡᴇɴᴛ ᴡʀᴏɴɢ.</b> ᴘʟᴇᴀꜱᴇ ᴛʀʏ ᴀɢᴀɪɴ ʟᴀᴛᴇʀ."
ERR_BANNED = "🚫 ʏᴏᴜ ʜᴀᴠᴇ ʙᴇᴇɴ ʙᴀɴɴᴇᴅ ꜰʀᴏᴍ ᴜꜱɪɴɢ ᴛʜɪꜱ ʙᴏᴛ."
ERR_NOT_ADMIN = "🔒 ᴀᴅᴍɪɴ ᴏɴʟʏ."
ERR_BAD_CALLBACK = "⚠️ ɪɴᴠᴀʟɪᴅ ᴏʀ ᴇxᴘɪʀᴇᴅ ʙᴜᴛᴛᴏɴ."

STATE_CLEARED = "🗑️ ᴄᴜʀʀᴇɴᴛ ᴏᴘᴇʀᴀᴛɪᴏɴ ᴄᴀɴᴄᴇʟʟᴇᴅ."
CLOSED = "🚪 ᴄʟᴏꜱᴇᴅ."

# ──────────────────────────────────────────────────────────────────────────
# Settings / history / admin
# ──────────────────────────────────────────────────────────────────────────
SETTINGS_MENU = (
    "⚙️ <b>ꜱᴇᴛᴛɪɴɢꜱ</b>\n\n"
    "🔠 ᴄᴀꜱᴇ: <b>{case_mode}</b>\n"
    "🧹 ᴡʜɪᴛᴇꜱᴘᴀᴄᴇ ᴄʟᴇᴀɴᴜᴘ: <b>{ws_mode}</b>\n"
    "🔢 ɴᴜᴍʙᴇʀ ꜰᴏʀᴍᴀᴛ: <b>{num_mode}</b>\n"
)

HISTORY_HEADER = "📜 <b>ʜɪꜱᴛᴏʀʏ</b> – ᴘᴀɢᴇ {page}/{pages}\n\n"
HISTORY_ROW = "• <code>{old}</code> → <code>{new}</code>\n  <i>{op} · {size} · {ts}</i>\n"
HISTORY_EMPTY = "📭 ɴᴏ ʜɪꜱᴛᴏʀʏ ʏᴇᴛ."

ADMIN_MENU = (
    "🛠️ <b>ᴀᴅᴍɪɴ ᴘᴀɴᴇʟ</b>\n\n"
    "👥 ᴜꜱᴇʀꜱ: {users}\n"
    "📊 ᴛᴏᴛᴀʟ ᴊᴏʙꜱ: {jobs}\n"
    "✅ ᴄᴏᴍᴘʟᴇᴛᴇᴅ: {completed}\n"
    "❌ ꜰᴀɪʟᴇᴅ: {failed}\n"
    "🕒 Qᴜᴇᴜᴇᴅ: {queued}\n"
    "⚡ ᴀᴄᴛɪᴠᴇ: {active}"
)


def render_history(rows: Iterable, page: int, pages: int) -> str:
    body = HISTORY_HEADER.format(page=page, pages=max(1, pages))
    items = list(rows)
    if not items:
        return body + HISTORY_EMPTY
    for r in items:
        body += HISTORY_ROW.format(
            old=r["original_name"],
            new=r["new_name"],
            op=r.get("operation", "rename"),
            size=human_size(r.get("file_size") or 0),
            ts=(r.get("created_at") or "").strftime("%Y-%m-%d %H:%M") if hasattr(r.get("created_at"), "strftime") else str(r.get("created_at") or ""),
        )
    return body


def human_size(num: int) -> str:
    try:
        n = float(num)
    except (TypeError, ValueError):
        return "0 B"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024.0:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} TB"
