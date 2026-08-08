"""Structured-ish JSON-line logging configuration.

Keeps dependencies at zero (stdlib only) and emits single-line records so they
play nicely with Render's log aggregation.
"""
from __future__ import annotations

import json
import logging
import sys
import time
from typing import Any, Dict

from utils.sanitize import mask_secrets


# LogRecord attributes that are part of the standard record and should never be
# dumped as structured extras (they are not error payloads).
_RESERVED = {
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "taskName", "message", "asctime",
}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        # ``exc_info=True`` outside an ``except`` block yields (None, None, None),
        # which formats to the useless "NoneType: None". Only emit ``exc`` when a
        # real exception is attached, and mask any credentials in the traceback.
        if record.exc_info and record.exc_info[0] is not None:
            payload["exc"] = mask_secrets(self.formatException(record.exc_info))
        # Attach structured extras (logging .extra={...}) so error details are
        # never silently swallowed. Keys are stored on the record's __dict__.
        for key, value in record.__dict__.items():
            if key in _RESERVED or key.startswith("_"):
                continue
            payload[key] = value
        return json.dumps(payload, ensure_ascii=False)


def setup_logging(level: str = "INFO") -> None:
    log_level = level.upper()
    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(log_level)

    # Quiet noisy libraries.
    logging.getLogger("pyrogram").setLevel(logging.WARNING)
    logging.getLogger("pyrogram.session").setLevel(logging.ERROR)
    logging.getLogger("asyncio").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
