"""
Inline keyboard construction.

Callback data is kept short (<64 bytes) and prefixed so handlers can route it.
"""
from __future__ import annotations

from typing import List

from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

# Callback prefixes
CB_RENAME = "rename"
CB_EXTENSION = "ext"
CB_CLOSE = "close"
CB_CANCEL = "cancel"
CB_BATCH_RENAME = "b_rename"
CB_BATCH_PREFIX = "b_prefix"
CB_BATCH_SUFFIX = "b_suffix"
CB_BATCH_REPLACE = "b_replace"
CB_BATCH_NUMBER = "b_number"
CB_BATCH_EXT = "b_ext"
CB_BATCH_WS = "b_ws"
CB_BATCH_CASE = "b_case"
CB_BATCH_CANCEL = "b_cancel"
CB_SETTINGS = "settings"
CB_HISTORY = "history"
CB_ADMIN = "admin"
CB_PAGE = "page"


def file_actions_keyboard(job_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✏️ ʀᴇɴᴀᴍᴇ", callback_data=f"{CB_RENAME}:{job_id}"),
            InlineKeyboardButton("🔄 ᴇxᴛᴇɴsɪᴏɴ", callback_data=f"{CB_EXTENSION}:{job_id}"),
        ],
        [InlineKeyboardButton("❌ ᴄʟᴏsᴇ", callback_data=f"{CB_CLOSE}:{job_id}")],
    ])


def processing_keyboard(job_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ ᴄᴀɴᴄᴇʟ", callback_data=f"{CB_CANCEL}:{job_id}")],
    ])


def batch_actions_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✏️ ʀᴇɴᴀᴍᴇ", callback_data=CB_BATCH_RENAME),
            InlineKeyboardButton("🔄 ᴇxᴛᴇɴsɪᴏɴ", callback_data=CB_BATCH_EXT),
        ],
        [
            InlineKeyboardButton("🔹 ᴘʀᴇғɪx", callback_data=CB_BATCH_PREFIX),
            InlineKeyboardButton("🔸 sᴜғғɪx", callback_data=CB_BATCH_SUFFIX),
        ],
        [
            InlineKeyboardButton("🔎 ꜰɪɴᴅ/ʀᴇᴘʟᴀᴄᴇ", callback_data=CB_BATCH_REPLACE),
            InlineKeyboardButton("🔢 ɴᴜᴍʙᴇʀ", callback_data=CB_BATCH_NUMBER),
        ],
        [
            InlineKeyboardButton("🧹 ᴡʜɪᴛᴇsᴘᴀᴄᴇ", callback_data=CB_BATCH_WS),
            InlineKeyboardButton("🔠 ᴄᴀsᴇ", callback_data=CB_BATCH_CASE),
        ],
        [InlineKeyboardButton("❌ ᴄᴀɴᴄᴇʟ ʙᴀᴛᴄʜ", callback_data=CB_BATCH_CANCEL)],
    ])


def cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ ᴄᴀɴᴄᴇʟ", callback_data="cancel_state")]])


def main_menu_keyboard(is_admin: bool = False) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton("📜 ʜɪsᴛᴏʀʏ", callback_data=CB_HISTORY),
            InlineKeyboardButton("⚙️ sᴇᴛᴛɪɴɢs", callback_data=CB_SETTINGS),
        ],
    ]
    if is_admin:
        rows.append([InlineKeyboardButton("🛠️ ᴀᴅᴍɪɴ", callback_data=CB_ADMIN)])
    return InlineKeyboardMarkup(rows)


def settings_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔠 ᴄᴀsᴇ ᴍᴏᴅᴇ", callback_data="set:case")],
        [InlineKeyboardButton("🧹 ᴡʜɪᴛᴇsᴘᴀᴄᴇ", callback_data="set:ws")],
        [InlineKeyboardButton("🔢 ɴᴜᴍ ғᴏʀᴍᴀᴛ", callback_data="set:num")],
        [InlineKeyboardButton("⬅️ ʙᴀᴄᴋ", callback_data="main")],
    ])


def admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("👥 ᴜsᴇʀs", callback_data="adm:users:0"),
            InlineKeyboardButton("📊 ᴊᴏʙs", callback_data="adm:jobs:0"),
        ],
        [
            InlineKeyboardButton("❌ ғᴀɪʟᴇᴅ", callback_data="adm:failed:0"),
            InlineKeyboardButton("📈 sᴛᴀᴛs", callback_data="adm:stats"),
        ],
        [InlineKeyboardButton("⬅️ ʙᴀᴄᴋ", callback_data="main")],
    ])


def pagination_keyboard(prefix: str, page: int, total_pages: int) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    nav: List[InlineKeyboardButton] = []
    if page > 1:
        nav.append(InlineKeyboardButton("◀️", callback_data=f"{prefix}:{page-1}"))
    nav.append(InlineKeyboardButton(f"ᴘᴀɢᴇ {page}/{max(1,total_pages)}", callback_data="noop"))
    if page < total_pages:
        nav.append(InlineKeyboardButton("▶️", callback_data=f"{prefix}:{page+1}"))
    rows.append(nav)
    rows.append([InlineKeyboardButton("⬅️ ʙᴀᴄᴋ", callback_data="main")])
    return InlineKeyboardMarkup(rows)


def users_admin_keyboard(rows, page: int, total_pages: int) -> InlineKeyboardMarkup:
    """Per-user ban/unban toggle plus pagination."""
    buttons: List[List[InlineKeyboardButton]] = []
    for r in rows:
        uid = r["user_id"]
        label = f"🚫 ban {uid}" if not r.get("is_banned") else f"✅ unban {uid}"
        buttons.append([InlineKeyboardButton(label, callback_data=f"ban:{uid}")])
    nav: List[InlineKeyboardButton] = []
    if page > 1:
        nav.append(InlineKeyboardButton("◀️", callback_data=f"adm:users:{page-1}"))
    nav.append(InlineKeyboardButton(f"ᴘᴀɢᴇ {page}/{max(1,total_pages)}", callback_data="noop"))
    if page < total_pages:
        nav.append(InlineKeyboardButton("▶️", callback_data=f"adm:users:{page+1}"))
    buttons.append(nav)
    buttons.append([InlineKeyboardButton("⬅️ ʙᴀᴄᴋ", callback_data="admin")])
    return InlineKeyboardMarkup(buttons)
