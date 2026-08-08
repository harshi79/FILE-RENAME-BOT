"""
Minimal HTTP health server (stdlib only – keeps RAM low).

Runs in its own thread so Telegram processing is never blocked. /health
returns 200 once the app is ready; / returns basic info. The server binds to
0.0.0.0 and the configured port (8080 by default) so Render can probe it.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from utils.logging import get_logger

log = get_logger(__name__)


class _Handler(BaseHTTPRequestHandler):
    healthy = False

    def log_message(self, format: str, *args) -> None:  # silence default logging
        return

    def _send(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path in ("/health", "/healthz"):
            code = 200 if self.healthy else 503
            self._send(code, {"status": "ok" if self.healthy else "starting"})
        elif self.path == "/":
            self._send(200, {"service": "file-renamer-bot", "healthy": self.healthy})
        else:
            self._send(404, {"error": "not found"})

    def do_HEAD(self) -> None:  # noqa: N802
        self.send_response(200 if self.healthy else 503)
        self.end_headers()


class HealthServer:
    def __init__(self, host: str = "0.0.0.0", port: int = 8080) -> None:
        self.host = host
        self.port = port
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        _Handler.healthy = False

    def start(self) -> None:
        self._server = ThreadingHTTPServer((self.host, self.port), _Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever,
                                        name="health-http", daemon=True)
        self._thread.start()
        log.info("health_server_started", extra={"host": self.host, "port": self.port})

    def set_healthy(self, healthy: bool) -> None:
        _Handler.healthy = healthy

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
