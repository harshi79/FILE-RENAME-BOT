"""
Minimal HTTP health server (stdlib only – keeps RAM low).

Runs in its own daemon thread so Telegram processing is never blocked. It is
deliberately independent of Telegram, PostgreSQL, Redis and the workers: its
only job is to tell Render / UptimeRobot that the web process is alive.

  GET /health  -> 200 "OK"
  GET /        -> 200 with a tiny JSON service descriptor

Bound to 0.0.0.0:$PORT (Render) or HEALTH_PORT with a safe 8080 default.
"""
from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from utils.logging import get_logger

log = get_logger(__name__)

_OK_BODY = b"OK"


class _Handler(BaseHTTPRequestHandler):
    server_version = "FileRenamerBot/1.0"

    def log_message(self, format: str, *args) -> None:  # silence access logs
        return

    def _write(self, code: int, body: bytes, content_type: str = "text/plain; charset=utf-8") -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self) -> None:  # noqa: N802
        path = (self.path or "/").split("?", 1)[0]
        if path in ("/health", "/healthz", "/ping"):
            # Process is alive – intentionally no dependency checks.
            self._write(200, _OK_BODY)
        elif path == "/":
            self._write(200, b'{"service":"file-renamer-bot","status":"ok"}',
                        "application/json")
        else:
            self._write(404, b"Not Found")

    def do_HEAD(self) -> None:  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(_OK_BODY)))
        self.end_headers()


class HealthServer:
    def __init__(self, host: str = "0.0.0.0", port: int = 8080) -> None:
        self.host = host
        self.port = port
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._server = ThreadingHTTPServer((self.host, self.port), _Handler)
        # Reflect the actual bound port (useful when port=0 / ephemeral).
        self.port = int(self._server.server_address[1])
        self._server.daemon_threads = True
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="health-http",
            daemon=True,
        )
        self._thread.start()
        log.info("health_server_started", extra={"host": self.host, "port": self.port})

    def set_healthy(self, healthy: bool) -> None:
        # Retained for API compatibility; /health reports liveness only.
        pass

    def stop(self) -> None:
        if self._server is not None:
            try:
                self._server.shutdown()
            except Exception:
                pass
            try:
                self._server.server_close()
            except Exception:
                pass
            self._server = None
