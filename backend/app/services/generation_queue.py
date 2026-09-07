"""FIFO generation queue for audiobook generation tasks.

Access is guarded by a lock because the queue is mutated both from FastAPI's
threadpool (route handlers) and from asyncio generation tasks.
"""
import asyncio
import logging
import threading
from typing import Optional, Tuple, List, Dict

logger = logging.getLogger(__name__)


class GenerationQueue:
    """FIFO queue for audiobook generation tasks."""

    def __init__(self):
        self.current: Optional[Tuple[str, str, str, asyncio.Task]] = None
        self.queue: List[Tuple[str, str, str]] = []
        self.paused = False
        self._per_item_paused: Dict[str, bool] = {}  # key -> paused state
        self._lock = threading.RLock()

    # -- lock-protected state accessors (used by stream_audiobook_service) --

    def set_current(self, item) -> None:
        with self._lock:
            self.current = item

    def clear_current(self) -> None:
        with self._lock:
            self.current = None

    def set_paused(self, paused: bool) -> None:
        with self._lock:
            self.paused = paused

    def get_paused(self) -> bool:
        with self._lock:
            return self.paused

    def set_item_paused(self, key: str, paused: bool) -> None:
        with self._lock:
            if paused:
                self._per_item_paused[key] = True
            else:
                self._per_item_paused.pop(key, None)

    def get_item_paused(self, key: str) -> bool:
        with self._lock:
            return self._per_item_paused.get(key, False)

    def get_current_tuple(self):
        """Return the raw (ebook_path, model, voice, task) current tuple, if any."""
        with self._lock:
            return self.current

    def get_queue_length(self) -> int:
        with self._lock:
            return len(self.queue)

    def is_in_queue_or_current(self, ebook_path: str, model: str, voice: str) -> bool:
        """Check if this ebook+model+voice combo is already being generated."""
        with self._lock:
            if self.current and self.current[0] == ebook_path and self.current[1] == model and self.current[2] == voice:
                return True
            for item in self.queue:
                if item[0] == ebook_path and item[1] == model and item[2] == voice:
                    return True
            return False

    def get_current(self):
        """Get current generation info."""
        with self._lock:
            if self.current:
                key = self.current[0] + ":" + self.current[1] + ":" + self.current[2]
                return {
                    "ebook_path": self.current[0],
                    "model": self.current[1],
                    "voice": self.current[2],
                    "paused": self._per_item_paused.get(key, self.paused)
                }
            return None

    def get_queue_status(self) -> dict:
        """Get full queue status."""
        with self._lock:
            current_info = self.get_current()
            return {
                "current": current_info,
                "queue": [{"ebook_path": item[0], "model": item[1], "voice": item[2]} for item in self.queue],
                "paused": self.paused
            }

    def enqueue(self, ebook_path: str, model: str, voice: str) -> bool:
        """Add to queue. Returns True if added, False if already in queue/current."""
        with self._lock:
            if self.is_in_queue_or_current(ebook_path, model, voice):
                return False
            self.queue.append((ebook_path, model, voice))
            logger.info("[QUEUE] Enqueued: %s/%s/%s", ebook_path, model, voice)
            return True

    def dequeue(self) -> Optional[Tuple[str, str, str]]:
        """Get next item from queue."""
        with self._lock:
            if self.queue:
                item = self.queue.pop(0)
                logger.info("[QUEUE] Dequeued: %s/%s/%s", item[0], item[1], item[2])
                return item
            return None

    def remove_from_queue(self, ebook_path: str, model: str, voice: str) -> bool:
        """Remove specific item from queue (not current)."""
        with self._lock:
            for i, item in enumerate(self.queue):
                if item[0] == ebook_path and item[1] == model and item[2] == voice:
                    self.queue.pop(i)
                    logger.info("[QUEUE] Removed from queue: %s/%s/%s", ebook_path, model, voice)
                    return True
            return False

    def clear(self):
        """Clear entire queue."""
        with self._lock:
            self.queue.clear()
            logger.info("[QUEUE] Queue cleared")
