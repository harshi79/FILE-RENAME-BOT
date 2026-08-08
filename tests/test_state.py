"""
Tests for the in-process StateStore (no Redis).

Verifies bounded queue, TTL, cancellation, rate limiting, dedup, locks,
and active counters. Also validates credential masking.
"""
from __future__ import annotations

import asyncio
import io
import os
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.state import StateStore
from utils.sanitize import mask_credentials


class StateStoreQueueTests(unittest.TestCase):
    def test_bounded_queue(self):
        async def scenario():
            s = StateStore(max_queue_size=3)
            self.assertEqual(await s.queue_length(), 0)
            self.assertTrue(await s.enqueue_job("j1"))
            self.assertTrue(await s.enqueue_job("j2"))
            self.assertTrue(await s.enqueue_job("j3"))
            self.assertFalse(await s.enqueue_job("j4"))  # full
            self.assertEqual(await s.queue_length(), 3)
            # dedup
            self.assertFalse(await s.enqueue_job("j2"))
            self.assertEqual(await s.dequeue_job(timeout=1), "j1")
            self.assertEqual(await s.queue_length(), 2)
            # remove specific
            self.assertEqual(await s.remove_from_queue("j2"), 1)
            self.assertEqual(await s.queue_length(), 1)
            self.assertFalse(await s.is_queued("j2"))
            self.assertTrue(await s.is_queued("j3"))
            await s.close()
        asyncio.run(scenario())

    def test_dequeue_timeout(self):
        async def scenario():
            s = StateStore(max_queue_size=5)
            start = time.time()
            res = await s.dequeue_job(timeout=1)
            elapsed = time.time() - start
            self.assertIsNone(res)
            self.assertGreaterEqual(elapsed, 0.9)
            await s.close()
        asyncio.run(scenario())

    def test_dequeue_returns_fifo(self):
        async def scenario():
            s = StateStore(max_queue_size=5)
            await s.enqueue_job("a")
            await s.enqueue_job("b")
            await s.enqueue_job("c")
            self.assertEqual(await s.dequeue_job(timeout=1), "a")
            self.assertEqual(await s.dequeue_job(timeout=1), "b")
            self.assertEqual(await s.dequeue_job(timeout=1), "c")
            await s.close()
        asyncio.run(scenario())

    def test_enqueue_wakes_dequeue(self):
        async def scenario():
            s = StateStore(max_queue_size=5)
            result = {}
            async def waiter():
                result["val"] = await s.dequeue_job(timeout=5)
            task = asyncio.create_task(waiter())
            await asyncio.sleep(0.1)
            await s.enqueue_job("wake")
            await asyncio.wait_for(task, timeout=2)
            self.assertEqual(result["val"], "wake")
            await s.close()
        asyncio.run(scenario())


class StateStoreUserStateTests(unittest.TestCase):
    def test_user_state_ttl(self):
        async def scenario():
            s = StateStore()
            await s.set_user_state(42, {"action": "rename"}, ttl=1)
            self.assertEqual(await s.get_user_state(42), {"action": "rename"})
            await asyncio.sleep(1.2)
            self.assertIsNone(await s.get_user_state(42))
            await s.close()
        asyncio.run(scenario())

    def test_user_state_clear(self):
        async def scenario():
            s = StateStore()
            await s.set_user_state(42, {"a": 1})
            await s.clear_user_state(42)
            self.assertIsNone(await s.get_user_state(42))
            await s.close()
        asyncio.run(scenario())


class StateStoreActiveCountersTests(unittest.TestCase):
    def test_incr_decr(self):
        async def scenario():
            s = StateStore()
            g, u = await s.incr_active(1)
            self.assertEqual(g, 1)
            self.assertEqual(u, 1)
            g, u = await s.incr_active(1)
            self.assertEqual(u, 2)
            self.assertEqual(await s.user_active_count(1), 2)
            await s.decr_active(1)
            self.assertEqual(await s.user_active_count(1), 1)
            await s.decr_active(1)
            self.assertEqual(await s.user_active_count(1), 0)
            await s.close()
        asyncio.run(scenario())

    def test_reset(self):
        async def scenario():
            s = StateStore()
            await s.incr_active(1)
            await s.incr_active(2)
            await s.reset_active_counters()
            self.assertEqual(await s.active_counts(), (0, -1))
            await s.close()
        asyncio.run(scenario())


