"""
Tests for Redis state handling: availability, graceful degradation during
startup and runtime outages, background reconnection, queue fallback, and
credential-safe logging.

Run with:  python -m pytest tests/test_state.py -q
"""
from __future__ import annotations

import asyncio
import io
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from redis import exceptions as redis_exceptions

import services.state as state_mod
from services.state import StateStore
from utils.sanitize import mask_credentials


class FakeRedis:
    """
    Minimal stand-in for redis.asyncio.Redis for unit testing.

    Reads availability (ping/ops) live from a shared ``cur`` dict so that
    changing the dict simulates Redis going up/down on an *already-connected*
    client, just like a real runtime outage.
    """

    def __init__(self, cur: dict) -> None:
        self.cur = cur
        self.closed = False

    def _ops(self):
        if not self.cur.get("ops_ok", True):
            raise redis_exceptions.ConnectionError(
                "Error 111 connecting to 127.0.0.1:1. Connect call failed "
                "('127.0.0.1', 1)."
            )

    async def ping(self):
        if not self.cur.get("ping_ok", True):
            raise redis_exceptions.ConnectionError(
                "Error 111 connecting to 127.0.0.1:1. Connect call failed "
                "('127.0.0.1', 1)."
            )
        return True

    async def aclose(self):
        self.closed = True

    def pipeline(self, transaction=True):
        self._ops()
        return FakePipe(self)

    async def execute(self):
        self._ops()
        return []

    async def lpush(self, *a):
        self._ops(); return 1

    async def llen(self, *a):
        self._ops(); return 0

    async def brpop(self, *a, **k):
        self._ops(); return None

    async def lrem(self, *a):
        self._ops(); return 0

    async def expire(self, *a):
        self._ops(); return True

    async def incr(self, *a):
        self._ops(); return 1

    async def decr(self, *a):
        self._ops(); return 1

    async def get(self, *a):
        self._ops(); return None

    async def delete(self, *a):
        self._ops(); return 0

    async def keys(self, *a):
        self._ops(); return []

    async def setex(self, *a):
        self._ops(); return True

    async def exists(self, *a):
        self._ops(); return 0

    async def set(self, *a, **k):
        self._ops(); return True

    async def ttl(self, *a):
        self._ops(); return 0


class FakePipe:
    def __init__(self, client: FakeRedis) -> None:
        self._client = client

    def lpush(self, *a):
        self._client._ops(); return self

    def expire(self, *a):
        self._client._ops(); return self

    def incr(self, *a):
        self._client._ops(); return self

    async def execute(self):
        self._client._ops()
        return []


def _make_factory(cur: dict):
    return lambda *a, **k: FakeRedis(cur)


