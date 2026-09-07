"""Profile management for audiobook generation.

Handles CRUD operations for ebook/model/voice generation profiles using SQLite.
Uses per-operation connections so it is safe to call from FastAPI's threadpool.
"""
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

from app.core.config import settings
from app.services.database import SCHEMA_SQL, DB_PATH

logger = logging.getLogger(__name__)


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


# Alias for backward compat with any remaining _row_to_dict calls.
def _row_to_dict(row):
    return _profile_row_to_dict(row)


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

    def _get_conn(self) -> sqlite3.Connection:
        """Fresh per-operation connection (thread-safe) with schema ensured."""
        db_path = Path(self.db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        if not self._table_exists(conn, "profiles"):
            conn.executescript(SCHEMA_SQL)
        return conn

    def close(self):
        """No-op retained for API compatibility (connections are per-operation now)."""

    @staticmethod
    def _table_exists(conn, table_name: str) -> bool:
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        )
        return len(cur.fetchall()) > 0

    # ------------------------------------------------------------------ #
    #  Hash helper                                                        #
    # ------------------------------------------------------------------ #

    def _compute_ebook_hash(self, ebook_path: str) -> str:
        """Compute MD5 hash of ebook file for change detection."""
        from app.utils.path_utils import compute_ebook_hash
        try:
            full_path = Path(ebook_path)
            if not full_path.exists():
                full_path = settings.EBOOKS_DIR / ebook_path
            digest = compute_ebook_hash(full_path)
            return digest[:12] if digest else "unknown"
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

        conn = self._get_conn()
        try:
            cur = conn.execute(
                """INSERT INTO profiles
                   (ebook_path, model_name, voice, title, status, ebook_hash,
                    total_chunks, completed_chunks, progress_pct, error, created_at, updated_at)
                 VALUES (?, ?, ?, ?, 'not_started', ?, 0, 0, 0.0, NULL, ?, ?)""",
                (ebook_path, model_name, voice, title, self._compute_ebook_hash(ebook_path), now, now),
            )

            chapter_rows = chapters or []
            for ch in chapter_rows:
                conn.execute(
                    """INSERT INTO chapters
                       (profile_id, name, start_idx, end_idx, start_chunk, end_chunk)
                     VALUES (?, ?, ?, ?, ?, ?)""",
                    (cur.lastrowid, ch["name"], ch.get("start_idx", 0),
                     ch.get("end_idx", 0), ch.get("start_chunk", 0), ch.get("end_chunk", 0)),
                )

            conn.commit()
        finally:
            conn.close()
        return self.get_profile(ebook_path, model_name, voice) or {}

    def get_profile(self, ebook_path: str, model_name: str, voice: str) -> Optional[dict]:
        """Get a profile by key."""
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT * FROM profiles WHERE ebook_path=? AND model_name=? AND voice=?",
                (ebook_path, model_name, voice),
            ).fetchone()

            if not row:
                return None

            result = _row_to_dict(row)
            # Attach chapters.
            rows = conn.execute(
                "SELECT * FROM chapters WHERE profile_id=? ORDER BY start_idx ASC",
                (result["id"],),
            ).fetchall()
            result["chapters"] = [_row_to_dict(r) for r in rows]

            # Attach bookmarks with context='profile'.
            bm_rows = conn.execute(
                "SELECT chunk_index, text_preview FROM bookmarks WHERE ebook_path=? AND context='profile' ORDER BY chunk_index ASC",
                (ebook_path,),
            ).fetchall()
            result["bookmarks"] = [r["chunk_index"] for r in bm_rows]

            return result
        finally:
            conn.close()

    def update_profile_status(
        self, ebook_path: str, model_name: str, voice: str, status: str,
        completed_chunks: int = None, error: str = None, total_chunks: int = None,
    ):
        """Update profile status and related fields."""
        now = datetime.now(timezone.utc).isoformat()

        conn = self._get_conn()
        try:
            cur_row = conn.execute(
                "SELECT * FROM profiles WHERE ebook_path=? AND model_name=? AND voice=?",
                (ebook_path, model_name, voice),
            ).fetchone()
            if not cur_row:
                return  # no-op

            cc = completed_chunks or cur_row["completed_chunks"]
            tc = total_chunks or cur_row["total_chunks"]
            progress_pct = round(cc / tc * 100, 1) if tc and tc > 0 else (cc * 1.0 if tc == 0 else 0.0)

            conn.execute(
                """UPDATE profiles SET status=?, updated_at=?, completed_chunks=?, total_chunks=?, progress_pct=? WHERE id=?""",
                (status, now, cc, tc or cur_row["total_chunks"], progress_pct, cur_row["id"]),
            )
            if error is not None:
                conn.execute(
                    "UPDATE profiles SET error=? WHERE id=?",
                    (error, cur_row["id"]),
                )
            conn.commit()
        finally:
            conn.close()

    def delete_profile(self, ebook_path: str, model_name: str, voice: str) -> bool:
        """Delete a profile."""
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT id FROM profiles WHERE ebook_path=? AND model_name=? AND voice=?",
                (ebook_path, model_name, voice),
            ).fetchone()
            if not row:
                return False

            conn.execute("DELETE FROM bookmarks WHERE ebook_path=?", (ebook_path,))
            conn.execute(
                "DELETE FROM profiles WHERE id=?", (row["id"],)
            )
            conn.commit()
            return True
        finally:
            conn.close()

    def update_profile_chapters(self, ebook_path: str, model_name: str, voice: str,
                                chapters: list, total_chunks: int):
        """Update profile with chapter and chunk info."""
        conn = self._get_conn()
        try:
            cur_row = conn.execute(
                "SELECT id FROM profiles WHERE ebook_path=? AND model_name=? AND voice=?",
                (ebook_path, model_name, voice),
            ).fetchone()
            if not cur_row:
                return

            profile_id = cur_row["id"]

            # Replace chapters.
            conn.execute("DELETE FROM chapters WHERE profile_id=?", (profile_id,))
            for ch in chapters or []:
                conn.execute(
                    """INSERT INTO chapters
                       (profile_id, name, start_idx, end_idx, start_chunk, end_chunk)
                     VALUES (?, ?, ?, ?, ?, ?)""",
                    (profile_id, ch["name"], ch.get("start_idx", 0),
                     ch.get("end_idx", 0), ch.get("start_chunk", 0), ch.get("end_chunk", 0)),
                )

            conn.execute(
                "UPDATE profiles SET total_chunks=?, ebook_hash=?, updated_at=? WHERE id=?",
                (total_chunks, self._compute_ebook_hash(ebook_path),
                 datetime.now(timezone.utc).isoformat(), profile_id),
            )
            conn.commit()
        finally:
            conn.close()

    def get_all_profiles(self) -> Dict[str, dict]:
        """Get all profiles keyed by {ebook}:{model}:{voice}."""
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM profiles ORDER BY created_at ASC"
            ).fetchall()
            result = {}
            for row in rows:
                d = _row_to_dict(row)
                key = f"{d['ebook_path']}:{d['model_name']}:{d['voice']}"

                ch_rows = conn.execute(
                    "SELECT * FROM chapters WHERE profile_id=? ORDER BY start_idx ASC",
                    (d["id"],),
                ).fetchall()
                d["chapters"] = [_row_to_dict(r) for r in ch_rows]

                bm_rows = conn.execute(
                    "SELECT chunk_index FROM bookmarks WHERE ebook_path=? AND context='profile' ORDER BY chunk_index ASC",
                    (d["ebook_path"],),
                ).fetchall()
                d["bookmarks"] = [r["chunk_index"] for r in bm_rows]

                result[key] = d
            return result
        finally:
            conn.close()

    def get_profiles_for_ebook(self, ebook_path: str) -> Dict[str, dict]:
        """Get all profiles for a specific ebook."""
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM profiles WHERE ebook_path=? ORDER BY created_at ASC",
                (ebook_path,),
            ).fetchall()
            result = {}
            for row in rows:
                d = _row_to_dict(row)
                key = f"{d['ebook_path']}:{d['model_name']}:{d['voice']}"

                ch_rows = conn.execute(
                    "SELECT * FROM chapters WHERE profile_id=? ORDER BY start_idx ASC",
                    (d["id"],),
                ).fetchall()
                d["chapters"] = [_row_to_dict(r) for r in ch_rows]

                bm_rows = conn.execute(
                    "SELECT chunk_index FROM bookmarks WHERE ebook_path=? AND context='profile' ORDER BY chunk_index ASC",
                    (d["ebook_path"],),
                ).fetchall()
                d["bookmarks"] = [r["chunk_index"] for r in bm_rows]

                result[key] = d
            return result
        finally:
            conn.close()
