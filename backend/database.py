import aiosqlite
from backend.config import settings

DB_PATH = settings.db_path

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,
    platform    TEXT NOT NULL,
    cookies_enc TEXT NOT NULL,
    user_agent  TEXT NOT NULL DEFAULT '',
    username    TEXT,
    valid       INTEGER DEFAULT 1,
    created_at  TEXT DEFAULT (datetime('now')),
    updated_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS tasks (
    id          TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL REFERENCES sessions(id),
    platform    TEXT NOT NULL,
    target_type TEXT NOT NULL,
    status      TEXT DEFAULT 'pending',
    total_items INTEGER DEFAULT 0,
    deleted     INTEGER DEFAULT 0,
    failed      INTEGER DEFAULT 0,
    created_at  TEXT DEFAULT (datetime('now')),
    updated_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS items (
    id          TEXT PRIMARY KEY,
    task_id     TEXT NOT NULL REFERENCES tasks(id),
    platform_id TEXT NOT NULL,
    item_type   TEXT NOT NULL,
    metadata    TEXT,
    status      TEXT DEFAULT 'pending',
    attempts    INTEGER DEFAULT 0,
    last_error  TEXT,
    deleted_at  TEXT,
    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id    TEXT NOT NULL REFERENCES tasks(id),
    event_type TEXT NOT NULL,
    payload    TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS daily_actions (
    session_id TEXT NOT NULL REFERENCES sessions(id),
    action_date TEXT NOT NULL,
    action_count INTEGER DEFAULT 0,
    PRIMARY KEY (session_id, action_date)
);
"""


async def get_db() -> aiosqlite.Connection:
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")
    return db


async def init_db():
    db = await get_db()
    try:
        await db.executescript(SCHEMA)
        await db.commit()
    finally:
        await db.close()
