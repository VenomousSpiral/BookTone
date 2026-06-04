"""
Settings service for streaming preferences.

Handles loading/saving user settings (model, voice, display options).
Uses SQLite key-value store instead of JSON files.
Extracted from stream_service.py to reduce its size.
"""
import json
import logging
from pathlib import Path
from typing import Dict
import sqlite3 as _sqlite3

from app.core.config import settings
from app.services.database import get_connection, DB_PATH

logger = logging.getLogger(__name__)

DEFAULT_SETTINGS: Dict = {
    "font_size": 16,
    "font_family": "system",
    "preferred_model": None,
    "preferred_voice": None,
    "progress_mode": "book",
    "time_mode": "total",
    "show_title": True,
    "show_progress_bar": True,
    "show_images": False,
    "save_stream_audio": False,
    "sleep_timer_minutes": 0,
    "show_sleep_timer": False,
}


def _decode_value(raw) -> object:
    """Decode a database row value into the correct Python type.

    Handles values that may have been stored via different paths:
      1. Normal save path (save_settings): JSON-encoded strings → json.loads()
         correctly decodes dicts, lists, ints, bools, etc.
      2. Migration script: also JSON-encoded strings (json.dumps on all values)
         → same as #1
      3. Direct SQL writes or raw inserts: may be int/float/bool directly in SQLite
         → already decoded; do NOT call json.loads() on non-strings.
      4. Corrupted double-encoded entries from old code paths:
         e.g. dict was pre-saved as JSON string then saved_settings() encoded again.
         Detected by checking if first decode yields a string starting with { or [,
         and decoding once more to recover the original structure.
    """
    # Already a Python primitive from direct SQL — use directly (safe fallback).
    if not isinstance(raw, str):
        return raw

    try:
        first = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as e:
        logger.warning("[SETTINGS] Could not parse value: %s", e)
        # Return the original string — better than crashing and losing all settings.
        return raw

    # Check for double-encoding from old buggy code paths.
    # If first decode yields a string starting with { or [, it was likely encoded twice:
    #   save_preferences json.dumps(dict) → "{'key': 'val'}"
    #   save_settings json.dumps("{...}")  -> '"{""'key'':'' val''}""'
    if isinstance(first, str) and len(first) >= 1 and first[0] in ('{', '['):
        try:
            return json.loads(first)
        except (json.JSONDecodeError, TypeError):
            # Not actually double-encoded; treat as a plain string value.
            pass
    
    return first


class SettingsService:
    """Manages streaming settings persistence via SQLite key-value store.

    Uses per-operation connections to be thread-safe (FastAPI test client runs
    on different threads than the service init).
    """

    def __init__(self, settings_file: Path = None, db_path: Path = None):
        if db_path is None:
            self.db_path = DB_PATH
            if settings_file is not None:
                sfile = Path(settings_file)
                self.db_path = sfile.parent / (sfile.stem + ".db")
        else:
            self.db_path = db_path

    def _get_conn(self):
        """Get a fresh per-operation connection, auto-creating tables."""
        import os as _os
        # Ensure parent dir exists.
        _db = Path(str(self.db_path))
        _db.parent.mkdir(parents=True, exist_ok=True)
        conn = _sqlite3.connect(str(_db))
        conn.row_factory = _sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        # Auto-create settings_kv table if missing.
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS settings_kv "
                "(key TEXT PRIMARY KEY, value_json TEXT NOT NULL)"
            )
        except Exception:
            pass  # ignore errors — schema may already exist
        return conn

    def load_settings(self) -> Dict:
        """Load settings from SQLite key-value store, falling back to defaults.

        Handles values stored as JSON-encoded strings (normal + migration path)
        and raw Python primitives (direct SQL writes). See _decode_value().

        Individual corrupt entries are logged and skipped — one bad row never
        wipes out all other settings.
        """
        result = dict(DEFAULT_SETTINGS)  # start with defaults
        try:
            conn = self._get_conn()
            rows = conn.execute("SELECT key, value_json FROM settings_kv").fetchall()
            for row in rows:
                k = str(row["key"])
                v = _decode_value(row["value_json"])
                result[k] = v
        except Exception as e:
            logger.error("[SETTINGS] Failed to load from DB: %s", e)
            # Connection-level failure — return defaults only.
            return dict(DEFAULT_SETTINGS)
        
        return result

    def save_settings(self, settings_data: Dict) -> None:
        """Save all settings as key-value pairs."""
        try:
            conn = self._get_conn()
            for k, v in (settings_data or {}).items():
                conn.execute(
                    "INSERT OR REPLACE INTO settings_kv (key, value_json) VALUES (?, ?)",
                    (str(k), json.dumps(v)),
                )
            conn.commit()
        except Exception as e:
            logger.error("[SETTINGS] Failed to save: %s", e)
            raise

    def save_setting(self, key: str, value) -> None:
        """Save a single setting."""
        try:
            conn = self._get_conn()
            conn.execute(
                "INSERT OR REPLACE INTO settings_kv (key, value_json) VALUES (?, ?)",
                (str(key), json.dumps(value)),
            )
            conn.commit()
        except Exception as e:
            logger.error("[SETTINGS] Failed to save %s: %s", key, e)

    def get_setting(self, key: str):
        """Get a single setting value."""
        try:
            conn = self._get_conn()
            row = conn.execute(
                "SELECT value_json FROM settings_kv WHERE key=?", (str(key),)
            ).fetchone()
            if row:
                return json.loads(str(row["value_json"]))
        except Exception as e:
            logger.error("[SETTINGS] Failed to get %s: %s", key, e)
        return DEFAULT_SETTINGS.get(key)


def save_preferences(preferences_data: Dict) -> None:
    """Convenience function for the preferences route — merges into settings_kv.

    Simply passes through to save_settings() which handles all JSON encoding via
    json.dumps(). No pre-encoding needed — save_settings wraps every value.
    """
    svc = SettingsService()
    all_settings = svc.load_settings()
    for k, v in (preferences_data or {}).items():
        # Just pass through as-is; save_settings() calls json.dumps(v) on everything
        try:
            _test_dump = json.dumps(v)
        except TypeError:
            logger.warning("[SETTINGS] Could not encode value for key '%s', skipping", k)
            continue
        all_settings[k] = v  # raw Python object; save_settings handles JSON encoding
    svc.save_settings(all_settings)


def get_preferences() -> Dict:
    """Convenience function for the preferences route."""
    return SettingsService().load_settings()
