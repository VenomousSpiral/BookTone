"""
Stream Audiobook Service
Manages audiobook profiles and generation using the stream cache.
Each profile = {ebook_path}:{model}:{voice} combo with independent progress tracking.

Uses extracted GenerationQueue and ProfileManager for separation of concerns.
"""
import asyncio
import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from datetime import datetime

from app.services.ebook_parser import EbookParser
from app.services.stream_service import StreamService
from app.services.generation_queue import GenerationQueue
from app.services.profile_manager import ProfileManager
from app.core.config import settings

logger = logging.getLogger(__name__)


class StreamAudiobookService:
    """Manages audiobook profiles and orchestrates generation using stream cache."""

    def __init__(self):
        self.stream_service = StreamService()
        self.ebook_parser = EbookParser()
        self.queue = GenerationQueue()
        self.profile_manager = ProfileManager()

    def create_profile(self, ebook_path: str, model: str, voice: str) -> dict:
        """Create a new profile for an ebook/model/voice combo."""
        total_chunks = 0
        chapters = []
        try:
            ebook_data = self.stream_service.parse_ebook_for_streaming(ebook_path)
            total_chunks = len(ebook_data["chunks"])
            chapters = ebook_data.get("chapters", [])
        except Exception:
            pass

        return self.profile_manager.create_profile(
            ebook_path, model, voice,
            total_chunks=total_chunks, chapters=chapters,
        )

    def pause_generation(self, ebook_path: str, model: str, voice: str) -> dict:
        """Pause current generation - cancels the running task."""
        key = f"{ebook_path}:{model}:{voice}"
        self.queue._per_item_paused[key] = True

        if (
            self.queue.current
            and self.queue.current[0] == ebook_path
            and self.queue.current[1] == model
            and self.queue.current[2] == voice
        ):
            task = self.queue.current[3]
            if task and not task.done():
                logger.info(
                    "[QUEUE] Cancelling task for %s/%s/%s",
                    ebook_path, model, voice,
                )
                task.cancel()
            self.queue.current = None

        self.profile_manager.update_profile_status(
            ebook_path, model, voice, "paused"
        )
        profile = self.profile_manager.get_profile(ebook_path, model, voice)
        if profile:
            return dict(profile)
        return {
            "ebook_path": ebook_path,
            "model": model,
            "voice": voice,
            "status": "paused",
        }

    def resume_generation(
        self, ebook_path: str, model: str, voice: str, background_tasks
    ) -> dict:
        """Resume current generation or start next in queue."""
        key = f"{ebook_path}:{model}:{voice}"
        self.queue._per_item_paused.pop(key, None)

        if not self.profile_manager.get_profile(ebook_path, model, voice):
            self.create_profile(ebook_path, model, voice)

        self.profile_manager.update_profile_status(
            ebook_path, model, voice, "in_progress"
        )
        self.queue.enqueue(ebook_path, model, voice)
        self._start_next_in_queue(background_tasks)

        profile = self.profile_manager.get_profile(ebook_path, model, voice)
        return dict(profile) if profile else {}

    # ------------------------------------------------------------------ #
    #  Cache info (reads disk, not profiles)                              #
    # ------------------------------------------------------------------ #

    def get_ebook_cache_info(self, ebook_path: str) -> Optional[dict]:
        """
        Scan the actual disk cache for an ebook and return detailed cache info.
        The disk IS the source of truth (cache-first approach).
        """
        try:
            full_path = self.stream_service._resolve_ebook_path(ebook_path)
            file_hash = self.stream_service._compute_ebook_hash(full_path)[:12]
            ebook_stem = Path(ebook_path).stem
            safe_stem = "".join(
                c if c.isalnum() or c in "-_" else "_" for c in ebook_stem
            )[:50]
            base_cache_dir = (
                settings.AUDIOBOOKS_DIR
                / f"_stream_cache_{safe_stem}_{file_hash}"
            )

            if not base_cache_dir.exists():
                return None

            try:
                ebook_data = self.stream_service.parse_ebook_for_streaming(
                    ebook_path
                )
                total_chunks = len(ebook_data["chunks"])
                chapters = ebook_data.get("chapters", [])
            except Exception as e:
                logger.error("[CACHE] Failed to parse ebook: %s", e)
                total_chunks = 0
                chapters = []

            title = Path(ebook_path).stem
            model_voice_caches = []

            for model_dir in sorted(base_cache_dir.iterdir()):
                if not model_dir.is_dir():
                    continue
                for voice_dir in model_dir.iterdir():
                    if not voice_dir.is_dir():
                        continue

                    model_name = model_dir.name
                    voice_name = voice_dir.name

                    combined_file = voice_dir / f"combined.{settings.AUDIO_FORMAT}"
                    has_combined = combined_file.exists()

                    # Step 1: Read ALL audio files from disk in ONE syscall.
                    audio_files_list = list(
                        voice_dir.glob(f"*.{settings.AUDIO_FORMAT}")
                    )

                    # Step 2: Build set of CAS hashes found on disk (includes stale/orphaned).
                    disk_cas_hashes: set[str] = set()
                    for af in audio_files_list:
                        stem = Path(af).stem
                        if len(stem) == 16 and all(c in '0123456789abcdef' for c in stem):
                            disk_cas_hashes.add(stem)

                    # Step 3: Build set of hashes belonging to CURRENT chunk indices.
                    chunk_hash_set: set[str] = set()
                    for i in range(total_chunks):
                        content_hash = ebook_data["chunks"][i].get("_content_hash")
                        if content_hash:
                            chunk_hash_set.add(content_hash)

                    # Step 4: Intersection — only hashes that are BOTH on disk AND belong to current chunks.
                    matched_hashes = disk_cas_hashes & chunk_hash_set
                    completed = len(matched_hashes)  # correct count for THIS book version

                    is_complete = (
                        completed == total_chunks and total_chunks > 0
                    )

                    # Step 5: Missing chunks — indices whose hash is NOT in disk_cas_hashes.
                    missing_chunks: list[int] = []
                    if total_chunks > 0:
                        for i in range(total_chunks):
                            content_hash = ebook_data["chunks"][i].get("_content_hash")
                            if not content_hash or content_hash not in disk_cas_hashes:
                                missing_chunks.append(i)

                    size_bytes = sum(
                        f.stat().st_size for f in voice_dir.iterdir() if f.is_file()
                    )

                    mv_key = f"{ebook_path}:{model_name}:{voice_name}"
                    is_queue_paused = self.queue._per_item_paused.get(mv_key, False)
                    is_queue_current = (
                        self.queue.current
                        and self.queue.current[0] == ebook_path
                        and self.queue.current[1] == model_name
                        and self.queue.current[2] == voice_name
                    )

                    if is_complete:
                        status = "completed"
                    elif is_queue_paused and (completed > 0 or is_queue_current):
                        status = "paused"
                    elif is_queue_current:
                        status = "in_progress"
                    elif completed > 0:
                        status = "paused"
                    else:
                        status = "not_started"

                    model_voice_caches.append({
                        "model": model_name,
                        "voice": voice_name,
                        "status": status,
                        "completed_chunks": completed,
                        "total_chunks": total_chunks,
                        "progress": (
                            round(completed / total_chunks * 100, 1)
                            if total_chunks > 0
                            else 0
                        ),
                        "is_complete": is_complete,
                        "has_combined": has_combined,
                        "missing_chunks": missing_chunks,
                        "missing_count": len(missing_chunks),
                        "size_bytes": size_bytes,
                        "size_mb": round(size_bytes / (1024 * 1024), 2),
                        "cache_dir": str(voice_dir),
                    })

            return {
                "ebook_path": ebook_path,
                "title": title,
                "total_chunks": total_chunks,
                "has_cache": len(model_voice_caches) > 0,
                "cache_size_mb": round(
                    sum(c["size_bytes"] for c in model_voice_caches)
                    / (1024 * 1024),
                    2,
                ),
                "chapters": chapters,
                "caches": model_voice_caches,
            }

        except FileNotFoundError:
            return None
        except Exception as e:
            logger.error("[CACHE] Failed to get cache info: %s", e)
            return None

    # ------------------------------------------------------------------ #
    #  Generation orchestration                                          #
    # ------------------------------------------------------------------ #

    def start_generation_for_cache(
        self,
        ebook_path: str,
        model: str,
        voice: str,
        background_tasks,
        resume_from: int = 0,
    ) -> dict:
        """Start (or resume) generation for a specific cache entry."""
        key = f"{ebook_path}:{model}:{voice}"

        profile = self.profile_manager.get_profile(ebook_path, model, voice)
        if profile and profile.get("status") == "completed":
            return dict(profile)

        if self.queue.is_in_queue_or_current(ebook_path, model, voice):
            profile = self.profile_manager.get_profile(ebook_path, model, voice)
            return dict(profile) if profile else {}

        try:
            ebook_data = self.stream_service.parse_ebook_for_streaming(ebook_path)
            total_chunks = len(ebook_data["chunks"])
            chapters = ebook_data.get("chapters", [])

            self.profile_manager.update_profile_chapters(
                ebook_path, model, voice, chapters, total_chunks
            )
            self.profile_manager.update_profile_status(
                ebook_path, model, voice, "in_progress"
            )
            self.queue.enqueue(ebook_path, model, voice)

            if len(self.queue.queue) == 1 and not self.queue.current:
                self._start_next_in_queue(background_tasks)

            profile = self.profile_manager.get_profile(ebook_path, model, voice)
            return dict(profile) if profile else {}

        except Exception as e:
            logger.error("[GENERATION] Failed to start: %s", e)
            self.profile_manager.update_profile_status(
                ebook_path, model, voice, "failed", error=str(e)
            )
            raise

    def _start_next_in_queue(
        self, background_tasks, item: Optional[Tuple[str, str, str]] = None
    ):
        """Start a generation item. If item is None, dequeue the next one."""
        if item is None:
            item = self.queue.dequeue()
        if not item:
            return

        ebook_path, model, voice = item
        key = f"{ebook_path}:{model}:{voice}"
        logger.info(
            "[QUEUE] Starting generation: %s/%s/%s",
            ebook_path, model, voice,
        )

        async def _generation_task():
            try:
                ebook_data = self.stream_service.parse_ebook_for_streaming(
                    ebook_path
                )
                total_chunks = len(ebook_data["chunks"])
                chapters = ebook_data.get("chapters", [])

                self.profile_manager.update_profile_chapters(
                    ebook_path, model, voice, chapters, total_chunks
                )
                self.profile_manager.update_profile_status(
                    ebook_path, model, voice, "in_progress"
                )

                cache_dir = self.stream_service.get_cache_dir(
                    ebook_path, model, voice
                )
                cache_dir.mkdir(parents=True, exist_ok=True)

                # CAS: build set of content hashes already cached on disk
                existing_hashed = set()
                if cache_dir.exists():
                    for af in cache_dir.glob(f"*.{settings.AUDIO_FORMAT}"):
                        stem = af.stem
                        # Valid MD5-prefix filename is 16 hex chars (no directory separators)
                        if len(stem) == 16 and all(c in '0123456789abcdef' for c in stem):
                            existing_hashed.add(stem)

                completed = 0
                for i in range(total_chunks):
                    if self.queue._per_item_paused.get(key, False):
                        logger.info(
                            "[QUEUE] Paused at chunk %d/%d", i, total_chunks
                        )
                        self.profile_manager.update_profile_status(
                            ebook_path, model, voice,
                            "paused", completed_chunks=completed,
                        )
                        self.queue.current = None
                        return

                    chunk = ebook_data["chunks"][i]
                    start_char = chunk["start_idx"]
                    end_char = chunk["end_idx"]

                    # CAS: use content hash as stable identifier across ebook versions
                    content_hash = chunk.get("_content_hash")
                    if content_hash and content_hash in existing_hashed:
                        completed += 1
                        continue

                    text = chunk.get("text", chunk.get("display_text", ""))
                    if not text or not text.strip():
                        continue

                    try:
                        # Audiobook generation always saves to disk regardless of streaming setting
                        audio_data = self.stream_service.generate_audio_for_text(
                            text, model, voice,
                            ebook_path=ebook_path,
                            start_char=start_char,
                            end_char=end_char,
                            save_to_disk=True,
                        )
                        # CAS: write with content-hash filename (stable across versions)
                        audio_file = (
                            cache_dir / f"{content_hash}.{settings.AUDIO_FORMAT}"
                        ) if content_hash else (
                            cache_dir / f"audio_{start_char}_{end_char}.{settings.AUDIO_FORMAT}"
                        )
                        with open(audio_file, "wb") as f:
                            f.write(audio_data)

                        completed += 1
                        self.profile_manager.update_profile_status(
                            ebook_path, model, voice,
                            "in_progress",
                            completed_chunks=completed,
                        )
                        await asyncio.sleep(0.5)

                    except Exception as chunk_err:
                        logger.error(
                            "[GENERATION] Chunk %d failed: %s", i, chunk_err
                        )
                        self.profile_manager.update_profile_status(
                            ebook_path, model, voice,
                            "failed",
                            completed_chunks=completed,
                            error=str(chunk_err),
                        )
                        self.queue.current = None
                        return

                self.profile_manager.update_profile_status(
                    ebook_path, model, voice,
                    "completed", completed_chunks=completed,
                )
                logger.info(
                    "[QUEUE] Completed: %s/%s/%s - %d/%d chunks",
                    ebook_path, model, voice, completed, total_chunks,
                )

            except asyncio.CancelledError:
                logger.info(
                    "[QUEUE] Task cancelled for %s/%s/%s",
                    ebook_path, model, voice,
                )
                self.profile_manager.update_profile_status(
                    ebook_path, model, voice, "paused"
                )
            except Exception as e:
                logger.error("[GENERATION] Task failed: %s", e)
                self.profile_manager.update_profile_status(
                    ebook_path, model, voice, "failed", error=str(e)
                )
            finally:
                self.queue.current = None
                if not self.queue.paused and self.queue.queue:
                    self._start_next_in_queue(background_tasks)

        loop = asyncio.get_event_loop()
        task = loop.create_task(_generation_task())
        self.queue.current = (ebook_path, model, voice, task)
        logger.info(
            "[QUEUE] Task created and stored: %s/%s/%s",
            ebook_path, model, voice,
        )

    # ------------------------------------------------------------------ #
    #  Cache deletion                                                    #
    # ------------------------------------------------------------------ #

    def delete_cache(self, ebook_path: str, model: str, voice: str) -> dict:
        """Delete cache files AND profile for a specific model/voice."""
        cache_result = self.stream_service.clear_stream_cache(
            ebook_path, model, voice
        )
        self.profile_manager.delete_profile(ebook_path, model, voice)
        self.queue.remove_from_queue(ebook_path, model, voice)

        return {
            "message": "Cache and profile deleted",
            "deleted_files": cache_result.get("deleted_files", 0),
            "deleted_size_mb": cache_result.get("deleted_size_mb", 0),
        }

    def get_cache_info_for_ebook(self, ebook_path: str) -> Optional[dict]:
        """Alias for get_ebook_cache_info (backward compat)."""
        return self.get_ebook_cache_info(ebook_path)

    # ------------------------------------------------------------------ #
    #  Queue management                                                  #
    # ------------------------------------------------------------------ #

    def get_queue_status(self) -> dict:
        return self.queue.get_queue_status()

    def clear_queue(self):
        self.queue.clear()
        self.queue.paused = False
        self.queue.current = None
        return {"message": "Queue cleared"}


# Global instance
stream_audiobook_service = StreamAudiobookService()
