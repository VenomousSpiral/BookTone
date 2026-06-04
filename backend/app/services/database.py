"""SQLite database connection and schema management."""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import sqlite3
from app.core.config import settings

logger = logging.getLogger(__name__)

DB_PATH: Path = settings.STORAGE_DIR / "app.db"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS profiles (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ebook_path      TEXT NOT NULL,
    model_name      TEXT NOT NULL,
    voice           TEXT NOT NULL,
    title           TEXT,
    status          TEXT NOT NULL DEFAULT 'not_started',
    last_position   INTEGER DEFAULT 0,
    ebook_hash      TEXT(12),
    total_chunks    INTEGER NOT NULL DEFAULT 0,
    completed_chunks INTEGER NOT NULL DEFAULT 0,
    progress_pct    REAL NOT NULL DEFAULT 0.0,
    error           TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_profiles_unique
    ON profiles(ebook_path, model_name, voice);
CREATE INDEX IF NOT EXISTS idx_profiles_status
    ON profiles(status);
CREATE INDEX IF NOT EXISTS idx_profiles_ebook
    ON profiles(ebook_path);

-- Chapters separated so they can be updated without rewriting the profile row.
CREATE TABLE IF NOT EXISTS chapters (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id  INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    start_idx   INTEGER NOT NULL,
    end_idx     INTEGER NOT NULL,
    start_chunk INTEGER NOT NULL,
    end_chunk   INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_chapters_profile ON chapters(profile_id);

-- Unified bookmarks table. Replaces embedded dicts in audiobooks_db.json and
-- stream_progress.json.  'context' distinguishes generation-profile bookmarks
-- from streaming-playback bookmarks.
CREATE TABLE IF NOT EXISTS bookmarks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ebook_path    TEXT NOT NULL,
    context       TEXT NOT NULL DEFAULT 'progress',   -- 'progress' | 'profile'
    chunk_index   INTEGER NOT NULL,
    text_preview  TEXT DEFAULT ''
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_bookmarks_unique
    ON bookmarks(ebook_path, context, chunk_index);
CREATE INDEX IF NOT EXISTS idx_bookmarks_ebook
    ON bookmarks(ebook_path);

-- Key-value store for all user settings (replaces stream_settings.json +
-- user_preferences.json).  Values are JSON-encoded so any Python type works.
CREATE TABLE IF NOT EXISTS settings_kv (
    key         TEXT PRIMARY KEY,
    value_json  TEXT NOT NULL
);

-- Audit trail: prevents double-migration and allows rollback identification.
CREATE TABLE IF NOT EXISTS migration_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    from_version    TEXT NOT NULL,
    to_version      TEXT NOT NULL,
    migrated_at     TEXT NOT NULL,
    details_json    TEXT
);

-- Playback position lives in the profile row for simplicity: each ebook+model+voice
-- combo has exactly one profile.
"""


def _get_connection(db_path: Path = DB_PATH) -> sqlite3.Connection:
    """Get a new SQLite connection with WAL mode and foreign keys enabled."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row  # dict-like row access by default
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """Create tables if they don't exist. Called at app startup."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = _get_connection()
    try:
        conn.executescript(SCHEMA_SQL)
        conn.commit()
        logger.info("[DB] Schema initialized at %s", DB_PATH)
    except Exception as e:
        logger.error("[DB] Failed to initialize schema: %s", e)
        raise
    finally:
        conn.close()


def get_connection(db_path: Path = None):
    """Public connection factory (used by migration and tests)."""
    return _get_connection(db_path or DB_PATH)
