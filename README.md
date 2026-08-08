# 🎬 File Renamer Bot

A production-ready **Telegram File Renamer Bot** built with [Pyrogram](https://docs.pyrogram.org/),
async PostgreSQL (`asyncpg`) and Redis. Designed from the ground up for a
**Render free web service (~512 MB RAM)**: low memory, streaming file I/O,
bounded concurrency and full crash recovery.

It renames **ordinary single files only** — text, code, config and
document-like files (`.txt`, `.py`, `.js`, `.json`, `.csv`, `.md`, `.html`,
`.yaml`, …). It **never** accepts photos, videos, audio or archives.

---

## ✨ Features

- ✅ **Normal rename** that always preserves the original extension
  (`test.py` + `hello.txt` → `hello.py`)
- 🔄 **Extension change** as a separate, validated operation
- 📦 **Batch mode** for multiple files (prefix, suffix, find/replace,
  numbering with zero padding, whitespace cleanup, case conversion)
- 💾 **PostgreSQL** persistence: users, jobs, history, settings, admin config
- 🧠 **Redis** for the queue, rate limits, locks, active-job counters and
  short-lived state (never permanent, never stores file bytes)
- 🧵 **Real background worker** with a bounded queue and concurrency caps
- 🚦 **Rate limiting** per user, per action
- 📜 **Paginated history** and an **admin panel** (users / jobs / failed /
  stats) with ownership and authorization checks on every callback
- 🧹 **Crash recovery**: stale jobs are detected on restart, temp directories
  wiped, interrupted jobs marked failed
- 🩺 **Minimal `/health` HTTP server** (stdlib only) for Render / UptimeRobot —
  returns plain text `OK` with HTTP 200 and is independent of Telegram,
  PostgreSQL, Redis and the workers (binds `0.0.0.0:$PORT`)
- 🛡️ **Memory-safe**: files are streamed to disk; never `read()` into RAM,
  no `BytesIO`, bounded worker count, retries with backoff

---

## 📁 Project tree

```
FILE-RENAME-BOT/
├── main.py                     # entrypoint – wires everything together
├── config.py                   # env-based configuration (fails fast)
├── requirements.txt
├── Dockerfile
├── render.yaml
├── Procfile
├── .env.example
├── README.md
├── bot/
│   ├── handlers/
│   │   ├── common.py           # shared handler context
│   │   ├── commands.py         # /start /help /cancel /history /settings /admin
│   │   ├── files.py            # incoming document validation + job creation
│   │   └── text_input.py       # rename / extension / batch text input
│   ├── callbacks/callbacks.py  # all inline-button routing
│   ├── keyboards/keyboards.py  # inline keyboards
│   └── messages/__init__.py    # all user-facing text (small-caps UI)
├── core/
│   ├── filename.py             # pure filename sanitising / transforms
│   ├── validation.py           # type/size validation (media type+ext+MIME)
│   ├── rename.py               # rename plans + batch planners
│   └── media.py                # Pyrogram message → MediaInfo
├── database/
│   ├── database.py             # asyncpg pool + schema init
│   ├── models.py               # JobStatus + idempotent DDL
│   └── queries.py              # all SQL
├── services/
│   ├── state.py                # Redis store (queue/locks/rate/cancel/state)
│   ├── jobs.py                 # admission control + slot management + cancel
│   ├── storage.py              # temp dirs, disk checks, filesystem rename
│   ├── cleanup.py              # stale-job + temp-dir recovery
│   ├── rate_limit.py           # rate-limit facade
│   └── health.py               # stdlib /health server
├── workers/processor.py        # download → rename → upload → cleanup loop
├── migrations/001_init.sql     # reference schema (also applied at startup)
└── tests/test_core.py          # core unit tests (no Telegram/DB needed)
```

---

## ⚙️ Configuration

All configuration comes from environment variables. The bot **refuses to
start** with a clear error if a required variable is missing. Credentials are
never logged.

### Required

| Variable       | Description                                              |
|----------------|----------------------------------------------------------|
| `API_ID`       | Telegram API ID from https://my.telegram.org             |
| `API_HASH`     | Telegram API hash                                        |
| `BOT_TOKEN`    | Bot token from [@BotFather](https://t.me/BotFather)      |
| `DATABASE_URL` | PostgreSQL connection string                             |
| `REDIS_URL`    | Redis / Render Key Value connection string               |
| `ADMIN_IDS`    | Comma-separated numeric Telegram admin IDs               |

### Optional (defaults tuned for 512 MB)

| Variable                  | Default | Meaning                                  |
|---------------------------|---------|------------------------------------------|
| `MAX_FILE_SIZE_MB`        | `25`    | Largest accepted file (hard cap 100 MB)  |
| `MAX_GLOBAL_ACTIVE_JOBS`  | `2`     | Max concurrently processing jobs         |
| `MAX_ACTIVE_JOBS_PER_USER`| `1`     | Max concurrent jobs per user             |
| `MAX_QUEUE_SIZE`          | `20`    | Pending jobs before queue-full message   |
| `MAX_RETRIES`             | `3`     | Retries for transient errors             |
| `JOB_TIMEOUT`             | `300`   | Seconds before a job is considered stale |
| `HEALTH_HOST` / `HEALTH_PORT` | `0.0.0.0` / `8080` | Health server bind (Render sets `PORT`, used automatically) |
| `TEMP_DIR`                | `/tmp/file-renamer` | Per-job temp directory root     |
| `START_VIDEO_URL`         | `https://files.catbox.moe/5qz09e.mp4` | Start video (overridable) |
| `RATE_LIMIT`              | `60`    | Rate-limit window (seconds)              |
| `RATE_LIMIT_*`            | see code| Per-action overrides                     |

---

## 🗄️ Database schema

PostgreSQL is the source of truth. Tables are created idempotently at startup
with `CREATE TABLE IF NOT EXISTS` and proper indexes. **Production tables are
never dropped automatically.**

- `users(user_id, username, first_name, is_banned, is_admin, timestamps)`
- `user_settings(user_id, case_mode, ws_mode, num_mode)`
- `jobs(id uuid, user_id, batch_id, status, operation, original_name,
  new_name, file_size, file_id, file_ref, chat_id, message ids, error,
  attempts, timestamps)`
- `history(user_id, job_id, operation, original_name, new_name, file_size,
  status, created_at)`
- `admin_config(key, value)`
- `schema_version(version)`

See `migrations/001_init.sql` for the full DDL.

Job states: `PENDING → QUEUED → DOWNLOADING → RENAMING → UPLOADING →
CLEANING → COMPLETED` (or `FAILED` / `CANCELLED`).

---

## 🚀 Local run

```bash
# 1. Python 3.10+
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. Provide credentials
cp .env.example .env
# edit .env

# 3. Run
python main.py
```

Run the unit tests:

```bash
python tests/test_core.py
# or
python -m pytest tests/ -q
```

The health server listens on `http://0.0.0.0:${PORT}/health` (Render injects
`PORT`; locally it defaults to `8080`). It returns a plain `200 OK` / `OK`
body and performs **no** dependency checks so it stays extremely cheap.

---

## ☁️ Render deployment

### Option A — Blueprint (recommended)

1. Push this repo to GitHub.
2. In Render: **New → Blueprint**, select the repo.
3. Render reads `render.yaml` and creates:
   - a **Web Service** (`file-renamer-bot`, Docker, free plan)
   - a **PostgreSQL** database (`file-renamer-db`, free)
   - a **Key Value / Redis** (`file-renamer-redis`, free)
4. Set the secret env vars when prompted: `API_ID`, `API_HASH`,
   `BOT_TOKEN`, `ADMIN_IDS`. (Optional `START_VIDEO_URL`.)
5. Deploy. Render health-checks `/health` automatically.

### Option B — Manual

1. Create a free PostgreSQL and a free Key Value (Redis) in Render.
2. Create a **Web Service** pointing at the repo, using the **Docker**
   runtime (or the Python starter with `pip install -r requirements.txt`
   and start command `python main.py`).
3. Add the environment variables above, wiring `DATABASE_URL` and
   `REDIS_URL` from the created datastores.
4. Set the health check path to `/health`.

No persistent disk is required. All temporary files live under `/tmp` and are
deleted after each job (and wiped on startup).

---

## 🏗️ Architecture explanation

```
Telegram update
      │
      ▼
 Handlers (commands / files / text / callbacks)
      │   validate size+type BEFORE download · rate limit · admission check
      ▼
 PostgreSQL  ◄── create job (PENDING) ──►  Redis user state
      │
      ▼ (user supplies new name)
 set_job_plan (QUEUED)  ──►  Redis work queue
                                  │
                                  ▼
                     JobProcessor (single consumer loop)
                                  │  acquire global + per-user slots
                                  ▼
                     DOWNLOAD (streamed to /tmp/.../<job>/)
                                  ▼
                     verify file on disk
                                  ▼
                     FILESYSTEM RENAME (preserve/change ext)
                                  ▼
                     UPLOAD (streamed from disk)
                                  ▼
                     CLEANUP (rm -rf job dir) in finally
                                  ▼
                     mark COMPLETED/FAILED/CANCELLED + history
```

- **Memory:** Pyrogram streams downloads/uploads to/from a path; the bot never
  holds a whole file in RAM. The worker pool is bounded
  (`MAX_GLOBAL_ACTIVE_JOBS=2`, per-user `1`) and the asyncpg/Redis pools are
  small.
- **PostgreSQL** is durable state (jobs, users, history). **Redis** is
  ephemeral (queue, counters, locks, cancellation tokens). If Redis is wiped,
  the bot stays correct: admission falls back to DB counts, and active jobs
  are recoverable.
- **Crash safety:** every job runs in a `try/finally`; unexpected exceptions
  are recorded, not propagated. On startup, `cleanup.py` removes stale temp
  directories and marks jobs stuck in active states as `FAILED`.
- **Retries:** only transient errors (network, timeout, `FloodWait`) are
  retried up to `MAX_RETRIES` with exponential backoff. Invalid files,
  unsupported types, bad names and cancellations fail immediately.

---

## ✅ Test checklist

Core unit tests in `tests/test_core.py` cover:

- [x] `.txt`, `.py`, `.json`, `.csv`, `.md` accepted
- [x] `hello.txt`, `hello world.txt`, `my.file.py`, `test_01.json`
- [x] Unicode / parentheses / underscores / hyphens
- [x] Reject `.jpg .png .mp4 .mkv .mp3` (media)
- [x] Reject `.zip .rar .7z .tar .tar.gz` (archives)
- [x] Reject `>25 MB` (before any download)
- [x] Rename-extension attempt: `test.py` + `hello.txt` → `hello.py`
- [x] Extension change: `test.txt` → `test.md`
- [x] Find/replace, prefix, suffix, remove prefix/suffix, whitespace, case
- [x] Sequential numbering with zero padding
- [x] Path-traversal / control-character sanitisation

Runtime / integration behaviors wired and exercised by the running system:
cancel, close, two simultaneous users (global slots), same user submitting
multiple files (per-user slot + queue), worker failure isolation, DB
reconnect, Redis unavailable (graceful degradation), Render restart / stale
job recovery, pagination, rate limiting and malformed callbacks.

---

## 🔒 Safety notes

- Only ordinary **documents** are processed. Photos/videos/audio/animations
  sent compressed are rejected by Telegram media type before any download.
- Extension change cannot turn a text file into a media/archive filename.
- Filenames are sanitised (no path separators, control chars, reserved names)
  and each job runs inside a UUID directory that cannot escape the temp root.
- Every admin callback re-checks `ADMIN_IDS`; every job action verifies
  ownership.

## 📜 License

See [LICENSE](LICENSE).