class StateStoreCancelTests(unittest.TestCase):
    def test_cancel_flow(self):
        async def scenario():
            s = StateStore()
            self.assertFalse(await s.is_cancelled("j1"))
            await s.request_cancel("j1")
            self.assertTrue(await s.is_cancelled("j1"))
            await s.clear_cancel("j1")
            self.assertFalse(await s.is_cancelled("j1"))
            await s.close()
        asyncio.run(scenario())


class StateStoreRateLimitTests(unittest.TestCase):
    def test_fixed_window(self):
        async def scenario():
            s = StateStore()
            # limit 2 per 5 seconds
            self.assertEqual(await s.rate_limit_check(1, "rename", 2, 5), (True, 0))
            self.assertEqual(await s.rate_limit_check(1, "rename", 2, 5), (True, 0))
            allowed, retry = await s.rate_limit_check(1, "rename", 2, 5)
            self.assertFalse(allowed)
            self.assertGreaterEqual(retry, 1)
            # different user not affected
            self.assertEqual(await s.rate_limit_check(2, "rename", 2, 5), (True, 0))
            # different action not affected
            self.assertEqual(await s.rate_limit_check(1, "callback", 2, 5), (True, 0))
            await s.close()
        asyncio.run(scenario())

    def test_rate_limit_resets_after_window(self):
        async def scenario():
            s = StateStore()
            await s.rate_limit_check(1, "rename", 1, 1)
            allowed, _ = await s.rate_limit_check(1, "rename", 1, 1)
            self.assertFalse(allowed)
            await asyncio.sleep(1.2)
            allowed, _ = await s.rate_limit_check(1, "rename", 1, 1)
            self.assertTrue(allowed)
            await s.close()
        asyncio.run(scenario())


class StateStoreDedupLockTests(unittest.TestCase):
    def test_dedup(self):
        async def scenario():
            s = StateStore()
            self.assertTrue(await s.acquire_dedup("id1", ttl=2))
            self.assertFalse(await s.acquire_dedup("id1", ttl=2))
            await s.release_dedup("id1")
            self.assertTrue(await s.acquire_dedup("id1", ttl=2))
            # expiry
            self.assertTrue(await s.acquire_dedup("id2", ttl=1))
            await asyncio.sleep(1.2)
            self.assertTrue(await s.acquire_dedup("id2", ttl=1))
            await s.close()
        asyncio.run(scenario())

    def test_lock(self):
        async def scenario():
            s = StateStore()
            self.assertTrue(await s.acquire_lock("res", ttl=5))
            self.assertFalse(await s.acquire_lock("res", ttl=5))
            await s.release_lock("res")
            self.assertTrue(await s.acquire_lock("res", ttl=5))
            await s.close()
        asyncio.run(scenario())

    def test_lock_ttl(self):
        async def scenario():
            s = StateStore()
            self.assertTrue(await s.acquire_lock("res", ttl=1))
            await asyncio.sleep(1.2)
            self.assertTrue(await s.acquire_lock("res", ttl=1))
            await s.close()
        asyncio.run(scenario())


class SanitizeTests(unittest.TestCase):
    def test_url_credentials_masked(self):
        masked = mask_credentials("postgresql://user:supersecret@host:5432/0")
        self.assertNotIn("supersecret", masked)
        self.assertIn("user:***@host", masked)

    def test_plain_url_not_mangled(self):
        self.assertEqual(mask_credentials("redis://host:6379/0"), "redis://host:6379/0")

    def test_postgres_url_masked(self):
        self.assertNotIn("secret", mask_credentials("postgresql://u:secret@host/db"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
