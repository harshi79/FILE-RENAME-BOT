"""
All SQL queries used by the application.

One layer above the pool so handlers / workers never embed raw SQL.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from database.database import Database
from database.models import JobStatus


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _row_to_dict(row) -> Dict[str, Any]:
    return dict(row) if row is not None else {}


# ──────────────────────────────────────────────────────────────────────
# Users / settings
# ──────────────────────────────────────────────────────────────────────
async def upsert_user(db: Database, user_id: int, username: str, first_name: str,
                      is_admin: bool = False) -> None:
    await db.execute(
        """
        INSERT INTO users(user_id, username, first_name, is_admin, last_seen_at)
        VALUES ($1, $2, $3, $4, NOW())
        ON CONFLICT (user_id) DO UPDATE
            SET username    = EXCLUDED.username,
                first_name  = EXCLUDED.first_name,
                is_admin    = users.is_admin OR EXCLUDED.is_admin,
                last_seen_at = NOW()
        """,
        user_id, username or "", first_name or "", is_admin,
    )
    await db.execute(
        """
        INSERT INTO user_settings(user_id) VALUES ($1)
        ON CONFLICT (user_id) DO NOTHING
        """,
        user_id,
    )


async def get_user(db: Database, user_id: int) -> Optional[Dict[str, Any]]:
    row = await db.fetch_one("SELECT * FROM users WHERE user_id = $1", user_id)
    return _row_to_dict(row)


async def set_banned(db: Database, user_id: int, banned: bool) -> None:
    await db.execute("UPDATE users SET is_banned = $2 WHERE user_id = $1", user_id, banned)


async def list_users(db: Database, offset: int, limit: int) -> List[Dict[str, Any]]:
    rows = await db.fetch_all(
        "SELECT * FROM users ORDER BY created_at DESC LIMIT $1 OFFSET $2",
        limit, offset,
    )
    return [_row_to_dict(r) for r in rows]


async def count_users(db: Database) -> int:
    row = await db.fetch_one("SELECT COUNT(*) AS c FROM users")
    return int(row["c"]) if row else 0


async def get_settings(db: Database, user_id: int) -> Dict[str, Any]:
    row = await db.fetch_one("SELECT * FROM user_settings WHERE user_id = $1", user_id)
    if row:
        return _row_to_dict(row)
    return {"user_id": user_id, "case_mode": "none", "ws_mode": "off", "num_mode": "2"}


async def update_setting(db: Database, user_id: int, field: str, value: str) -> None:
    if field not in {"case_mode", "ws_mode", "num_mode"}:
        raise ValueError("invalid setting field")
    await db.execute(
        f"UPDATE user_settings SET {field} = $2, updated_at = NOW() WHERE user_id = $1",
        user_id, value,
    )


# ──────────────────────────────────────────────────────────────────────
# Jobs
# ──────────────────────────────────────────────────────────────────────
async def create_job(
    db: Database,
    *,
    user_id: int,
    chat_id: int,
    original_name: str,
    file_size: int,
    file_id: str,
    file_ref: Optional[str],
    operation: str = "rename",
    batch_id: Optional[uuid.UUID] = None,
    request_msg_id: Optional[int] = None,
) -> str:
    job_id = uuid.uuid4()
    await db.execute(
        """
        INSERT INTO jobs(id, user_id, batch_id, status, operation, original_name,
                         file_size, file_id, file_ref, chat_id, request_msg_id)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
        """,
        job_id, user_id, batch_id, JobStatus.PENDING.value, operation, original_name,
        file_size, file_id, file_ref, chat_id, request_msg_id,
    )
    return str(job_id)


async def get_job(db: Database, job_id: str) -> Optional[Dict[str, Any]]:
    row = await db.fetch_one("SELECT * FROM jobs WHERE id = $1", uuid.UUID(job_id))
    return _row_to_dict(row)


async def set_job_status(
    db: Database,
    job_id: str,
    status: JobStatus,
    *,
    new_name: Optional[str] = None,
    error: Optional[str] = None,
    status_msg_id: Optional[int] = None,
    result_msg_id: Optional[int] = None,
    increment_attempts: bool = False,
) -> None:
    set_parts: List[str] = []
    positional: List[Any] = [uuid.UUID(job_id)]  # $1 = id
    next_idx = 2

    set_parts.append(f"status = ${next_idx}")
    positional.append(status.value)
    next_idx += 1

    if status in {JobStatus.DOWNLOADING, JobStatus.RENAMING,
                  JobStatus.UPLOADING, JobStatus.CLEANING}:
        set_parts.append("started_at = COALESCE(started_at, NOW())")
    if status in JobStatus.terminal_states():
        set_parts.append("completed_at = NOW()")

    if new_name is not None:
        set_parts.append(f"new_name = ${next_idx}")
        positional.append(new_name)
        next_idx += 1
    if error is not None:
        set_parts.append(f"error = ${next_idx}")
        positional.append(error[:1000])
        next_idx += 1
    if status_msg_id is not None:
        set_parts.append(f"status_msg_id = ${next_idx}")
        positional.append(status_msg_id)
        next_idx += 1
    if result_msg_id is not None:
        set_parts.append(f"result_msg_id = ${next_idx}")
        positional.append(result_msg_id)
        next_idx += 1
    if increment_attempts:
        set_parts.append("attempts = attempts + 1")

    sql = f"UPDATE jobs SET {', '.join(set_parts)} WHERE id = $1"
    await db.execute(sql, *positional)


async def set_job_plan(db: Database, job_id: str, operation: str, new_name: str,
                       status_msg_id: Optional[int] = None) -> None:
    if status_msg_id is not None:
        await db.execute(
            "UPDATE jobs SET operation = $2, new_name = $3, status = $4, status_msg_id = $5 WHERE id = $1",
            uuid.UUID(job_id), operation, new_name, JobStatus.QUEUED.value, status_msg_id,
        )
    else:
        await db.execute(
            "UPDATE jobs SET operation = $2, new_name = $3, status = $4 WHERE id = $1",
            uuid.UUID(job_id), operation, new_name, JobStatus.QUEUED.value,
        )


async def list_jobs_by_status(db: Database, statuses: List[str], limit: int = 50) -> List[Dict[str, Any]]:
    rows = await db.fetch_all(
        "SELECT * FROM jobs WHERE status = ANY($1::text[]) ORDER BY created_at ASC LIMIT $2",
        statuses, limit,
    )
    return [_row_to_dict(r) for r in rows]


async def list_recent_jobs(db: Database, offset: int, limit: int) -> List[Dict[str, Any]]:
    rows = await db.fetch_all(
        "SELECT * FROM jobs ORDER BY created_at DESC LIMIT $1 OFFSET $2", limit, offset
    )
    return [_row_to_dict(r) for r in rows]


async def count_jobs(db: Database, status: Optional[str] = None) -> int:
    if status:
        row = await db.fetch_one("SELECT COUNT(*) AS c FROM jobs WHERE status = $1", status)
    else:
        row = await db.fetch_one("SELECT COUNT(*) AS c FROM jobs")
    return int(row["c"]) if row else 0


async def count_user_active_jobs(db: Database, user_id: int) -> int:
    row = await db.fetch_one(
        "SELECT COUNT(*) AS c FROM jobs WHERE user_id = $1 AND status = ANY($2::text[])",
        user_id, list(JobStatus.active_states()),
    )
    return int(row["c"]) if row else 0


async def get_stale_jobs(db: Database, cutoff: datetime) -> List[Dict[str, Any]]:
    rows = await db.fetch_all(
        "SELECT * FROM jobs WHERE status = ANY($1::text[]) AND started_at < $2",
        [JobStatus.DOWNLOADING.value, JobStatus.RENAMING.value,
         JobStatus.UPLOADING.value, JobStatus.CLEANING.value],
        cutoff,
    )
    return [_row_to_dict(r) for r in rows]


async def reset_stale_jobs(db: Database, cutoff: datetime) -> int:
    result = await db.execute(
        """
        UPDATE jobs SET status = $1, error = 'recovered after restart', completed_at = NOW()
        WHERE status = ANY($2::text[]) AND started_at < $3
        """,
        JobStatus.FAILED.value,
        [JobStatus.DOWNLOADING.value, JobStatus.RENAMING.value,
         JobStatus.UPLOADING.value, JobStatus.CLEANING.value],
        cutoff,
    )
    # asyncpg returns "UPDATE n"
    try:
        return int(result.split()[-1])
    except Exception:
        return 0


# ──────────────────────────────────────────────────────────────────────
# History
# ──────────────────────────────────────────────────────────────────────
async def add_history(
    db: Database,
    *,
    user_id: int,
    job_id: Optional[str],
    operation: str,
    original_name: str,
    new_name: str,
    file_size: int,
    status: str,
) -> None:
    jid = uuid.UUID(job_id) if job_id else None
    await db.execute(
        """
        INSERT INTO history(user_id, job_id, operation, original_name, new_name, file_size, status)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        """,
        user_id, jid, operation, original_name, new_name, file_size, status,
    )


async def list_history(db: Database, user_id: int, offset: int, limit: int):
    rows = await db.fetch_all(
        "SELECT * FROM history WHERE user_id = $1 ORDER BY created_at DESC LIMIT $2 OFFSET $3",
        user_id, limit, offset,
    )
    return [_row_to_dict(r) for r in rows]


async def count_history(db: Database, user_id: int) -> int:
    row = await db.fetch_one("SELECT COUNT(*) AS c FROM history WHERE user_id = $1", user_id)
    return int(row["c"]) if row else 0


# ──────────────────────────────────────────────────────────────────────
# Admin stats
# ──────────────────────────────────────────────────────────────────────
async def admin_stats(db: Database) -> Dict[str, int]:
    totals = await db.fetch_one(
        """
        SELECT
            (SELECT COUNT(*) FROM users) AS users,
            (SELECT COUNT(*) FROM jobs) AS jobs,
            (SELECT COUNT(*) FROM jobs WHERE status = 'COMPLETED') AS completed,
            (SELECT COUNT(*) FROM jobs WHERE status = 'FAILED') AS failed,
            (SELECT COUNT(*) FROM jobs WHERE status = 'QUEUED') AS queued,
            (SELECT COUNT(*) FROM jobs WHERE status = ANY($1::text[])) AS active
        """,
        [JobStatus.DOWNLOADING.value, JobStatus.RENAMING.value, JobStatus.UPLOADING.value],
    )
    return {k: int(v) for k, v in dict(totals).items()} if totals else {}
