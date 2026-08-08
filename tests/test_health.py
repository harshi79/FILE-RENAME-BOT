"""
Tests for the /health liveness endpoint.

The health server must always answer HTTP 200 OK regardless of Redis /
PostgreSQL / Telegram / worker state. It is a process-liveness probe only.
"""
from __future__ import annotations

import asyncio
import http.client
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import services.state as state_mod
from services.state import StateStore
from services.health import HealthServer
from tests.test_state import FakeRedis


def _factory(cur: dict):
    return lambda *a, **k: FakeRedis(cur)


class HealthServerTests(unittest.TestCase):
    def test_health_returns_200_while_redis_unavailable(self):
        cur = {"ping_ok": False, "ops_ok": True}
        with mock.patch.object(state_mod.redis, "from_url",
                               side_effect=_factory(cur)):

            async def scenario():
                st = StateStore(
                    "redis://user:supersecret@127.0.0.1:1",
                    reconnect_base_delay=5.0,
                    reconnect_max_delay=5.0,
                )
                await st.connect()
                return st

            st = asyncio.run(scenario())
            self.assertFalse(st.available)

        server = HealthServer("127.0.0.1", 0)
        server.start()
        try:
            conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
            conn.request("GET", "/health")
            resp = conn.getresponse()
            body = resp.read()
            self.assertEqual(resp.status, 200)
            self.assertEqual(body, b"OK")
            conn.close()
        finally:
            server.stop()
        asyncio.run(st.close())

    def test_health_returns_200_while_redis_available(self):
        cur = {"ping_ok": True, "ops_ok": True}
        with mock.patch.object(state_mod.redis, "from_url",
                               side_effect=_factory(cur)):

            async def scenario():
                st = StateStore("redis://127.0.0.1:1")
                await st.connect()
                return st

            st = asyncio.run(scenario())
            self.assertTrue(st.available)

        server = HealthServer("127.0.0.1", 0)
        server.start()
        try:
            conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
            conn.request("GET", "/health")
            resp = conn.getresponse()
            self.assertEqual(resp.status, 200)
            conn.close()
        finally:
            server.stop()
        asyncio.run(st.close())


if __name__ == "__main__":
    unittest.main(verbosity=2)
