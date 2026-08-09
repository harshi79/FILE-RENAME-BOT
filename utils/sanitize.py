"""
Safe-logging helpers.

Redis / Postgres connection URLs carry credentials. We must never emit those
credentials (or passwords embedded in error messages) into logs. These helpers
mask any ``user:password@`` fragment found in a URL or a raw exception string.
"""
from __future__ import annotations

import re

# Matches the userinfo (and any password) of a URL authority:
#   scheme://user:password@host:port  ->  scheme://user:***@host:port
# The bare ``host:port`` form (no "@") is intentionally left untouched so a
# plain ``redis://host:6379`` is never mangled.
_USERINFO_RE = re.compile(r"(://[^/:\s@]+):([^@\s]*)@")

# Matches a Telegram bot token (``<bot_id>:<secret>``) so it can never reach a
# log line, e.g. through an exception message raised by the Telegram library.
_BOT_TOKEN_RE = re.compile(r"\b\d{5,16}:[A-Za-z0-9_-]{30,}\b")


def mask_credentials(text: str | None) -> str:
    """Replace embedded URL credentials with a masked form, never leaking them."""
    if not text:
        return text or ""
    return _USERINFO_RE.sub(r"\1:***@", text)


def mask_secrets(text: str | None) -> str:
    """Mask URL credentials *and* bot tokens in arbitrary text (logs, tracebacks)."""
    if not text:
        return text or ""
    return _BOT_TOKEN_RE.sub("<bot_token>", mask_credentials(text))


def redact(value: str | None) -> str:
    """Alias kept for clarity when logging URL-shaped values."""
    return mask_credentials(value)
