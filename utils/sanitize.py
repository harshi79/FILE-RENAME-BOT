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


def mask_credentials(text: str | None) -> str:
    """Replace embedded URL credentials with a masked form, never leaking them."""
    if not text:
        return text or ""
    return _USERINFO_RE.sub(r"\1:***@", text)


def redact(value: str | None) -> str:
    """Alias kept for clarity when logging URL-shaped values."""
    return mask_credentials(value)
