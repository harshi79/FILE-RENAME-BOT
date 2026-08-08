"""
Central application configuration.

All credentials and tunable limits are loaded from environment variables.
The process fails fast with a clear error if a required variable is missing.
Credentials are never logged.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Set

# Default welcome video shown by /start (can be overridden via START_VIDEO_URL).
DEFAULT_START_VIDEO_URL = "https://files.catbox.moe/5qz09e.mp4"

# Load a local .env file if present (safe no-op in production).
try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover - dotenv is optional at runtime
    pass


class ConfigurationError(Exception):
    """Raised when the application is misconfigured."""


def _get_env(name: str, default: Optional[str] = None, *, required: bool = False) -> str:
    value = os.environ.get(name, default)
    if required and (value is None or str(value).strip() == ""):
        raise ConfigurationError(
            f"Missing required environment variable: {name}. "
            f"Set it in your Render dashboard / .env file and restart."
        )
    return "" if value is None else str(value).strip()


def _get_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        value = int(str(raw).strip())
        if value <= 0:
            raise ValueError
        return value
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(f"{name} must be a positive integer, got: {raw!r}") from exc


def _get_port(default: int = 8080) -> int:
    """Render sets $PORT. HEALTH_PORT still overrides for local runs."""
    raw = os.environ.get("HEALTH_PORT") or os.environ.get("PORT")
    if raw is None or str(raw).strip() == "":
        return default
    try:
        port = int(str(raw).strip())
        if not (1 <= port <= 65535):
            raise ValueError
        return port
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(f"PORT must be an integer 1-65535, got: {raw!r}") from exc


def _parse_admin_ids(raw: str) -> Set[int]:
    admins: Set[int] = set()
    for chunk in raw.replace(";", ",").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            admins.add(int(chunk))
        except ValueError as exc:
            raise ConfigurationError(f"ADMIN_IDS contains a non-integer value: {chunk!r}") from exc
    if not admins:
        raise ConfigurationError("ADMIN_IDS must contain at least one numeric Telegram user ID.")
    return admins


@dataclass(frozen=True)
class RateLimitConfig:
    """Per-user fixed-window limits (actions per window seconds)."""

    window_seconds: int = 60
    file_submit: int = 10
    rename: int = 12
    extension: int = 12
    callback: int = 40
    batch_create: int = 5

    @classmethod
    def from_env(cls) -> "RateLimitConfig":
        # RATE_LIMIT can override the whole window; e.g. RATE_LIMIT=60
        window = _get_int("RATE_LIMIT", 60)
        return cls(
            window_seconds=max(10, window),
            file_submit=_get_int("RATE_LIMIT_FILE_SUBMIT", 10),
            rename=_get_int("RATE_LIMIT_RENAME", 12),
            extension=_get_int("RATE_LIMIT_EXTENSION", 12),
            callback=_get_int("RATE_LIMIT_CALLBACK", 40),
            batch_create=_get_int("RATE_LIMIT_BATCH", 5),
        )


@dataclass(frozen=True)
class Config:
    # Telegram / API
    api_id: int
    api_hash: str
    bot_token: str

    # Data stores
    database_url: str
    redis_url: str

    # Operators
    admin_ids: frozenset

    # Limits / resources
    max_file_size: int           # bytes
    max_global_active_jobs: int
    max_active_jobs_per_user: int
    max_retries: int
    job_timeout: int             # seconds
    max_queue_size: int
    history_page_size: int
    admin_page_size: int

    # Paths
    temp_dir: Path
    download_chunk_size: int = 1024 * 1024  # 1 MiB streaming chunks

    # Health server
    health_host: str = "0.0.0.0"
    health_port: int = 8080

    # Optional UI
    start_video_url: str = ""

    # Rate limiting
    rate_limit: RateLimitConfig = field(default_factory=RateLimitConfig)

    def is_admin(self, user_id: int) -> bool:
        return user_id in self.admin_ids


def load_config() -> Config:
    api_id_raw = _get_env("API_ID", required=True)
    try:
        api_id = int(api_id_raw)
    except ValueError as exc:
        raise ConfigurationError(f"API_ID must be an integer, got: {api_id_raw!r}") from exc

    db_url = _get_env("DATABASE_URL", required=True)
    # Render/Heroku style postgres:// must be translated for asyncpg.
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)

    redis_url = _get_env("REDIS_URL", required=True)
    if redis_url.startswith("rediss://"):
        # redis-py handles rediss natively; nothing to do.
        pass

    max_file_mb = _get_int("MAX_FILE_SIZE_MB", 25)
    if max_file_mb > 100:
        raise ConfigurationError("MAX_FILE_SIZE_MB must be <= 100 MB for this deployment.")

    temp_dir = Path(_get_env("TEMP_DIR", "/tmp/file-renamer")).resolve()

    return Config(
        api_id=api_id,
        api_hash=_get_env("API_HASH", required=True),
        bot_token=_get_env("BOT_TOKEN", required=True),
        database_url=db_url,
        redis_url=redis_url,
        admin_ids=frozenset(_parse_admin_ids(_get_env("ADMIN_IDS", required=True))),
        max_file_size=max_file_mb * 1024 * 1024,
        max_global_active_jobs=_get_int("MAX_GLOBAL_ACTIVE_JOBS", 2),
        max_active_jobs_per_user=_get_int("MAX_ACTIVE_JOBS_PER_USER", 1),
        max_retries=_get_int("MAX_RETRIES", 3),
        job_timeout=_get_int("JOB_TIMEOUT", 300),
        max_queue_size=_get_int("MAX_QUEUE_SIZE", 20),
        history_page_size=_get_int("HISTORY_PAGE_SIZE", 8),
        admin_page_size=_get_int("ADMIN_PAGE_SIZE", 10),
        temp_dir=temp_dir,
        health_host=_get_env("HEALTH_HOST", "0.0.0.0"),
        health_port=_get_port(8080),
        start_video_url=_get_env("START_VIDEO_URL", DEFAULT_START_VIDEO_URL),
        rate_limit=RateLimitConfig.from_env(),
    )


# Lazy singleton; imported across the codebase.
_settings: Optional[Config] = None


def get_config() -> Config:
    global _settings
    if _settings is None:
        _settings = load_config()
    return _settings
