-- 001_init.sql
-- Initial schema for the File Renamer Bot.
-- This file is provided for reference / manual provisioning; the application
-- also runs this DDL idempotently at startup (CREATE TABLE IF NOT EXISTS).
-- Tables are NEVER dropped automatically.

CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER PRIMARY KEY,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS users (
    user_id         BIGINT PRIMARY KEY,
    username        TEXT,
    first_name      TEXT,
    is_banned       BOOLEAN NOT NULL DEFAULT FALSE,
    is_admin        BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS user_settings (
    user_id         BIGINT PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
    case_mode       TEXT NOT NULL DEFAULT 'none',
    ws_mode         TEXT NOT NULL DEFAULT 'off',
    num_mode        TEXT NOT NULL DEFAULT '2',
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS jobs (
    id              UUID PRIMARY KEY,
    user_id         BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    batch_id        UUID,
    status          TEXT NOT NULL DEFAULT 'PENDING',
    operation       TEXT NOT NULL DEFAULT 'rename',
    original_name   TEXT NOT NULL,
    new_name        TEXT NOT NULL DEFAULT '',
    file_size       BIGINT NOT NULL DEFAULT 0,
    file_id         TEXT NOT NULL DEFAULT '',
    file_ref        TEXT,
    chat_id         BIGINT NOT NULL,
    request_msg_id  BIGINT,
    status_msg_id   BIGINT,
    result_msg_id   BIGINT,
    error           TEXT,
    attempts        INTEGER NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at      TIMESTAMPTZ,
    completed_at    TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_jobs_user_created     ON jobs(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_status           ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_batch            ON jobs(batch_id);
CREATE INDEX IF NOT EXISTS idx_jobs_user_status      ON jobs(user_id, status);

CREATE TABLE IF NOT EXISTS history (
    id              BIGSERIAL PRIMARY KEY,
    user_id         BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    job_id          UUID REFERENCES jobs(id) ON DELETE SET NULL,
    operation       TEXT NOT NULL,
    original_name   TEXT NOT NULL,
    new_name        TEXT NOT NULL,
    file_size       BIGINT NOT NULL DEFAULT 0,
    status          TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_history_user_time ON history(user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS admin_config (
    key             TEXT PRIMARY KEY,
    value           TEXT NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO schema_version(version)
VALUES (1)
ON CONFLICT (version) DO NOTHING;
