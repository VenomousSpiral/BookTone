"""
Cache service for stream audio management.

Handles cache directory resolution, scanning, size calculation,
and deletion of cached audio files.
Extracted from stream_service.py to reduce its size.
"""
from pathlib import Path
from typing import Dict, List, Optional
import logging
import shutil

from app.core.config import settings
from app.utils.path_utils import (
    resolve_cache_dir,
    resolve_base_cache_dir,
    safe_stem,
)

logger = logging.getLogger(__name__)


class CacheService:
    """Manages stream audio cache directories and files."""

    def __init__(
        self,
        audio_format: str = None,
        compute_hash_fn=None,
    ):
        self.audio_format = audio_format or settings.AUDIO_FORMAT
        self.compute_hash_fn = compute_hash_fn

    # ---- Path resolution helpers ----

    def get_cache_dir(self, ebook_path: str, model: str, voice: str) -> Path:
        """Get the cache directory for a specific ebook+model+voice combo."""
        return resolve_cache_dir(
            settings.AUDIOBOOKS_DIR, ebook_path, model, voice,
            compute_hash_fn=self.compute_hash_fn,
        )

    def get_base_cache_dir(self, ebook_path: str) -> Path:
        """Get the base cache directory (parent of model/voice subdirs)."""
        return resolve_base_cache_dir(
            settings.AUDIOBOOKS_DIR, ebook_path,
            compute_hash_fn=self.compute_hash_fn,
        )

    def get_stream_cache_dir_for_ebook(self, ebook_path: str) -> Optional[Path]:
        """Find any existing stream cache directory for an ebook."""
        cache_dir = self.get_base_cache_dir(ebook_path)
        return cache_dir if cache_dir.exists() else None

    # ---- Cache scanning ----

    def get_cache_status(
        self, ebook_path: str, model: str = None, voice: str = None
    ) -> Dict:
        """
        Get information about cached stream audio for an ebook.
        Returns cache size, number of cached chunks, and cache location.
        """
        try:
            base_cache_dir = self.get_base_cache_dir(ebook_path)

            if not base_cache_dir.exists():
                return {
                    "has_cache": False,
                    "total_size_bytes": 0,
                    "total_size_mb": 0,
                    "cached_chunks": 0,
                    "model_voice_caches": [],
                }

            model_voice_caches = []
            total_size = 0
            total_chunks = 0

            for model_dir in base_cache_dir.iterdir():
                if not model_dir.is_dir():
                    continue
                for voice_dir in model_dir.iterdir():
                    if not voice_dir.is_dir():
                        continue

                    cache_info = {
                        "model": model_dir.name,
                        "voice": voice_dir.name,
                        "files": 0,
                        "size_bytes": 0,
                        "size_mb": 0,
                    }

                    for audio_file in voice_dir.glob(f"audio_*.{self.audio_format}"):
                        cache_info["files"] += 1
                        cache_info["size_bytes"] += audio_file.stat().st_size

                    cache_info["size_mb"] = round(
                        cache_info["size_bytes"] / (1024 * 1024), 2
                    )
                    total_size += cache_info["size_bytes"]
                    total_chunks += cache_info["files"]

                    if model and voice:
                        if (
                            model_dir.name == model
                            and voice_dir.name == voice
                        ):
                            model_voice_caches.append(cache_info)
                    else:
                        model_voice_caches.append(cache_info)

            return {
                "has_cache": total_chunks > 0,
                "total_size_bytes": total_size,
                "total_size_mb": round(total_size / (1024 * 1024), 2),
                "cached_chunks": total_chunks,
                "model_voice_caches": model_voice_caches,
            }

        except FileNotFoundError:
            return {
                "has_cache": False,
                "total_size_bytes": 0,
                "total_size_mb": 0,
                "cached_chunks": 0,
                "model_voice_caches": [],
            }

    # ---- Cache deletion ----

    def clear_stream_cache(
        self, ebook_path: str, model: str = None, voice: str = None
    ) -> Dict:
        """
        Clear cached stream audio for an ebook.
        If model/voice specified, only clears that specific cache.
        """
        try:
            base_cache_dir = self.get_base_cache_dir(ebook_path)

            if not base_cache_dir.exists():
                return {
                    "message": "No cache found",
                    "deleted_files": 0,
                    "deleted_size_mb": 0,
                }

            deleted_files = 0
            deleted_size = 0

            def _delete_mv_dir(mv_dir: Path) -> None:
                nonlocal deleted_files, deleted_size
                for audio_file in mv_dir.glob(f"audio_*.{self.audio_format}"):
                    deleted_size += audio_file.stat().st_size
                    audio_file.unlink()
                    deleted_files += 1
                for combined_file in mv_dir.glob(f"combined.{self.audio_format}"):
                    deleted_size += combined_file.stat().st_size
                    combined_file.unlink()
                    deleted_files += 1
                try:
                    shutil.rmtree(mv_dir)
                except OSError:
                    pass

            if model and voice:
                mv_dir = base_cache_dir / model / voice
                if mv_dir.exists():
                    _delete_mv_dir(mv_dir)
            else:
                for mv_dir in base_cache_dir.iterdir():
                    if mv_dir.is_dir():
                        _delete_mv_dir(mv_dir)
                try:
                    shutil.rmtree(base_cache_dir)
                except OSError:
                    pass

            return {
                "message": f"Deleted {deleted_files} cached audio files",
                "deleted_files": deleted_files,
                "deleted_size_mb": round(deleted_size / (1024 * 1024), 2),
            }

        except FileNotFoundError:
            return {
                "message": "Ebook not found",
                "deleted_files": 0,
                "deleted_size_mb": 0,
            }

    # ---- Cache lookup ----

    def get_cached_stream_audio_by_chars(
        self, ebook_path: str, start_char: int, end_char: int, model: str, voice: str
    ) -> Optional[bytes]:
        """Check if audio for a specific char range was cached."""
        cache_dir = self.get_cache_dir(ebook_path, model, voice)
        audio_file = cache_dir / f"audio_{start_char}_{end_char}.{self.audio_format}"
        if audio_file.exists():
            logger.debug(
                "[STREAM CACHE] Found cached audio for chars %d-%d: %s",
                start_char, end_char, audio_file,
            )
            return audio_file.read_bytes()
        return None

    def find_stream_cache_covering_range(
        self, cache_model_dir: Path, start_char: int, end_char: int
    ) -> Optional[Path]:
        """
        Find a cached stream audio file that exactly covers the given text range.
        Returns the path to the cache file if found, None otherwise.
        """
        if not cache_model_dir or not cache_model_dir.exists():
            return None

        for audio_file in cache_model_dir.glob(f"audio_*.{self.audio_format}"):
            try:
                parts = audio_file.stem.split("_")
                if len(parts) == 3 and parts[0] == "audio":
                    cached_start = int(parts[1])
                    cached_end = int(parts[2])
                    if cached_start <= start_char and cached_end >= end_char:
                        return audio_file
            except (ValueError, IndexError):
                continue
        return None
