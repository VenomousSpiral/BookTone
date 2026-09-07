"""SQLite-backed progress and bookmark persistence.

Extracted from ``stream_service.StreamService`` so the single schema definition in
``database.SCHEMA_SQL`` is the only DDL source and progress SQL is no longer
interleaved with streaming logic.
"""
import logging
import sqlite3
from datetime import datetime
from typing import Dict

from app.core.config import settings
from app.services.database import SCHEMA_SQL
from app.models.streaming import StreamProgress

logger = logging.getLogger(__name__)


class ProgressRepository:
    """Stores playback position and bookmarks in the profiles/bookmarks tables."""

    def _get_conn(self) -> sqlite3.Connection:
        """Fresh per-operation connection; resolves the DB path at call time so
        tests that repoint ``settings.STORAGE_DIR`` keep working."""
        db_path = settings.STORAGE_DIR / "app.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(SCHEMA_SQL)  # idempotent; single schema source
        return conn

    # ------------------------------------------------------------------ #
    #  Progress / bookmarks                                               #
    # ------------------------------------------------------------------ #

    def get_progress(self, ebook_path: str) -> StreamProgress:
        """Return a StreamProgress populated from the SQLite tables.

        Current chunk comes from ``profiles.last_position`` (primary source);
        bookmarks are collected but do NOT influence playback position.
        """
        conn = self._get_conn()
        try:
            current_chunk: int = 0
            row = conn.execute(
                "SELECT last_position FROM profiles WHERE ebook_path=?",
                (ebook_path,),
            ).fetchone()
            if row and row["last_position"] is not None:
                val = int(row["last_position"])
                if val > current_chunk:
                    current_chunk = val

            bm_rows = conn.execute(
                "SELECT chunk_index, text_preview FROM bookmarks WHERE ebook_path=? AND context='progress' ORDER BY chunk_index ASC",
                (ebook_path,),
            ).fetchall()

            bm_dict: Dict[str, str] = {}
            for row in bm_rows:
                ci = int(row["chunk_index"])
                bm_dict[str(ci)] = str(row["text_preview"]) or ""
        except Exception:
            pass  # keep defaults: current_chunk=0, bookmarks={}
        finally:
            conn.close()

        return StreamProgress(ebook_path=ebook_path, current_chunk=current_chunk, bookmarks=bm_dict)

    def update_progress(self, ebook_path: str, chunk_index: int):
        """Store the current playback position in profiles.last_position."""
        conn = self._get_conn()
        try:
            existing = conn.execute(
                "SELECT id FROM profiles WHERE ebook_path=?",
                (ebook_path,),
            ).fetchone()

            if existing:
                conn.execute(
                    "UPDATE profiles SET last_position=? WHERE ebook_path=?",
                    (chunk_index, ebook_path),
                )
            else:
                now_str = datetime.now().isoformat()
                try:
                    conn.execute(
                        "INSERT INTO profiles "
                        "(ebook_path, model_name, voice, status, last_position, created_at, updated_at) "
                        "VALUES (?, '', '', 'not_started', ?, ?, ?)",
                        (ebook_path, chunk_index, now_str, now_str),
                    )
                except Exception:  # simplified schema without timestamp columns
                    conn.execute(
                        "INSERT INTO profiles "
                        "(ebook_path, model_name, voice, status, last_position) "
                        "VALUES (?, '', '', 'not_started', ?)",
                        (ebook_path, chunk_index),
                    )
            conn.commit()
        except Exception as e:
            logger.error("[ERROR] Failed to update progress: %s", e)
        finally:
            conn.close()

    def toggle_bookmark(self, ebook_path: str, chunk_index: int, text_preview: str = "") -> bool:
        conn = self._get_conn()
        try:
            existing = conn.execute(
                "SELECT 1 FROM bookmarks WHERE ebook_path=? AND context='progress' AND chunk_index=?",
                (ebook_path, chunk_index),
            ).fetchone()

            if existing:
                conn.execute(
                    "DELETE FROM bookmarks WHERE ebook_path=? AND context='progress' AND chunk_index=?",
                    (ebook_path, chunk_index),
                )
                conn.commit()
                return False  # removed
            else:
                conn.execute(
                    "INSERT OR IGNORE INTO bookmarks (ebook_path, context, chunk_index, text_preview) VALUES (?, 'progress', ?, ?)",
                    (ebook_path, chunk_index, text_preview or ""),
                )
                conn.commit()
                return True  # added
        except Exception as e:
            logger.error("[ERROR] Failed to toggle bookmark: %s", e)
            raise
        finally:
            conn.close()

    def clear_progress(self, ebook_path: str):
        conn = self._get_conn()
        try:
            conn.execute(
                "DELETE FROM profiles WHERE ebook_path=?",
                (ebook_path,),
            )
            conn.execute(
                "DELETE FROM bookmarks WHERE ebook_path=? AND context='progress'",
                (ebook_path,),
            )
            conn.commit()
        except Exception as e:
            logger.error("[ERROR] Failed to clear progress: %s", e)
        finally:
            conn.close()

    def rename_progress(self, old_path: str, new_path: str):
        """Migrate progress/bookmarks from old path to new path."""
        if old_path == new_path:
            return
        conn = self._get_conn()
        try:
            conn.execute(
                "UPDATE bookmarks SET ebook_path=? WHERE ebook_path=?",
                (new_path, old_path),
            )
            conn.execute(
                "UPDATE profiles SET ebook_path=? WHERE ebook_path=?",
                (new_path, old_path),
            )
            conn.commit()
            logger.info("[PROGRESS] Migrated progress for %s -> %s", old_path, new_path)
        except Exception as e:
            logger.error("[ERROR] Failed to rename progress: %s", e)
            conn.rollback()
        finally:
            conn.close()

    def rename_progress_recursive(self, source_path_str: str, dest_dir: str) -> int:
        """Migrate bookmarks + reading position for all ebooks under *source_path_str*.

        Uses SQL so no filesystem walk is required. Returns the number of migrated
        ebook paths.
        """
        if source_path_str == dest_dir:
            return 0

        old_prefix = f"{source_path_str}/"
        new_base = dest_dir.rstrip('/')
        conn = self._get_conn()
        migrated = 0
        try:
            rows = list(conn.execute(
                "SELECT DISTINCT ebook_path FROM bookmarks WHERE ebook_path LIKE ?"
                    " UNION SELECT DISTINCT ebook_path FROM profiles "
                    "WHERE ebook_path LIKE ?",
                (f"{old_prefix}%", f"{old_prefix}%")
            ).fetchall())

            if not rows:
                return 0

            all_paths = [str(r["ebook_path"]) for r in rows]
            replacements: Dict[str, str] = {}
            for old_ebook in all_paths:
                remaining = old_ebook[len(old_prefix):]
                new_ebook = f"{new_base}/{remaining}" if new_base else remaining
                replacements[old_ebook] = new_ebook

            for old_ebook, new_ebook in replacements.items():
                conn.execute(
                    "UPDATE bookmarks SET ebook_path=? WHERE ebook_path=? AND context='progress'",
                    (new_ebook, old_ebook),
                )
                conn.execute(
                    "UPDATE profiles SET ebook_path=? WHERE ebook_path=?",
                    (new_ebook, old_ebook),
                )

            migrated = len(replacements)
            conn.commit()
        except Exception as e:
            logger.error("[ERROR] Failed to rename progress recursively: %s", e)
            conn.rollback()
        finally:
            conn.close()
        return migrated