class StateStoreTests(unittest.TestCase):
    def test_redis_available(self):
        cur = {"ping_ok": True, "ops_ok": True}
        with mock.patch.object(state_mod.redis, "from_url", side_effect=_make_factory(cur)):

            async def scenario():
                st = StateStore("redis://127.0.0.1:1")
                await st.connect()
                self.assertTrue(st.available)
                self.assertIsNotNone(st._redis)
                await st.close()

            asyncio.run(scenario())

    def test_redis_unavailable_during_startup_is_nonfatal(self):
        cur = {"ping_ok": False, "ops_ok": True}
        with mock.patch.object(state_mod.redis, "from_url", side_effect=_make_factory(cur)):

            async def scenario():
                st = StateStore(
                    "redis://user:supersecret@127.0.0.1:1",
                    reconnect_base_delay=5.0,
                    reconnect_max_delay=5.0,
                )
                # Must NOT raise even though Redis is down.
                await st.connect()
                self.assertFalse(st.available)
                await st.close()

            asyncio.run(scenario())

    def test_queue_fallback_when_redis_unavailable(self):
        cur = {"ping_ok": False, "ops_ok": True}
        with mock.patch.object(state_mod.redis, "from_url", side_effect=_make_factory(cur)):

            async def scenario():
                st = StateStore(
                    "redis://127.0.0.1:1",
                    reconnect_base_delay=5.0,
                    reconnect_max_delay=5.0,
                )
                await st.connect()
                self.assertFalse(st.available)
                # Queue operations must degrade safely (no raise).
                self.assertFalse(await st.enqueue_job("j1"))
                self.assertIsNone(await st.dequeue_job(timeout=0))
                self.assertEqual(await st.queue_length(), 0)
                self.assertEqual(await st.remove_from_queue("j1"), 0)
                await st.close()

            asyncio.run(scenario())

    def test_redis_unavailable_during_runtime_degrades(self):
        cur = {"ping_ok": True, "ops_ok": True}
        with mock.patch.object(state_mod.redis, "from_url", side_effect=_make_factory(cur)):

            async def scenario():
                st = StateStore(
                    "redis://127.0.0.1:1",
                    reconnect_base_delay=0.05,
                    reconnect_max_delay=0.2,
                )
                await st.connect()
                self.assertTrue(st.available)
                # Simulate Redis going down *after* startup.
                cur["ops_ok"] = False
                self.assertEqual(await st.queue_length(), 0)
                self.assertIsNone(await st.dequeue_job(timeout=0))
                self.assertFalse(await st.enqueue_job("abc"))
                # Rate limiting fails open rather than blocking everyone.
                allowed, retry = await st.rate_limit_check(42, "rename", 12, 60)
                self.assertTrue(allowed)
                self.assertEqual(retry, 0)
                await st.close()

            asyncio.run(scenario())

    def test_redis_reconnects_when_available_again(self):
        cur = {"ping_ok": False, "ops_ok": True}
        with mock.patch.object(state_mod.redis, "from_url", side_effect=_make_factory(cur)):

            async def scenario():
                st = StateStore(
                    "redis://127.0.0.1:1",
                    reconnect_base_delay=0.05,
                    reconnect_max_delay=0.2,
                )
                await st.connect()
                self.assertFalse(st.available)
                # Redis comes back.
                cur["ping_ok"] = True
                for _ in range(200):
                    if st.available:
                        break
                    await asyncio.sleep(0.02)
                self.assertTrue(st.available)
                # And it actually works again.
                self.assertEqual(await st.queue_length(), 0)
                await st.close()

            asyncio.run(scenario())

    def test_reconnect_backoff_increases(self):
        st = StateStore("redis://127.0.0.1:1", reconnect_base_delay=2.0,
                        reconnect_max_delay=16.0)
        st._reconnect_attempts = 0
        self.assertEqual(
            min(st._reconnect_base_delay * (2 ** st._reconnect_attempts),
                st._reconnect_max_delay), 2.0)
        st._reconnect_attempts = 3
        self.assertEqual(
            min(st._reconnect_base_delay * (2 ** st._reconnect_attempts),
                st._reconnect_max_delay), 16.0)


class SanitizeTests(unittest.TestCase):
    def test_url_credentials_masked(self):
        masked = mask_credentials("redis://user:supersecret@host:6379/0")
        self.assertNotIn("supersecret", masked)
        self.assertIn("user:***@host", masked)

    def test_plain_url_not_mangled(self):
        self.assertEqual(
            mask_credentials("redis://host:6379/0"),
            "redis://host:6379/0",
        )

    def test_redis_unavailable_log_has_no_credentials(self):
        # Capture the log output for a failed connect and ensure the password
        # never appears.
        import logging
        from utils.logging import JsonFormatter

        buf = io.StringIO()
        logger = state_mod.log
        old_handlers = list(logger.handlers)
        old_propagate = logger.propagate
        logger.handlers.clear()
        logger.propagate = False
        handler = logging.StreamHandler(buf)
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
        try:
            cur = {"ping_ok": False, "ops_ok": True}
            with mock.patch.object(state_mod.redis, "from_url",
                                   side_effect=_make_factory(cur)):

                async def scenario():
                    st = StateStore(
                        "redis://user:supersecret@127.0.0.1:1",
                        reconnect_base_delay=5.0,
                        reconnect_max_delay=5.0,
                    )
                    await st.connect()
                    await st.close()

                asyncio.run(scenario())
        finally:
            logger.removeHandler(handler)
            logger.handlers = old_handlers
            logger.propagate = old_propagate
        text = buf.getvalue()
        self.assertIn("redis_unavailable", text)
        self.assertNotIn("supersecret", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
