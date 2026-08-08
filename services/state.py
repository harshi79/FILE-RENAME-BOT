"""
Redis-backed ephemeral state.

Redis is used for: rate limit counters, the work queue, distributed locks,
active-job counters, per-user pending-input state, cancellation tokens and
short-lived cache. Nothing permanent and nothing containing file bytes lives
here.

Every Redis call is wrapped so that a Redis outage degrades gracefully
(rate limiting / counters fail-open where safe) instead of crashing the bot.
PostgreSQL remains the source of truth for jobs.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, Awaitable, Callable, Dict, Optional

import redis.asyncio as redis

from utils.logging import get_logger

log = get_logger(__name__)


def _job_queue_key() -> str:
    return "fr:queue:jobs"


def _lock_key(name: str) -> str:
    return f"fr:lock:{name}"


def _active_global_key() -> str:
    return "fr:active:global"


def _active_user_key(user_id: int) -> str:
    return f"fr:active:user:{user_id}"


def _cancel_key(job_id: str) -> str:
    return f"fr:cancel:{job_id}"


def _state_key(user_id: int) -> str:
    return f"fr:state:{user_id}"


def _rate_key(user_id: int, action: str) -> str:
    return f"fr:rate:{action}:{user_id}"


def _dedup_key(identifier: str) -> str:
    return f"fr:dedup:{identifier}"


class StateStore:
    def __init__(self, url: str) -> None:
        self._url = url
        self._redis: Optional[redis.Redis] = None
        # In-memory fallback used only when Redis is unreachable.
        self._mem: Dict[str, Any] = {}
        self._mem_counters: Dict[str, int] = {}
        self._available = True

    @property
    def available(self) -> bool:
        return self._available

    async def connect(self) -> None:
        try:
            self._redis = redis.from_url(
                self._url,
                encoding="utf-8",
                decode_responses=True,
                socket_timeout=5,
                socket_connect_timeout=5,
                health_check_interval=30,
                max_connections=10,
            )
            await self._redis.ping()
            self._available = True
            log.info("redis_connected")
        except Exception as exc:  # pragma: no cover - network dependent
            self._redis = None
            self._available = False
            log.warning("redis_unavailable", extra={"error": str(exc)})

    async def close(self) -> None:
        if self._redis is not None:
            try:
                await self._redis.aclose()
            except Exception:
                pass
            self._redis = None

    async def _safe(self, coro_factory: Callable[[], Awaitable], fallback: Any) -> Any:
        if self._redis is None:
            return fallback
        try:
            return await coro_factory()
        except (redis.RedisError, OSError, asyncio.TimeoutError) as exc:
            self._available = False
            log.warning("redis_error", extra={"error": str(exc)})
            return fallback

    # ── Queue ──────────────────────────────────────────────────────────
    async def enqueue_job(self, job_id: str) -> bool:
        async def _do():
            pipe = self._redis.pipeline(transaction=True)
            pipe.lpush(_job_queue_key(), job_id)
            pipe.expire(_job_queue_key(), 3600)
            await pipe.execute()
            return True
        return await self._safe(_do, False)

    async def dequeue_job(self, timeout: int = 5) -> Optional[str]:
        async def _do():
            res = await self._redis.brpop(_job_queue_key(), timeout=timeout)
            if not res:
                return None
            # brpop returns (key, value)
            return res[1] if isinstance(res, (list, tuple)) else res
        return await self._safe(_do, None)

    async def queue_length(self) -> int:
        async def _do():
            return int(await self._redis.llen(_job_queue_key()) or 0)
        return await self._safe(_do, 0)

    async def remove_from_queue(self, job_id: str) -> int:
        async def _do():
            return int(await self._redis.lrem(_job_queue_key(), 0, job_id) or 0)
        return await self._safe(_do, 0)

    # ── Active job counters ────────────────────────────────────────────
    async def incr_active(self, user_id: int) -> tuple:
        """Increment global + per-user counters. Returns (global, user)."""
        async def _do():
            g = await self._redis.incr(_active_global_key())
            u = await self._redis.incr(_active_user_key(user_id))
            await self._redis.expire(_active_global_key(), 3600)
            await self._redis.expire(_active_user_key(user_id), 3600)
            return int(g), int(u)
        return await self._safe(_do, (1, 1))

    async def decr_active(self, user_id: int) -> None:
        async def _do():
            g = await self._redis.decr(_active_global_key())
            u = await self._redis.decr(_active_user_key(user_id))
            if g is not None and int(g) <= 0:
                await self._redis.delete(_active_global_key())
            if u is not None and int(u) <= 0:
                await self._redis.delete(_active_user_key(user_id))
        await self._safe(_do, None)

    async def active_counts(self) -> tuple:
        async def _do():
            g = await self._redis.get(_active_global_key())
            return int(g or 0), -1
        return await self._safe(_do, (0, -1))

    async def user_active_count(self, user_id: int) -> int:
        async def _do():
            return int(await self._redis.get(_active_user_key(user_id)) or 0)
        return await self._safe(_do, 0)

    async def reset_active_counters(self) -> None:
        async def _do():
            keys = await self._redis.keys("fr:active:*")
            if keys:
                await self._redis.delete(*keys)
        await self._safe(_do, None)

    # ── Cancellation ───────────────────────────────────────────────────
    async def request_cancel(self, job_id: str) -> None:
        async def _do():
            await self._redis.setex(_cancel_key(job_id), 3600, "1")
        await self._safe(_do, None)

    async def is_cancelled(self, job_id: str) -> bool:
        async def _do():
            return await self._redis.exists(_cancel_key(job_id)) == 1
        return await self._safe(_do, False)

    async def clear_cancel(self, job_id: str) -> None:
        async def _do():
            await self._redis.delete(_cancel_key(job_id))
        await self._safe(_do, None)

    # ── Per-user pending input state ───────────────────────────────────
    async def set_user_state(self, user_id: int, state: Dict[str, Any], ttl: int = 600) -> None:
        async def _do():
            await self._redis.setex(_state_key(user_id), ttl, json.dumps(state))
        await self._safe(_do, None)

    async def get_user_state(self, user_id: int) -> Optional[Dict[str, Any]]:
        async def _do():
            raw = await self._redis.get(_state_key(user_id))
            if not raw:
                return None
            return json.loads(raw)
        return await self._safe(_do, None)

    async def clear_user_state(self, user_id: int) -> None:
        async def _do():
            await self._redis.delete(_state_key(user_id))
        await self._safe(_do, None)

    # ── Rate limiting (fixed window) ───────────────────────────────────
    async def rate_limit_check(self, user_id: int, action: str, limit: int, window: int) -> tuple:
        """
        Returns (allowed: bool, retry_after_seconds: int).
        """
        key = _rate_key(user_id, action)

        async def _do():
            pipe = self._redis.pipeline()
            pipe.incr(key)
            pipe.expire(key, window, nx=True)
            count, _ = await pipe.execute()
            count = int(count or 0)
            if count > limit:
                ttl = int(await self._redis.ttl(key) or window)
                return False, max(1, ttl)
            return True, 0
        try:
            return await _do()
        except (redis.RedisError, OSError, asyncio.TimeoutError) as exc:
            self._available = False
            log.warning("ratelimit_redis_error", extra={"error": str(exc)})
            # Fail open so a Redis outage never blocks all users.
            return True, 0

    # ── Deduplication locks ────────────────────────────────────────────
    async def acquire_dedup(self, identifier: str, ttl: int = 5) -> bool:
        async def _do():
            return bool(await self._redis.set(_dedup_key(identifier), "1", nx=True, ex=ttl))
        return await self._safe(_do, True)

    async def release_dedup(self, identifier: str) -> None:
        async def _do():
            await self._redis.delete(_dedup_key(identifier))
        await self._safe(_do, None)

    # ── Distributed lock (best-effort, short TTL) ──────────────────────
    async def acquire_lock(self, name: str, ttl: int = 30) -> bool:
        token = str(uuid.uuid4())
        async def _do():
            ok = await self._redis.set(_lock_key(name), token, nx=True, ex=ttl)
            return bool(ok)
        return await self._safe(_do, True)

    async def release_lock(self, name: str) -> None:
        async def _do():
            await self._redis.delete(_lock_key(name))
        await self._safe(_do, None)

    async def ping(self) -> bool:
        if self._redis is None:
            return False
        try:
            return bool(await self._redis.ping())
        except Exception:
            return False
