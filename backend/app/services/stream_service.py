"""
Core streaming service for on-demand TTS generation.

Handles text extraction, image retrieval, and TTS audio generation.
Cache management and settings are delegated to separate services.
Progress/bookmarks use SQLite (bookmarks table with context='progress').
"""
from pathlib import Path
from typing import Optional, Dict
import json
import hashlib
import logging
from datetime import datetime

from openai import OpenAI
import httpx

from app.services.ebook_parser import EbookParser
from app.services.cache_service import CacheService
from app.services.settings_service import SettingsService
from app.core.config import settings
from app.models.streaming import StreamProgress

logger = logging.getLogger(__name__)


class StreamService:
    """Core service for streaming TTS generation."""

    def __init__(self):
        self.ebook_parser = EbookParser()
        self.cache_service = CacheService(
            audio_format=settings.AUDIO_FORMAT,
            compute_hash_fn=self._compute_ebook_hash,
        )
        self.settings_service = SettingsService()

    # ------------------------------------------------------------------ #
    #  Settings delegation                                               #
    # ------------------------------------------------------------------ #

    def load_settings(self) -> Dict:
        return self.settings_service.load_settings()

    def save_settings(self, data: Dict) -> None:
        self.settings_service.save_settings(data)

    # ------------------------------------------------------------------ #
    #  Cache delegation                                                  #
    # ------------------------------------------------------------------ #

    def get_cache_dir(self, ebook_path: str, model: str, voice: str) -> Path:
        return self.cache_service.get_cache_dir(ebook_path, model, voice)

    def get_base_cache_dir(self, ebook_path: str) -> Path:
        return self.cache_service.get_base_cache_dir(ebook_path)

    def get_cache_status(self, ebook_path: str, model: str = None, voice: str = None) -> Dict:
        return self.cache_service.get_cache_status(ebook_path, model, voice)

    def clear_stream_cache(self, ebook_path: str, model: str = None, voice: str = None) -> Dict:
        return self.cache_service.clear_stream_cache(ebook_path, model, voice)

    def get_cached_stream_audio_by_chars(
        self, ebook_path: str, start_char: int, end_char: int, model: str, voice: str
    ) -> Optional[bytes]:
        return self.cache_service.get_cached_stream_audio_by_chars(
            ebook_path, start_char, end_char, model, voice
        )

    def find_stream_cache_covering_range(
        self, cache_model_dir: Path, start_char: int, end_char: int
    ) -> Optional[Path]:
        return self.cache_service.find_stream_cache_covering_range(
            cache_model_dir, start_char, end_char
        )

    @staticmethod
    def _get_db_conn():
        """Get a dict-like SQLite connection (same row_factory as database.get_connection)."""
        import sqlite3 as _sqlite3
        conn = _sqlite3.connect(str(settings.STORAGE_DIR / "app.db"))
        conn.row_factory = _sqlite3.Row  # Enable dict-style row access by index name
        return conn
    
    _progress_schema_created = False
    
    def _ensure_progress_schema(self):
        """Ensure the progress/bookmarks tables exist in app.db."""
        if StreamService._progress_schema_created:
            return
        db_path = settings.STORAGE_DIR / "app.db"
        conn = self._get_db_conn()
        try:
            # Create bookmarks table if missing (for tests that don't call init_db).
            conn.execute("""
                CREATE TABLE IF NOT EXISTS bookmarks (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    ebook_path    TEXT NOT NULL,
                    context       TEXT NOT NULL DEFAULT 'progress',
                    chunk_index   INTEGER NOT NULL,
                    text_preview  TEXT DEFAULT ''
                )
            """)
            conn.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_bookmarks_unique
                    ON bookmarks(ebook_path, context, chunk_index)
            """)
            # Create profiles table if missing (for update_progress to work).
            conn.execute("""
                CREATE TABLE IF NOT EXISTS profiles (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    ebook_path      TEXT NOT NULL,
                    model_name      TEXT NOT NULL,
                    voice           TEXT NOT NULL,
                    title           TEXT,
                    status          TEXT NOT NULL DEFAULT 'not_started',
                    last_position   INTEGER DEFAULT 0
                )
            """)
            # Ensure last_position column exists (migration for older DBs).
            try:
                conn.execute("ALTER TABLE profiles ADD COLUMN last_position INTEGER DEFAULT 0")
            except Exception:  # already exists or table has different shape
                pass
            conn.commit()
        finally:
            conn.close()
        StreamService._progress_schema_created = True

    def get_progress(self, ebook_path: str) -> StreamProgress:
        """Return a StreamProgress populated from the SQLite tables.

        Current chunk comes exclusively from profiles.last_position (primary source).
        Bookmarks are collected but do NOT influence playback position.
        """
        self._ensure_progress_schema()
        conn = self._get_db_conn()
        try:
            # 1. Current chunk from profiles table (primary source).
            current_chunk: int = 0
            row = conn.execute(
                "SELECT last_position FROM profiles WHERE ebook_path=?",
                (ebook_path,),
            ).fetchone()
            if row and row["last_position"] is not None:
                val = int(row["last_position"])
                if val > current_chunk:
                    current_chunk = val

            # 2. Also check bookmarks for backward compat (legacy data still in DB).
            bm_rows = conn.execute(
                "SELECT chunk_index, text_preview FROM bookmarks WHERE ebook_path=? AND context='progress' ORDER BY chunk_index ASC",
                (ebook_path,),
            ).fetchall()

            # 3. Collect user-created bookmark entries (do NOT influence current_chunk).
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
        self._ensure_progress_schema()
        conn = self._get_db_conn()
        try:
            # Upsert into profiles table — this is separate from user bookmarks.
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
                # Create a minimal profile row just to hold position.
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
        self._ensure_progress_schema()
        conn = self._get_db_conn()
        try:
            # Check if bookmark already exists.
            existing = conn.execute(
                "SELECT 1 FROM bookmarks WHERE ebook_path=? AND context='progress' AND chunk_index=?",
                (ebook_path, chunk_index),
            ).fetchone()

            if existing:
                conn.execute(
                    "DELETE FROM bookmarks WHERE ebook_path=? AND context='progress' AND chunk_index=?",
                    (ebook_path, chunk_index),
                )
                conn.commit()  # ← Added missing commit.
                return False  # removed
            else:
                conn.execute(
                    "INSERT OR IGNORE INTO bookmarks (ebook_path, context, chunk_index, text_preview) VALUES (?, 'progress', ?, ?)",
                    (ebook_path, chunk_index, text_preview or ""),
                )
                conn.commit()  # ← Added missing commit.
                return True  # added
        except Exception as e:
            logger.error("[ERROR] Failed to toggle bookmark: %s", e)
            raise
        finally:
            conn.close()

    def clear_progress(self, ebook_path: str):
        self._ensure_progress_schema()
        conn = self._get_db_conn()
        try:
            # Clear position from profiles.
            conn.execute(
                "DELETE FROM profiles WHERE ebook_path=?",
                (ebook_path,),
            )
            # Also clear any legacy progress bookmarks.
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
        self._ensure_progress_schema()
        if old_path == new_path:
            return
        conn = self._get_db_conn()
        try:
            # Migrate bookmarks table.
            conn.execute(
                "UPDATE bookmarks SET ebook_path=? WHERE ebook_path=?",
                (new_path, old_path),
            )
            # Migrate profiles table (reading position / last_position).
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

    def rename_progress_recursive(self, source_path_str: str,
                                  dest_dir: str) -> int:
        """Migrate bookmarks + reading position for all ebook files under *source_path_str*

        so that their DB entries point to the path where they now live on disk
        (*dest_dir*).  Uses SQL REPLACE — deterministic, no filesystem walks,
        and preserves ALL rows (multiple bookmarks/profiles per file).

        Returns the number of migrated ebook paths.
        """
        self._ensure_progress_schema()
        if source_path_str == dest_dir:
            return 0

        old_prefix = f"{source_path_str}/"
        new_base   = dest_dir.rstrip('/')
        conn = self._get_db_conn()
        migrated = 0
        try:
            # Count how many ebook paths will be affected.
            rows = list(conn.execute(
                "SELECT DISTINCT ebook_path FROM bookmarks WHERE ebook_path LIKE ?"
                    f" UNION SELECT DISTINCT ebook_path FROM profiles "
                    f"WHERE ebook_path LIKE ?",
                (f"{old_prefix}%", f"{old_prefix}%")
            ).fetchall())

            if not rows:
                return 0

            # Build a mapping from old prefix -> new path for each affected file.
            all_paths = [str(r["ebook_path"]) for r in rows]
            replacements: Dict[str, str] = {}
            for old_ebook in all_paths:
                remaining = old_ebook[len(old_prefix):]
                if new_base:
                    new_ebook = f"{new_base}/{remaining}"
                else:
                    new_ebook = remaining
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

    # ------------------------------------------------------------------ #
    #  Path helpers                                                      #
    # ------------------------------------------------------------------ #

    def _resolve_ebook_path(self, ebook_path: str) -> Path:
        """Resolve ebook path to full path, raising 404 if not found."""
        path = Path(ebook_path)
        if path.exists():
            return path
        full_path = settings.EBOOKS_DIR / ebook_path
        if full_path.exists():
            return full_path
        raise FileNotFoundError(f"Ebook not found: {ebook_path}")

    def _compute_ebook_hash(self, ebook_path: Path) -> str:
        """Compute MD5 hash of ebook file, with mtime-based cache."""
        path_str = str(ebook_path)
        current_mtime = ebook_path.stat().st_mtime

        if path_str in self._hash_cache:
            cached_mtime, cached_hash = self._hash_cache[path_str]
            if cached_mtime == current_mtime:
                return cached_hash

        hash_md5 = hashlib.md5()
        with open(ebook_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_md5.update(chunk)
        file_hash = hash_md5.hexdigest()
        self._hash_cache[path_str] = (current_mtime, file_hash)
        return file_hash

    # ------------------------------------------------------------------ #
    #  Ebook parsing                                                     #
    # ------------------------------------------------------------------ #

    def parse_ebook_for_streaming(self, ebook_path: str, chunk_size: int = 4096) -> Dict:
        full_path = self._resolve_ebook_path(ebook_path)
        return self.ebook_parser.parse_and_cache(full_path, with_images=False)

    def parse_ebook_with_images(self, ebook_path: str, chunk_size: int = 4096) -> Dict:
        full_path = self._resolve_ebook_path(ebook_path)
        return self.ebook_parser.parse_and_cache(full_path, with_images=True)

    # ------------------------------------------------------------------ #
    #  Text extraction                                                   #
    # ------------------------------------------------------------------ #

    def get_text_segment(
        self, ebook_path: str, start_char: int, end_char: int
    ) -> str:
        """Get a segment of text from the ebook by character range."""
        ebook_data = self.parse_ebook_for_streaming(ebook_path)
        text_segments = []
        for chunk in ebook_data["chunks"]:
            if chunk["start_idx"] < end_char and chunk["end_idx"] > start_char:
                chunk_start = max(0, start_char - chunk["start_idx"])
                chunk_end = min(len(chunk["text"]), end_char - chunk["start_idx"])
                text_segments.append(chunk["text"][chunk_start:chunk_end])
        return "".join(text_segments)

    def find_chapter_at_position(self, ebook_path: str, char_position: int) -> Optional[Dict]:
        """Find which chapter contains the given character position."""
        ebook_data = self.parse_ebook_for_streaming(ebook_path)
        for chapter in ebook_data["chapters"]:
            if chapter["start_idx"] <= char_position < chapter["end_idx"]:
                return chapter
        return None

    def get_image(self, ebook_path: str, image_id: str) -> Optional[str]:
        """Get a specific image by ID (returns base64 data URL)."""
        cache_key_prefix = f"{self._get_cache_key(ebook_path)}:"
        for key, data in self._cache.items():
            if key.startswith(cache_key_prefix) and "images" in data:
                if image_id in data["images"]:
                    return data["images"][image_id]
        result = self.parse_ebook_with_images(ebook_path)
        return result.get("images", {}).get(image_id)

    # ------------------------------------------------------------------ #
    #  TTS audio generation                                              #
    # ------------------------------------------------------------------ #

    def generate_audio_for_text(
        self,
        text: str,
        model: str,
        voice: str,
        ebook_path: str = None,
        start_char: int = None,
        end_char: int = None,
        save_to_disk: bool = True,
    ) -> bytes:
        """
        Generate audio for a specific text segment.
        Returns audio data as bytes.

        Args:
            save_to_disk: If False, skip cache lookup and disk write entirely
                (ephemeral/streaming-only mode). Defaults to True.
        """
        logger.debug(
            "[DEBUG] Generating audio - model: %s, voice: %s, text length: %d",
            model, voice, len(text),
        )

        # Check stream cache (only when saving is enabled)
        if save_to_disk and ebook_path and start_char is not None and end_char is not None:
            cached = self.get_cached_stream_audio_by_chars(
                ebook_path, start_char, end_char, model, voice
            )
            if cached:
                logger.debug(
                    "[DEBUG] Returning cached stream audio for chars %d-%d",
                    start_char, end_char,
                )
                return cached

        model_config = self._get_model_config(model)
        api_model = model_config.get("api_model", model) if model_config else model

        # Apply text scrubbing
        text_scrub_chars = (
            model_config.get("text_scrub_chars") if model_config else None
        )
        if text_scrub_chars:
            original_len = len(text)
            text = self._scrub_text(text, text_scrub_chars)
            logger.debug(
                "[DEBUG] Text scrubbed: %d -> %d chars (removed: %s)",
                original_len, len(text), text_scrub_chars,
            )

        client = self._get_openai_client(model_config)

        try:
            response = client.audio.speech.create(
                model=api_model,
                voice=voice,
                input=text,
                response_format=settings.AUDIO_FORMAT,
            )
            audio_data = response.read()
            logger.debug("[DEBUG] Generated audio: %d bytes", len(audio_data))

            # Persist audio data when save_to_disk=True (and params present)
            if save_to_disk and ebook_path and start_char is not None and end_char is not None:
                try:
                    cache_dir = self.get_cache_dir(ebook_path, model, voice)
                    logger.info(
                        "[STREAM CACHE] Saving audio for chars %d-%d -> dir=%s (exists=%s)",
                        start_char, end_char, cache_dir, cache_dir.exists(),
                    )
                    cache_dir.mkdir(parents=True, exist_ok=True)
                    if not cache_dir.exists():
                        logger.error(
                            "[STREAM CACHE ERROR] mkdir failed to create dir: %s",
                            cache_dir,
                        )
                        raise RuntimeError(f"Failed to create cache directory: {cache_dir}")
                    audio_file = cache_dir / f"audio_{start_char}_{end_char}.{settings.AUDIO_FORMAT}"
                    logger.info(
                        "[STREAM CACHE] Writing %d bytes -> %s",
                        len(audio_data), audio_file,
                    )
                    audio_file.write_bytes(audio_data)
                    if not audio_file.exists():
                        raise RuntimeError(f"Audio file was not created: {audio_file}")
                    logger.info(
                        "[STREAM CACHE] Successfully saved %d bytes to %s",
                        len(audio_data), audio_file,
                    )
                except Exception as e:
                    logger.error("[STREAM CACHE ERROR] Failed to save audio: %s", e)
            elif not save_to_disk and (ebook_path or start_char is not None):
                pass  # ephemeral mode — no cache lookup, no disk write

            return audio_data

        except Exception as e:
            logger.error("[ERROR] TTS generation failed: %s", e)
            raise

    # ------------------------------------------------------------------ #
    #  Model config                                                      #
    # ------------------------------------------------------------------ #

    def _get_model_config(self, model_name: str) -> Optional[Dict]:
        if not settings.MODELS_CONFIG_FILE.exists():
            return None
        try:
            with open(settings.MODELS_CONFIG_FILE, "r") as f:
                return json.load(f).get(model_name)
        except Exception as e:
            logger.error("[ERROR] Failed to load model config: %s", e)
            return None

    def _get_openai_client(self, model_config: Optional[Dict]) -> OpenAI:
        base_url = None
        api_key = None
        if model_config:
            base_url = model_config.get("base_url")
            api_key = model_config.get("api_key")
        if not base_url and settings.OPENAI_BASE_URL:
            base_url = settings.OPENAI_BASE_URL
        if not api_key and settings.OPENAI_API_KEY:
            api_key = settings.OPENAI_API_KEY
        if not api_key:
            api_key = "not-needed"
        http_client = httpx.Client(timeout=120.0)
        return OpenAI(api_key=api_key, base_url=base_url, http_client=http_client)

    # ------------------------------------------------------------------ #
    #  Internal helpers                                                  #
    # ------------------------------------------------------------------ #

    def _scrub_text(self, text: str, scrub_chars: str) -> str:
        if not scrub_chars:
            return text
        for char in scrub_chars:
            text = text.replace(char, "")
        return text

    def _get_cache_key(self, ebook_path: str) -> str:
        full_path = self._resolve_ebook_path(ebook_path)
        file_hash = self._compute_ebook_hash(full_path)
        return f"{ebook_path}:{file_hash}"

    # Cache dicts (kept for backward compat with callers that use them)
    _cache: Dict = {}
    _hash_cache: Dict = {}
