"""
Asyncpg connection pool + schema initialisation.

PostgreSQL is the source of truth. The pool is small on purpose (a few
connections) to fit within the 512 MB budget.
"""
from __future__ import annotations

import asyncio
from typing import Optional

import asyncpg

from database.models import SCHEMA_SQL, SCHEMA_VERSION
from utils.logging import get_logger

log = get_logger(__name__)


class Database:
    def __init__(self, dsn: str, *, min_size: int = 1, max_size: int = 5) -> None:
        self._dsn = dsn
        self._min = min_size
        self._max = max_size
        self._pool: Optional[asyncpg.Pool] = None
        self._lock = asyncio.Lock()

    @property
    def pool(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError("Database not initialised. Call connect() first.")
        return self._pool

    async def connect(self) -> None:
        async with self._lock:
            if self._pool is not None:
                return
            # Retry briefly – Render Postgres can take a moment on cold start.
            last_exc: Optional[Exception] = None
            for attempt in range(1, 6):
                try:
                    self._pool = await asyncpg.create_pool(
                        self._dsn,
                        min_size=self._min,
                        max_size=self._max,
                        command_timeout=30,
                        # Use prepared statements carefully with some proxies.
                        statement_cache_size=100,
                    )
                    log.info("db_connected", extra={"attempt": attempt})
                    break
                except (OSError, asyncpg.PostgresError, ConnectionError) as exc:
                    last_exc = exc
                    log.warning("db_connect_retry", extra={"attempt": attempt, "error": str(exc)})
                    await asyncio.sleep(min(2 ** attempt, 15))
            if self._pool is None:
                raise RuntimeError(f"Could not connect to database: {last_exc}")
            await self.init_schema()

    async def init_schema(self) -> None:
        async with self._pool.acquire() as conn:  # type: ignore[union-attr]
            await conn.execute(SCHEMA_SQL)
            row = await conn.fetchrow("SELECT MAX(version) AS v FROM schema_version")
            version = int(row["v"]) if row and row["v"] is not None else 0
            if version < SCHEMA_VERSION:
                # Placeholder for future migrations – never destructive.
                log.info("db_schema_upgrade", extra={"from": version, "to": SCHEMA_VERSION})
            log.info("db_schema_ready", extra={"version": version})

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
            log.info("db_closed")

    async def fetch_one(self, query: str, *args):
        async with self._pool.acquire() as conn:  # type: ignore[union-attr]
            return await conn.fetchrow(query, *args)

    async def fetch_all(self, query: str, *args):
        async with self._pool.acquire() as conn:  # type: ignore[union-attr]
            return await conn.fetch(query, *args)

    async def execute(self, query: str, *args) -> str:
        async with self._pool.acquire() as conn:  # type: ignore[union-attr]
            return await conn.execute(query, *args)
