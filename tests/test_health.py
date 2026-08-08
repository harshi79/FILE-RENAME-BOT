"""
Tests for the /health liveness endpoint.

The health server must always answer HTTP 200 OK regardless of DB/
Telegram/worker state. It is dependency-free.
"""
from __future__ import annotations

import http.client
import sys
import os
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.health import HealthServer


class HealthServerTests(unittest.TestCase):
    def test_health_returns_200(self):
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

    def test_health_root(self):
        server = HealthServer("127.0.0.1", 0)
        server.start()
        try:
            conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
            conn.request("GET", "/")
            resp = conn.getresponse()
            self.assertEqual(resp.status, 200)
            body = resp.read()
            self.assertIn(b"file-renamer-bot", body)
            conn.close()
        finally:
            server.stop()

    def test_health_not_found(self):
        server = HealthServer("127.0.0.1", 0)
        server.start()
        try:
            conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
            conn.request("GET", "/unknown")
            resp = conn.getresponse()
            self.assertEqual(resp.status, 404)
            conn.close()
        finally:
            server.stop()

    def test_health_is_dependency_free(self):
        # No DB, no state, no workers — just health server alone must still answer.
        server = HealthServer("127.0.0.1", 0)
        server.start()
        try:
            conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
            conn.request("GET", "/health")
            resp = conn.getresponse()
            self.assertEqual(resp.status, 200)
            conn.close()
            # second request still works
            conn2 = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
            conn2.request("GET", "/health")
            resp2 = conn2.getresponse()
            self.assertEqual(resp2.status, 200)
            conn2.close()
        finally:
            server.stop()


if __name__ == "__main__":
    unittest.main(verbosity=2)
