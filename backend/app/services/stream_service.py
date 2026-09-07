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

from openai import OpenAI
import httpx

from app.services.ebook_parser import EbookParser
from app.services.cache_service import CacheService
from app.services.settings_service import SettingsService
from app.services.progress_repository import ProgressRepository
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
        self.progress_repository = ProgressRepository()

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
        self, ebook_path: str, start_char: int, end_char: int,
        model: str, voice: str, text: str = "",
    ) -> Optional[bytes]:
        return self.cache_service.get_cached_stream_audio_by_chars(
            ebook_path, start_char, end_char, model, voice, text=text
        )

    # ------------------------------------------------------------------ #
    #  Progress/bookmark delegation                                      #
    # ------------------------------------------------------------------ #

    def get_progress(self, ebook_path: str) -> StreamProgress:
        """Return playback position and bookmarks for an ebook."""
        return self.progress_repository.get_progress(ebook_path)

    def update_progress(self, ebook_path: str, chunk_index: int):
        """Store the current playback position in profiles.last_position."""
        self.progress_repository.update_progress(ebook_path, chunk_index)

    def toggle_bookmark(self, ebook_path: str, chunk_index: int, text_preview: str = "") -> bool:
        return self.progress_repository.toggle_bookmark(ebook_path, chunk_index, text_preview)

    def clear_progress(self, ebook_path: str):
        self.progress_repository.clear_progress(ebook_path)

    def rename_progress(self, old_path: str, new_path: str):
        """Migrate progress/bookmarks from old path to new path."""
        self.progress_repository.rename_progress(old_path, new_path)

    def rename_progress_recursive(self, source_path_str: str, dest_dir: str) -> int:
        """Migrate progress/bookmarks for all ebooks under *source_path_str*."""
        return self.progress_repository.rename_progress_recursive(source_path_str, dest_dir)

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
        """Compute MD5 hash of ebook file (delegates to shared path_utils helper).

        Raises FileNotFoundError for missing files, matching the previous
        ``stat()``-based contract that download routes map to a 404.
        """
        from app.utils.path_utils import compute_ebook_hash
        if not ebook_path.exists():
            raise FileNotFoundError(f"Ebook not found: {ebook_path}")
        return compute_ebook_hash(ebook_path)

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
        use_cached: bool = True,
    ) -> bytes:
        """
        Generate audio for a specific text segment.
        Returns audio data as bytes.

        Args:
            save_to_disk: If False, skip all disk operations entirely
                (ephemeral/streaming-only mode). Defaults to True.
            use_cached: If False, always generate fresh TTS even if cached
                version exists. When used together with save_to_disk=True,
                freshly generated audio is still saved to disk for future use.
                Defaults to True.
        """
        logger.debug(
            "[DEBUG] Generating audio - model: %s, voice: %s, text length: %d",
            model, voice, len(text),
        )

        # use_cached controls ONLY the cache read (lookup).
        # save_stream_audio independently controls whether generated chunks are written.
        if not use_cached:
            logger.debug(
                "[STREAM CACHE] Skipped — always generating fresh TTS for chars %d-%d",
                start_char, end_char,
            )
        elif ebook_path and start_char is not None and end_char is not None:
            # CAS lookup with text content (stable across versions)
            cached = self.get_cached_stream_audio_by_chars(
                ebook_path, start_char, end_char, model, voice, text=text
            )
            if cached:
                logger.debug(
                    "[STREAM CACHE] Hit: returning cached audio for chars %d-%d",
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

            # Persist audio whenever save_to_disk=True, regardless of use_cached.
            # use_cached only controls whether we READ from disk; save_stream_audio
            # independently controls whether freshly generated chunks are WRITTEN to disk.
            if save_to_disk and ebook_path and start_char is not None and end_char is not None:
                try:
                    # CAS save path — content-hash filename (stable across versions)
                    content_hash = hashlib.md5(text.encode()).hexdigest()[:16]
                    cache_dir = self.get_cache_dir(ebook_path, model, voice)
                    logger.info(
                        "[STREAM CACHE] Saving audio for chars %d-%d -> dir=%s hash=%s (exists=%s)",
                        start_char, end_char, cache_dir, content_hash, cache_dir.exists(),
                    )
                    cache_dir.mkdir(parents=True, exist_ok=True)
                    if not cache_dir.exists():
                        logger.error(
                            "[STREAM CACHE ERROR] mkdir failed to create dir: %s",
                            cache_dir,
                        )
                        raise RuntimeError(f"Failed to create cache directory: {cache_dir}")
                    audio_file = cache_dir / f"{content_hash}.{settings.AUDIO_FORMAT}"
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
