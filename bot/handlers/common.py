"""
Shared helpers for Telegram handlers.

Holds references to services and exposes small wrappers for user state,
banning and rate-limit enforcement.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from pyrogram import Client
from pyrogram.types import Message, User

from config import Config
from database.database import Database
from database import queries
from services.rate_limit import RateLimiter
from services.state import StateStore
from utils.logging import get_logger

log = get_logger(__name__)


class HandlerContext:
    def __init__(self, app: Client, db: Database, state: StateStore,
                 rate_limiter: RateLimiter, config: Config) -> None:
        self.app = app
        self.db = db
        self.state = state
        self.rate_limiter = rate_limiter
        self.config = config

    async def ensure_user(self, message: Message) -> Optional[Dict[str, Any]]:
        user: User = message.from_user
        if user is None:
            return None
        is_admin = self.config.is_admin(user.id)
        try:
            await queries.upsert_user(self.db, user.id, user.username, user.first_name, is_admin)
        except Exception as exc:
            log.error("upsert_user_failed", extra={"error": str(exc)})
        record = await queries.get_user(self.db, user.id)
        return record

    async def is_banned(self, user_id: int) -> bool:
        try:
            row = await queries.get_user(self.db, user_id)
            return bool(row and row.get("is_banned"))
        except Exception:
            return False

    async def rate_limited(self, message: Message, action: str) -> bool:
        """Returns True if the request was rate limited (and already replied)."""
        user = message.from_user
        if user is None or self.config.is_admin(user.id):
            return False
        result = await self.rate_limiter.check(user.id, action)
        if not result.allowed:
            try:
                from bot import messages as M
                await message.reply(M.ERR_RATE_LIMIT.format(seconds=result.retry_after), quote=True)
            except Exception:
                pass
            return True
        return False
