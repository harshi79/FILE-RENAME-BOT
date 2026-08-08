"""
Redis-backed ephemeral state.

Redis is used for: rate limit counters, the work queue, distributed locks,
active-job counters, per-user pending-input state, cancellation tokens and
short-lived cache. Nothing permanent and nothing containing file bytes lives
here.

Redis availability policy
-------------------------
Redis is *not* a fatal startup dependency. PostgreSQL remains the source of
truth for jobs. If Redis cannot be reached the store:

* degrades gracefully (every call returns a safe fallback / fails-open where
  that is appropriate, e.g. rate limiting never blocks everyone during an
  outage);
* logs the real exception type + sanitized message (never the REDIS_URL
  credentials) so the problem can be diagnosed;
* keeps the Telegram bot and the /health server running;
* reconnects automatically in the background with exponential backoff once
  Redis becomes reachable again.

Every Redis call is wrapped through :meth:`_safe` so a transient outage can
never crash the bot.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, Awaitable, Callable, Dict, Optional

import redis.asyncio as redis

from utils.logging import get_logger
from utils.sanitize import mask_credentials

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


def _short_reason(exc: Exception) -> dict:
    """Build a safe, credential-free structured reason for log extras."""
    return {
        "error_type": type(exc).__name__,
        "error": mask_credentials(str(exc))[:1000],
    }


class StateStore:
    def __init__(
        self,
        url: str,
        *,
        connect_timeout: float = 5,
        socket_timeout: float = 5,
        health_check_interval: float = 30,
        max_connections: int = 10,
        reconnect_base_delay: float = 1.0,
        reconnect_max_delay: float = 60.0,
    ) -> None:
        self._url = url
        self._connect_timeout = connect_timeout
        self._socket_timeout = socket_timeout
        self._health_check_interval = health_check_interval
        self._max_connections = max_connections
        self._reconnect_base_delay = reconnect_base_delay
        self._reconnect_max_delay = reconnect_max_delay

        self._redis: Optional[redis.Redis] = None
        # In-memory fallback used only when Redis is unreachable.
        self._mem: Dict[str, Any] = {}
        self._mem_counters: Dict[str, int] = {}
        self._available = False

        # Background reconnection machinery.
        self._conn_lock = asyncio.Lock()
        self._closed = asyncio.Event()
        self._wakeup = asyncio.Event()
        self._reconnect_task: Optional[asyncio.Task] = None
        self._reconnect_attempts = 0

    # ── Connection state ────────────────────────────────────────────────
    @property
    def available(self) -> bool:
        return self._available

    def _make_client(self) -> redis.Redis:
        return redis.from_url(
            self._url,
            encoding="utf-8",
            decode_responses=True,
            socket_timeout=self._socket_timeout,
            socket_connect_timeout=self._connect_timeout,
            health_check_interval=self._health_check_interval,
            max_connections=self._max_connections,
        )

    async def _attempt(self):
        """
        Try to build + ping a fresh client.

        Returns ``(client, None)`` on success or ``(None, exc)`` on failure.
        The exception is returned (not raised) so the caller can log the real,
        sanitized reason without swallowing it.
        """
        client = self._make_client()
        try:
            await client.ping()
            return client, None
        except Exception as exc:  # noqa: BLE001 - any failure means unavailable
            try:
                await client.aclose()
            except Exception:
                pass
            return None, exc

    async def _swap_client(self, new_client) -> None:
        """Atomically install a healthy client, closing any previous one."""
        async with self._conn_lock:
            old = self._redis
            self._redis = new_client
            self._available = True
            self._reconnect_attempts = 0
        if old is not None:
            try:
                await old.aclose()
            except Exception:
                pass

    async def connect(self) -> None:
        """
        Establish the initial Redis connection.

        This is intentionally non-fatal: on failure it logs the sanitized
        reason and schedules background reconnection, but never raises and
        never stops the rest of the application from starting.
        """
        client, exc = await self._attempt()
        if client is not None:
            await self._swap_client(client)
            log.info("redis_connected")
        else:
            self._available = False
            log.warning("redis_unavailable", extra=_short_reason(exc))
        self._ensure_reconnect_task()

    def _ensure_reconnect_task(self) -> None:
        if self._reconnect_task is None or self._reconnect_task.done():
            self._reconnect_task = asyncio.create_task(
                self._reconnect_loop(), name="redis-reconnect"
            )

    def _request_reconnect(self) -> None:
        """Mark Redis unavailable and wake the background loop immediately."""
        self._available = False
        self._reconnect_attempts = 0
        self._wakeup.set()
        self._ensure_reconnect_task()

    async def _wait(self, seconds: float) -> bool:
        """
        Sleep for ``seconds`` or until woken by a reconnect request.
        Returns False when the store is closed (caller should exit).
        """
        self._wakeup.clear()
        try:
            await asyncio.wait_for(self._wakeup.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass
        return not self._closed.is_set()

    async def _reconnect_loop(self) -> None:
        while not self._closed.is_set():
            if self._available:
                # Healthy: just probe periodically so a runtime outage (or a
                # dropped connection) is noticed and triggers reconnection.
                if not await self._health_ping():
                    self._available = False
                    self._reconnect_attempts = 0
                    log.warning("redis_lost")
                    continue
                if not await self._wait(self._health_check_interval):
                    break
            else:
                # Unavailable: try to reconnect with exponential backoff.
                delay = min(
                    self._reconnect_base_delay * (2 ** self._reconnect_attempts),
                    self._reconnect_max_delay,
                )
                if self._reconnect_attempts > 0:
                    if not await self._wait(delay):
                        break
                self._reconnect_attempts += 1

                client, exc = await self._attempt()
                if client is not None:
                    await self._swap_client(client)
                    log.info("redis_reconnected")
                else:
                    log.warning(
                        "redis_reconnect_failed", extra=_short_reason(exc)
                    )

    async def _health_ping(self) -> bool:
        client = self._redis
        if client is None:
            return False
        try:
            await client.ping()
            return True
        except Exception:  # noqa: BLE001
            return False

    async def close(self) -> None:
        self._closed.set()
        self._wakeup.set()
        if self._reconnect_task is not None:
            self._reconnect_task.cancel()
            try:
                await self._reconnect_task
            except (asyncio.CancelledError, Exception):
                pass
            self._reconnect_task = None
        if self._redis is not None:
            try:
                await self._redis.aclose()
            except Exception:
                pass
            self._redis = None
        self._available = False

    async def _safe(self, coro_factory: Callable[[], Awaitable], fallback: Any) -> Any:
        if self._redis is None:
            return fallback
        try:
            return await coro_factory()
        except (redis.RedisError, OSError, asyncio.TimeoutError) as exc:
            self._request_reconnect()
            log.warning("redis_error", extra=_short_reason(exc))
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
        Completely safe when Redis is down (fail-open).
        """
        if self._redis is None:
            return True, 0
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
        except (redis.RedisError, OSError, asyncio.TimeoutError, AttributeError) as exc:
            self._request_reconnect()
            log.warning("ratelimit_redis_error", extra=_short_reason(exc))
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
