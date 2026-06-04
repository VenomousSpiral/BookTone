"""One-shot migration: JSON file storage → SQLite database.

Called once at app startup after schema init (main.py lifespan).  If a previous
run already succeeded, this function is a no-op.
"""
import json
import logging
from datetime import datetime
from pathlib import Path

from app.core.config import settings
from app.services.database import get_connection, DB_PATH

logger = logging.getLogger(__name__)


def _already_migrated(conn) -> bool:
    """Check whether migration has already been performed."""
    try:
        cur = conn.execute("SELECT COUNT(*) AS cnt FROM migration_log")
        return cur.fetchone()["cnt"] > 0
    except Exception:
        # Table doesn't exist — not migrated yet.
        return False


def migrate(db_path=None):
    """Run one-time JSON → SQLite migration. Idempotent (guarded by migration_log).

    Creates the schema first, then checks for prior runs.
    """
    conn = get_connection(db_path)

    if _already_migrated(conn):
        logger.info("[MIGRATE] Already migrated — skipping")
        return

    # Ensure all tables exist before inserting data.
    from app.services.database import SCHEMA_SQL
    try:
        conn.executescript(SCHEMA_SQL)
    except Exception as e:
        logger.error("[MIGRATE] Failed to create schema: %s", e)
        raise

    # Re-check after creating tables (in case another process created them).
    if _already_migrated(conn):
        return

    try:
        # ---- 1. Migrate profiles from audiobooks_db.json --------
        raw_profiles = {}
        pfile = settings.STORAGE_DIR / "audiobooks_db.json"
        if pfile.exists():
            with open(pfile, "r") as f:
                try:
                    raw_profiles = json.load(f)
                except json.JSONDecodeError:
                    logger.error("[MIGRATE] audiobooks_db.json is corrupt — skipping profiles")

        for key, data in (raw_profiles or {}).items():
            ebook_path = data.get("ebook_path", "")
            model_name = data.get("model", "unknown")
            voice = data.get("voice", "unknown")
            title = data.get("title", Path(ebook_path).stem) if ebook_path else None

            now_created = data.get("created_at") or datetime.now().isoformat()
            now_updated = data.get("updated_at") or datetime.now().isoformat()

            cur = conn.execute(
                """INSERT OR IGNORE INTO profiles
                   (ebook_path, model_name, voice, title, status, ebook_hash,
                    total_chunks, completed_chunks, progress_pct, error,
                    created_at, updated_at)
                 VALUES (?, ?, ?, ?, 'not_started', ?, 0, 0, 0.0, NULL, ?, ?)""",
                (ebook_path, model_name, voice, title or "",
                 data.get("ebook_hash"), now_created, now_updated),
            )

            row = conn.execute(
                "SELECT id FROM profiles WHERE ebook_path=? AND model_name=? AND voice=?",
                (ebook_path, model_name, voice),
            ).fetchone()
            if not row:
                logger.warning("[MIGRATE] Could not find profile for %s:%s",
                               ebook_path, key)
                continue
            profile_id = row["id"]

            # Migrate chapters.
            for ch in data.get("chapters") or []:
                conn.execute(
                    """INSERT INTO chapters
                       (profile_id, name, start_idx, end_idx, start_chunk, end_chunk)
                     VALUES (?, ?, ?, ?, ?, ?)""",
                    (profile_id, ch["name"], ch.get("start_idx", 0),
                     ch.get("end_idx", 0), ch.get("start_chunk", 0),
                     ch.get("end_chunk", 0)),
                )

            # Migrate bookmarks from profile's "bookmarks" list.
            for bm in data.get("bookmarks") or []:
                conn.execute(
                    """INSERT OR IGNORE INTO bookmarks (ebook_path, context, chunk_index, text_preview)
                       VALUES (?, 'profile', ?, '')""",
                    (ebook_path, bm),
                )

            # Update status/completed_chunks/progress.
            conn.execute(
                """UPDATE profiles SET
                   status=?, total_chunks=?, completed_chunks=?, progress_pct=?, error=?
                 WHERE id=?""",
                (data.get("status") or "not_started",
                 data.get("total_chunks", 0),
                 data.get("completed_chunks", 0),
                 float(data.get("progress", 0.0)),
                 data.get("error"),
                 profile_id),
            )

        # ---- 2. Migrate stream_progress.json → bookmarks table --
        raw_progress = {}
        progress_file = settings.STORAGE_DIR / "stream_progress.json"
        if progress_file.exists():
            with open(progress_file, "r") as f:
                try:
                    raw_progress = json.load(f)
                except json.JSONDecodeError:
                    logger.error("[MIGRATE] stream_progress.json is corrupt — skipping progress migration")

        for ebook_path, prog_data in (raw_progress or {}).items():
            bms = prog_data.get("bookmarks", {})
            if isinstance(bms, dict):
                for chunk_str, text_preview in bms.items():
                    try:
                        ci = int(chunk_str)
                    except (ValueError, TypeError):
                        continue
                    conn.execute(
                        """INSERT OR IGNORE INTO bookmarks (ebook_path, context, chunk_index, text_preview)
                           VALUES (?, 'progress', ?, ?)""",
                        (ebook_path, ci, str(text_preview)),
                    )

        # ---- 3. Migrate stream_settings.json → settings_kv -----
        raw_settings = {}
        ss_file = settings.STORAGE_DIR / "stream_settings.json"
        if ss_file.exists():
            with open(ss_file, "r") as f:
                try:
                    raw_settings = json.load(f)
                except json.JSONDecodeError:
                    logger.error("[MIGRATE] stream_settings.json is corrupt — skipping settings migration")

        # ---- 4. Migrate user_preferences.json → settings_kv ---
        raw_prefs = {}
        up_file = settings.STORAGE_DIR / "user_preferences.json"
        if up_file.exists():
            with open(up_file, "r") as f:
                try:
                    raw_prefs = json.load(f)
                except json.JSONDecodeError:
                    logger.error("[MIGRATE] user_preferences.json is corrupt — skipping preferences migration")

        # Merge settings + prefs (user_preferences take priority for same keys).
        merged_settings = dict(raw_settings or {})
        if raw_prefs:
            for k, v in raw_prefs.items():
                if isinstance(v, dict):
                    merged_settings[k] = json.dumps(v)  # flatten nested dicts as JSON strings
                else:
                    merged_settings[k] = v

        for key, value in (merged_settings or {}).items():
            conn.execute(
                "INSERT OR REPLACE INTO settings_kv (key, value_json) VALUES (?, ?)",
                (str(key), json.dumps(value)),
            )

        # ---- 5. Log migration ----------------------------------
        details = {
            "profiles_migrated": len(raw_profiles or {}),
            "settings_entries": len(merged_settings or {}),
        }
        conn.execute(
            """INSERT INTO migration_log (from_version, to_version, migrated_at, details_json)
               VALUES ('json', 'sqlite_v1', ?, ?)""",
            (datetime.now().isoformat(), json.dumps(details)),
        )

        conn.commit()
        logger.info("[MIGRATE] Migration complete: %s", details)

    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        logger.error("[MIGRATE] Migration failed, rolled back: %s", e)
        raise


def migrate_if_needed(db_path=None):
    """Public entry point — checks migration_log before doing anything."""
    if db_path is None:
        conn = get_connection()
    else:
        conn = get_connection(db_path)
    try:
        if not _already_migrated(conn):
            migrate(db_path)
    finally:
        conn.close()
