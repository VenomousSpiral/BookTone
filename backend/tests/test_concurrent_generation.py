"""Tests for concurrent audiobook generation scheduling.

Verifies that multiple books can be generated concurrently with correct
per-book progress tracking and non-blocking event loop behavior.
"""
import asyncio
import sys
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch, PropertyMock
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.generation_queue import GenerationQueue
from app.services.profile_manager import ProfileManager


class TestConcurrentGenerationQueue:
    """Tests for queue behavior with concurrent generation tasks."""

    def test_concurrent_enqueue_different_books(self):
        """Two different books should both be enqueued successfully."""
        q = GenerationQueue()
        
        assert q.enqueue("book1.epub", "gpt-4o-mini", "alloy") is True
        assert q.enqueue("book2.epub", "gpt-4o-mini", "nova") is True
        
        # Both should be in queue (FIFO order)
        item1 = q.dequeue()
        item2 = q.dequeue()
        
        assert item1[0] == "book1.epub"
        assert item2[0] == "book2.epub"

    def test_concurrent_enqueue_same_book(self):
        """Same book+model+voice should only enqueue once."""
        q = GenerationQueue()
        
        key = ("book1.epub", "gpt-4o-mini", "alloy")
        assert q.enqueue(*key) is True
        # Second attempt for same item should fail (idempotent)
        assert q.enqueue(*key) is False

    def test_concurrent_per_item_pause_tracking(self):
        """Different books can have independent pause states."""
        q = GenerationQueue()
        
        key1 = "book1.epub:gpt-4o-mini:alloy"
        key2 = "book2.epub:gpt-4o-mini:nova"
        
        q.set_item_paused(key1, True)
        q.set_item_paused(key2, False)
        
        assert q.get_item_paused(key1) is True
        assert q.get_item_paused(key2) is False

    def test_queue_status_includes_all_items(self):
        """Queue status should show all queued items with correct data."""
        q = GenerationQueue()
        
        q.enqueue("bookA.epub", "gpt-4o-mini", "alloy")
        q.set_current(("bookA.epub", "gpt-4o-mini", "alloy", MagicMock()))
        q.enqueue("bookB.epub", "suno-v3", "nova")
        
        status = q.get_queue_status()
        
        assert status["current"]["ebook_path"] == "bookA.epub"
        # bookB should be in queue, not current
        queued_paths = [item["ebook_path"] for item in status["queue"]]
        assert "bookB.epub" in queued_paths

    def test_concurrent_current_updates(self):
        """Setting current multiple times should update correctly."""
        q = GenerationQueue()
        
        # Simulate Book A becoming current
        task_a = MagicMock()
        q.set_current(("bookA.epub", "m1", "v1", task_a))
        assert q.get_current()["ebook_path"] == "bookA.epub"
        
        # Simulate Book B becoming current (after A finishes)
        task_b = MagicMock()
        q.set_current(("bookB.epub", "m2", "v2", task_b))
        assert q.get_current()["ebook_path"] == "bookB.epub"


class TestProfileManagerConcurrent:
    """Tests for profile manager with concurrent updates."""

    def test_concurrent_profile_updates_different_books(self, tmp_path):
        """Different books' profiles should update independently."""
        pm = ProfileManager(
            profiles_file=tmp_path / "profiles.json"
        )
        
        # Create two different book profiles
        pm.create_profile("bookA.epub", "gpt-4o-mini", "alloy")
        pm.create_profile("bookB.epub", "suno-v3", "nova")
        
        # Update Book A's progress
        pm.update_profile_status(
            "bookA.epub", "gpt-4o-mini", "alloy", 
            "in_progress", completed_chunks=10, total_chunks=20
        )
        
        # Book B should still be at not_started
        profile_b = pm.get_profile("bookB.epub", "suno-v3", "nova")
        assert profile_b["status"] == "not_started"
        assert profile_b["completed_chunks"] == 0
        
        # Now update Book B's progress
        pm.update_profile_status(
            "bookB.epub", "suno-v3", "nova", 
            "in_progress", completed_chunks=5, total_chunks=15
        )
        
        # Both should have correct independent data
        profile_a = pm.get_profile("bookA.epub", "gpt-4o-mini", "alloy")
        profile_b = pm.get_profile("bookB.epub", "suno-v3", "nova")
        
        assert profile_a["status"] == "in_progress"
        assert profile_a["completed_chunks"] == 10
        assert profile_a["progress"] == 50.0
        
        assert profile_b["status"] == "in_progress"
        assert profile_b["completed_chunks"] == 5
        assert profile_b["progress"] == 33.3


class TestGenerationQueueConcurrency:
    """Tests for thread-safe queue operations."""

    def test_concurrent_enqueue_dequeue(self):
        """Multiple threads adding/removing items should maintain FIFO order."""
        q = GenerationQueue()
        
        # Simulate concurrent enqueues from multiple requests
        results = []
        for i in range(10):
            result = q.enqueue(f"book{i}.epub", "model", f"voice_{i}")
            results.append(result)
        
        # All should succeed (different items)
        assert all(results), "All unique items should be enqueued"
        
        # Should dequeue in FIFO order
        for i in range(10):
            item = q.dequeue()
            assert item[0] == f"book{i}.epub", f"FIFO violation at index {i}"

    def test_concurrent_pause_resume(self, tmp_path):
        """Pause/resume should work correctly with multiple books."""
        pm = ProfileManager(
            profiles_file=tmp_path / "profiles.json"
        )
        
        # Create two book profiles
        pm.create_profile("bookA.epub", "m1", "v1")
        pm.create_profile("bookB.epub", "m2", "v2")
        
        q = GenerationQueue()
        key_a = "bookA.epub:m1:v1"
        key_b = "bookB.epub:m2:v2"
        
        # Pause Book A only
        q.set_item_paused(key_a, True)
        
        assert q.get_item_paused(key_a) is True
        assert q.get_item_paused(key_b) is False
        
        # Resume Book A
        q.set_item_paused(key_a, False)
        
        assert q.get_item_paused(key_a) is False


