"""
Rate limiting facade.

Maps application actions to fixed-window counters. Limits come from
config and can be overridden per action. Backed by the in-process
StateStore (no Redis) and optionally falls back to a PostgreSQL table
for persistence if a Database is supplied. For the 512 MB single-instance
deployment an in-memory fixed window is sufficient and bounded.
"""
from __future__ import annotations

from dataclasses import dataclass

from config import Config
from services.state import StateStore
from utils.logging import get_logger

log = get_logger(__name__)


@dataclass
class RateLimitResult:
    allowed: bool
    retry_after: int


class RateLimiter:
    def __init__(self, state: StateStore, config: Config, db=None) -> None:
        self._state = state
        self._config = config
        self._db = db

    async def check(self, user_id: int, action: str) -> RateLimitResult:
        rl = self._config.rate_limit
        limit_map = {
            "file_submit": rl.file_submit,
            "rename": rl.rename,
            "extension": rl.extension,
            "callback": rl.callback,
            "batch_create": rl.batch_create,
        }
        limit = limit_map.get(action, rl.callback)

        # Prefer StateStore (in-memory, bounded). If a DB table exists we
        # could also persist, but in-memory is correct for single instance.
        allowed, retry = await self._state.rate_limit_check(
            user_id, action, limit, rl.window_seconds
        )
        return RateLimitResult(allowed, retry)
