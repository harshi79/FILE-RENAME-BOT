"""
Rate limiting facade.

Maps application actions to Redis fixed-window counters. Limits come from
config and can be overridden per action.
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
    def __init__(self, state: StateStore, config: Config) -> None:
        self._state = state
        self._config = config

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
        allowed, retry = await self._state.rate_limit_check(
            user_id, action, limit, rl.window_seconds
        )
        return RateLimitResult(allowed, retry)
