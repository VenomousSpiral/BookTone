"""
Profile management for audiobook generation.

Handles CRUD operations for ebook/model/voice generation profiles.
Uses SQLite (via database module) instead of in-memory dict + JSON files.
Extracted from stream_audiobook_service.py to improve separation of concerns.
"""
import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from app.core.config import settings
from app.services.database import SCHEMA_SQL, get_connection, DB_PATH

logger = logging.getLogger(__name__)


# Alias for backward compat with any remaining _row_to_dict calls.
def _row_to_dict(row):
    return _profile_row_to_dict(row)


def _profile_row_to_dict(row) -> dict:
    """Convert a profiles sqlite3.Row to plain dict, adding 'model' alias for backward compat.

    The DB column is `model_name` but the old JSON format used key `model`,
    so callers expect profile["model"] instead of profile["model_name"].
    """
    if row is None:
        return None
    d = dict(row)
    # Backward-compatible alias.
    d.setdefault("model", d.get("model_name"))
    # Map progress_pct -> progress for backward compat (old JSON used "progress").
    d.setdefault("progress", d.get("progress_pct", 0.0))
    return d


class ProfileManager:
    """Manages audiobook generation profiles (ebook:model/voice combos)."""

    def __init__(self, profiles_file: Path = None, db_path: Path = None):
        # Accept both old `profiles_file` (maps to db_path) and new `db_path`
        if db_path is None:
            self.db_path = DB_PATH
            if profiles_file is not None:
                # Derive SQLite path from legacy JSON path for backward compat.
                pfile = Path(profiles_file)
                self.db_path = pfile.parent / (pfile.stem + ".db")
        else:
            self.db_path = db_path
        # Lazy init — connection opened on first use.
        self._conn = None  # type: Optional[object]

    @property
    def conn(self):
        if self._conn is None:
            import sqlite3 as _sqlite3
            conn = _sqlite3.connect(str(self.db_path))
            conn.row_factory = _sqlite3.Row
            # Ensure schema exists (for tests that pass custom db_path).
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            if not self._table_exists(conn, "profiles"):
                conn.executescript(SCHEMA_SQL)
            self._conn = conn
        return self._conn

    @staticmethod
    def _table_exists(conn, table_name: str) -> bool:
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND ?",
            (table_name,),
        )
        return len(cur.fetchall()) > 0

    def close(self):
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            finally:
                self._conn = None

    # ------------------------------------------------------------------ #
    #  Key helpers                                                        #
    # ------------------------------------------------------------------ #

    def _get_profile_key(self, ebook_path: str, model_name: str, voice: str) -> str:
        """Generate profile key: {ebook_path}:{model_name}:{voice}"""
        return f"{ebook_path}:{model_name}:{voice}"

    def _compute_ebook_hash(self, ebook_path: str) -> str:
        """Compute MD5 hash of ebook file for change detection."""
        try:
            full_path = Path(ebook_path)
            if not full_path.exists():
                full_path = settings.EBOOKS_DIR / ebook_path
            mtime = full_path.stat().st_mtime
            size = full_path.stat().st_size
            return hashlib.md5(f"{mtime}:{size}".encode()).hexdigest()[:12]
        except Exception:
            return "unknown"

    # ------------------------------------------------------------------ #
    #  CRUD                                                               #
    # ------------------------------------------------------------------ #

    def create_profile(
        self, ebook_path: str, model_name: str, voice: str,
        total_chunks: int = 0, chapters: list = None,
    ) -> dict:
        """Create a new profile for an ebook/model/voice combo."""
        now = datetime.now(timezone.utc).isoformat()
        title = Path(ebook_path).stem

        cur = self.conn.execute(
            """INSERT INTO profiles
               (ebook_path, model_name, voice, title, status, ebook_hash,
                total_chunks, completed_chunks, progress_pct, error, created_at, updated_at)
             VALUES (?, ?, ?, ?, 'not_started', ?, 0, 0, 0.0, NULL, ?, ?)""",
            (ebook_path, model_name, voice, title, self._compute_ebook_hash(ebook_path), now, now),
        )

        chapter_rows = chapters or []
        for ch in chapter_rows:
            self.conn.execute(
                """INSERT INTO chapters
                   (profile_id, name, start_idx, end_idx, start_chunk, end_chunk)
                 VALUES (?, ?, ?, ?, ?, ?)""",
                (cur.lastrowid, ch["name"], ch.get("start_idx", 0),
                 ch.get("end_idx", 0), ch.get("start_chunk", 0), ch.get("end_chunk", 0)),
            )

        self.conn.commit()
        return self.get_profile(ebook_path, model_name, voice) or {}

    def get_profile(self, ebook_path: str, model_name: str, voice: str) -> Optional[dict]:
        """Get a profile by key."""
        row = self.conn.execute(
            "SELECT * FROM profiles WHERE ebook_path=? AND model_name=? AND voice=?",
            (ebook_path, model_name, voice),
        ).fetchone()

        if not row:
            return None

        result = _row_to_dict(row)
        # Attach chapters.
        rows = self.conn.execute(
            "SELECT * FROM chapters WHERE profile_id=? ORDER BY start_idx ASC",
            (result["id"],),
        ).fetchall()
        result["chapters"] = [_row_to_dict(r) for r in rows]

        # Attach bookmarks with context='profile'.
        bm_rows = self.conn.execute(
            "SELECT chunk_index, text_preview FROM bookmarks WHERE ebook_path=? AND context='profile' ORDER BY chunk_index ASC",
            (ebook_path,),
        ).fetchall()
        result["bookmarks"] = [r["chunk_index"] for r in bm_rows]

        return result

    def update_profile_status(
        self, ebook_path: str, model_name: str, voice: str, status: str,
        completed_chunks: int = None, error: str = None, total_chunks: int = None,
    ):
        """Update profile status and related fields."""
        now = datetime.now(timezone.utc).isoformat()

        # Fetch current row to compute progress.
        cur_row = self.conn.execute(
            "SELECT * FROM profiles WHERE ebook_path=? AND model_name=? AND voice=?",
            (ebook_path, model_name, voice),
        ).fetchone()
        if not cur_row:
            return  # no-op

        cc = completed_chunks or cur_row["completed_chunks"]
        tc = total_chunks or cur_row["total_chunks"]
        progress_pct = round(cc / tc * 100, 1) if tc and tc > 0 else (cc * 1.0 if tc == 0 else 0.0)

        self.conn.execute(
            """UPDATE profiles SET status=?, updated_at=?, completed_chunks=?, total_chunks=?, progress_pct=? WHERE id=?""",
            (status, now, cc, tc or cur_row["total_chunks"], progress_pct, cur_row["id"]),
        )
        if error is not None:
            self.conn.execute(
                "UPDATE profiles SET error=? WHERE id=?",
                (error, cur_row["id"]),
            )
        self.conn.commit()

    def delete_profile(self, ebook_path: str, model_name: str, voice: str) -> bool:
        """Delete a profile."""
        row = self.conn.execute(
            "SELECT id FROM profiles WHERE ebook_path=? AND model_name=? AND voice=?",
            (ebook_path, model_name, voice),
        ).fetchone()
        if not row:
            return False

        self.conn.execute("DELETE FROM bookmarks WHERE ebook_path=?", (ebook_path,))
        self.conn.execute(
            "DELETE FROM profiles WHERE id=?", (row["id"],)
        )
        self.conn.commit()
        return True

    def update_profile_chapters(self, ebook_path: str, model_name: str, voice: str,
                                chapters: list, total_chunks: int):
        """Update profile with chapter and chunk info."""
        # Fetch existing row.
        cur_row = self.conn.execute(
            "SELECT id FROM profiles WHERE ebook_path=? AND model_name=? AND voice=?",
            (ebook_path, model_name, voice),
        ).fetchone()
        if not cur_row:
            return

        profile_id = cur_row["id"]

        # Replace chapters.
        self.conn.execute("DELETE FROM chapters WHERE profile_id=?", (profile_id,))
        for ch in chapters or []:
            self.conn.execute(
                """INSERT INTO chapters
                   (profile_id, name, start_idx, end_idx, start_chunk, end_chunk)
                 VALUES (?, ?, ?, ?, ?, ?)""",
                (profile_id, ch["name"], ch.get("start_idx", 0),
                 ch.get("end_idx", 0), ch.get("start_chunk", 0), ch.get("end_chunk", 0)),
            )

        self.conn.execute(
            "UPDATE profiles SET total_chunks=?, ebook_hash=?, updated_at=? WHERE id=?",
            (total_chunks, self._compute_ebook_hash(ebook_path),
             datetime.now(timezone.utc).isoformat(), profile_id),
        )
        self.conn.commit()

    def get_all_profiles(self) -> Dict[str, dict]:
        """Get all profiles keyed by {ebook}:{model}:{voice}."""
        rows = self.conn.execute(
            "SELECT * FROM profiles ORDER BY created_at ASC"
        ).fetchall()
        result = {}
        for row in rows:
            d = _row_to_dict(row)
            key = f"{d['ebook_path']}:{d['model_name']}:{d['voice']}"

            # Attach chapters.
            ch_rows = self.conn.execute(
                "SELECT * FROM chapters WHERE profile_id=? ORDER BY start_idx ASC",
                (d["id"],),
            ).fetchall()
            d["chapters"] = [_row_to_dict(r) for r in ch_rows]

            # Attach bookmarks.
            bm_rows = self.conn.execute(
                "SELECT chunk_index FROM bookmarks WHERE ebook_path=? AND context='profile' ORDER BY chunk_index ASC",
                (d["ebook_path"],),
            ).fetchall()
            d["bookmarks"] = [r["chunk_index"] for r in bm_rows]

            result[key] = d
        return result

    def get_profiles_for_ebook(self, ebook_path: str) -> Dict[str, dict]:
        """Get all profiles for a specific ebook."""
        rows = self.conn.execute(
            "SELECT * FROM profiles WHERE ebook_path=? ORDER BY created_at ASC",
            (ebook_path,),
        ).fetchall()
        result = {}
        for row in rows:
            d = _row_to_dict(row)
            key = f"{d['ebook_path']}:{d['model_name']}:{d['voice']}"

            ch_rows = self.conn.execute(
                "SELECT * FROM chapters WHERE profile_id=? ORDER BY start_idx ASC",
                (d["id"],),
            ).fetchall()
            d["chapters"] = [_row_to_dict(r) for r in ch_rows]

            bm_rows = self.conn.execute(
                "SELECT chunk_index FROM bookmarks WHERE ebook_path=? AND context='profile' ORDER BY chunk_index ASC",
                (d["ebook_path"],),
            ).fetchall()
            d["bookmarks"] = [r["chunk_index"] for r in bm_rows]

            result[key] = d
        return result