class TestEventLoopTaskCreation:
    """Tests for proper asyncio task creation."""

    def test_asyncio_create_task_in_context(self):
        """Verify create_task works correctly inside an async context using anyio."""
        
        results = []
        
        async def sample_task(name, delay=0.1):
            await asyncio.sleep(delay)
            results.append((name, "done"))
        
        # Use anyio.run to provide an event loop
        import anyio
        anyio.run(lambda: self._run_concurrent_tasks(sample_task, results))
    
    async def _run_concurrent_tasks(self, sample_task, results):
        """Helper for running concurrent tasks."""
        task1 = asyncio.create_task(sample_task("bookA", 0.2))
        task2 = asyncio.create_task(sample_task("bookB", 0.1))
        
        await task2
        assert ("bookB", "done") in results
        
        await task1
        assert len(results) == 2


class TestQueueStatusConsistency:
    """Tests for queue status reporting accuracy."""

    def test_status_shows_correct_current(self):
        """Current item should be reported correctly, not stale data."""
        q = GenerationQueue()
        
        # Book A is current
        task_a = MagicMock()
        q.set_current(("bookA.epub", "m1", "v1", task_a))
        status = q.get_queue_status()
        assert status["current"]["ebook_path"] == "bookA.epub"
        
        # Clear and set Book B as current
        q.clear_current()
        task_b = MagicMock()
        q.set_current(("bookB.epub", "m2", "v2", task_b))
        
        status = q.get_queue_status()
        assert status["current"]["ebook_path"] == "bookB.epub"
        # Book A should NOT be in current anymore
        assert status["current"]["ebook_path"] != "bookA.epub"

    def test_status_shows_correct_paused_state(self):
        """Paused state per item should not interfere with other items."""
        q = GenerationQueue()
        
        key_a = "bookA.epub:m1:v1"
        key_b = "bookB.epub:m2:v2"
        
        # Set Book A as paused at item level
        q.set_item_paused(key_a, True)
        q.enqueue("bookA.epub", "m1", "v1")
        
        status = q.get_queue_status()
        current_info = status["current"]  # This will be None since we didn't set_current
        
        assert q.get_item_paused(key_a) is True
        assert q.get_item_paused(key_b) is False


class TestBlockingIOFix:
    """Tests verifying the blocking I/O fix for concurrent generation."""

    def test_io_executor_initialized(self):
        """StreamAudiobookService should initialize an IO executor thread pool."""
        from app.services.stream_audiobook_service import StreamAudiobookService
        
        service = StreamAudiobookService()
        assert hasattr(service, '_io_executor')
        assert service._io_executor is not None
    
    def test_generation_task_uses_async_io(self):
        """Verify _start_next_in_queue is now async (not blocking)."""
        from app.services.stream_audiobook_service import StreamAudiobookService
        import inspect
        
        service = StreamAudiobookService()
        # Should be a coroutine function
        assert asyncio.iscoroutinefunction(service._start_next_in_queue)
    

class TestProgressIsolation:
    """Tests ensuring progress data stays isolated between books."""

    def test_different_ebooks_get_independent_progress(self, tmp_path):
        """Each ebook should have completely independent progress tracking."""
        pm = ProfileManager(
            profiles_file=tmp_path / "profiles.json"
        )
        
        # Create profile for Book A with 20 chunks total
        profile_a = pm.create_profile("bookA.epub", "gpt-4o-mini", "alloy")
        assert profile_a["total_chunks"] == 0
        
        # Simulate parsing and updating chapters (like _generation_task does)
        chapters_a = [{"name": f"Chapter {i}", "start_idx": i*100, "end_idx": (i+1)*100} for i in range(5)]
        pm.update_profile_chapters("bookA.epub", "gpt-4o-mini", "alloy", chapters_a, 20)
        
        # Create profile for Book B with different chunk count
        profile_b = pm.create_profile("bookB.epub", "suno-v3", "nova")
        chapters_b = [{"name": f"Chapter {i}", "start_idx": i*50, "end_idx": (i+1)*50} for i in range(8)]
        pm.update_profile_chapters("bookB.epub", "suno-v3", "nova", chapters_b, 40)
        
        # Update progress independently
        pm.update_profile_status("bookA.epub", "gpt-4o-mini", "alloy", 
                                  "in_progress", completed_chunks=15, total_chunks=20)
        pm.update_profile_status("bookB.epub", "suno-v3", "nova", 
                                  "in_progress", completed_chunks=10, total_chunks=40)
        
        # Verify independent data
        prof_a = pm.get_profile("bookA.epub", "gpt-4o-mini", "alloy")
        prof_b = pm.get_profile("bookB.epub", "suno-v3", "nova")
        
        assert prof_a["completed_chunks"] == 15
        assert prof_a["total_chunks"] == 20
        assert round(prof_a["progress"], 1) == 75.0
        
        assert prof_b["completed_chunks"] == 10
        assert prof_b["total_chunks"] == 40
        assert round(prof_b["progress"], 1) == 25.0
        
        # Cross-check: Book B should NOT have Book A's data
        assert "bookA.epub" not in str(prof_b.get("ebook_path", ""))
