"""Tests for service modules."""
import sys
import os
import pytest
import json
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.settings_service import SettingsService, DEFAULT_SETTINGS
from app.services.cache_service import CacheService
from app.services.profile_manager import ProfileManager
from app.services.generation_queue import GenerationQueue
from app.services.stream_service import StreamService


class TestSettingsService:
    def test_load_defaults(self, tmp_path):
        service = SettingsService(
            settings_file=tmp_path / "settings.json"
        )
        settings = service.load_settings()
        assert settings["font_size"] == 16
        assert settings["font_family"] == "system"
        assert settings["show_title"] is True
        assert settings["show_images"] is False

    def test_save_and_load(self, tmp_path):
        service = SettingsService(
            settings_file=tmp_path / "settings.json"
        )
        custom = dict(DEFAULT_SETTINGS)
        custom["font_size"] = 20
        custom["preferred_voice"] = "nova"
        service.save_settings(custom)

        # New instance should load saved settings
        service2 = SettingsService(
            settings_file=tmp_path / "settings.json"
        )
        loaded = service2.load_settings()
        assert loaded["font_size"] == 20
        assert loaded["preferred_voice"] == "nova"

    def test_corrupt_file_returns_defaults(self, tmp_path):
        settings_file = tmp_path / "settings.json"
        settings_file.write_text("not valid json {{{")
        service = SettingsService(settings_file=settings_file)
        settings = service.load_settings()
        assert settings["font_size"] == 16  # default


class TestCacheService:
    def test_get_cache_dir_structure(self, tmp_path):
        service = CacheService(
            audio_format="opus",
            compute_hash_fn=lambda p: "abcdef123456",
        )
        cache_dir = service.get_cache_dir("book.epub", "tts-1", "alloy")
        assert "book" in str(cache_dir)
        assert "tts-1" in str(cache_dir)
        assert "alloy" in str(cache_dir)

    def test_get_base_cache_dir(self, tmp_path):
        service = CacheService(
            audio_format="opus",
            compute_hash_fn=lambda p: "abcdef123456",
        )
        base_dir = service.get_base_cache_dir("book.epub")
        assert "book" in str(base_dir)
        assert "tts-1" not in str(base_dir)  # no model/voice

    def test_clear_nonexistent_cache(self, tmp_path):
        service = CacheService(
            audio_format="opus",
            compute_hash_fn=lambda p: "abcdef123456",
        )
        result = service.clear_stream_cache("nonexistent.epub")
        assert result["deleted_files"] == 0
        assert "No cache found" in result["message"]

    def test_get_cache_status_no_cache(self, tmp_path):
        service = CacheService(
            audio_format="opus",
            compute_hash_fn=lambda p: "abcdef123456",
        )
        status = service.get_cache_status("nonexistent.epub")
        assert status["has_cache"] is False
        assert status["cached_chunks"] == 0


class TestProfileManager:
    def test_create_and_get_profile(self, tmp_path):
        pm = ProfileManager(
            profiles_file=tmp_path / "profiles.json"
        )
        profile = pm.create_profile("book.epub", "tts-1", "alloy")
        assert profile["status"] == "not_started"
        assert profile["model"] == "tts-1"
        assert profile["voice"] == "alloy"

        retrieved = pm.get_profile("book.epub", "tts-1", "alloy")
        assert retrieved is not None
        assert retrieved["status"] == "not_started"

    def test_update_status(self, tmp_path):
        pm = ProfileManager(
            profiles_file=tmp_path / "profiles.json"
        )
        pm.create_profile("book.epub", "tts-1", "alloy")
        pm.update_profile_status(
            "book.epub", "tts-1", "alloy", "in_progress",
            completed_chunks=5, total_chunks=10,
        )
        profile = pm.get_profile("book.epub", "tts-1", "alloy")
        assert profile["status"] == "in_progress"
        assert profile["completed_chunks"] == 5
        assert profile["progress"] == 50.0

    def test_delete_profile(self, tmp_path):
        pm = ProfileManager(
            profiles_file=tmp_path / "profiles.json"
        )
        pm.create_profile("book.epub", "tts-1", "alloy")
        deleted = pm.delete_profile("book.epub", "tts-1", "alloy")
        assert deleted is True
        assert pm.get_profile("book.epub", "tts-1", "alloy") is None

    def test_persistence(self, tmp_path):
        pm1 = ProfileManager(
            profiles_file=tmp_path / "profiles.json"
        )
        pm1.create_profile("book.epub", "tts-1", "alloy")
        del pm1

        pm2 = ProfileManager(
            profiles_file=tmp_path / "profiles.json"
        )
        profile = pm2.get_profile("book.epub", "tts-1", "alloy")
        assert profile is not None


class TestGenerationQueue:
    def test_enqueue_dequeue(self):
        q = GenerationQueue()
        assert q.enqueue("book.epub", "tts-1", "alloy") is True
        assert q.is_in_queue_or_current("book.epub", "tts-1", "alloy") is True
        item = q.dequeue()
        assert item == ("book.epub", "tts-1", "alloy")
        assert q.is_in_queue_or_current("book.epub", "tts-1", "alloy") is False

    def test_duplicate_enqueue(self):
        q = GenerationQueue()
        q.enqueue("book.epub", "tts-1", "alloy")
        assert q.enqueue("book.epub", "tts-1", "alloy") is False

    def test_fifo_order(self):
        q = GenerationQueue()
        q.enqueue("book1.epub", "m1", "v1")
        q.enqueue("book2.epub", "m2", "v2")
        q.enqueue("book3.epub", "m3", "v3")
        assert q.dequeue()[0] == "book1.epub"
        assert q.dequeue()[0] == "book2.epub"
        assert q.dequeue()[0] == "book3.epub"

    def test_remove_from_queue(self):
        q = GenerationQueue()
        q.enqueue("book1.epub", "m1", "v1")
        q.enqueue("book2.epub", "m2", "v2")
        q.remove_from_queue("book1.epub", "m1", "v1")
        assert q.dequeue()[0] == "book2.epub"

    def test_clear(self):
        q = GenerationQueue()
        q.enqueue("book1.epub", "m1", "v1")
        q.enqueue("book2.epub", "m2", "v2")
        q.clear()
        assert len(q.queue) == 0

    def test_get_status(self):
        q = GenerationQueue()
        q.enqueue("book.epub", "tts-1", "alloy")
        status = q.get_queue_status()
        assert status["paused"] is False
        assert len(status["queue"]) == 1
        assert status["current"] is None


class TestStreamService:
    def test_service_creation(self, tmp_path):
        service = StreamService()
        assert service.ebook_parser is not None
        assert service.cache_service is not None
        assert service.settings_service is not None

    def test_load_settings(self, tmp_path):
        service = StreamService()
        settings = service.load_settings()
        assert "font_size" in settings
        assert "preferred_model" in settings

    def test_save_settings(self, tmp_path):
        service = StreamService()
        settings = service.load_settings()
        settings["font_size"] = 24
        service.save_settings(settings)
        assert service.load_settings()["font_size"] == 24

    def test_hash_cache(self, tmp_path):
        """Test that _compute_ebook_hash caches based on mtime."""
        service = StreamService()
        test_file = tmp_path / "test.epub"
        test_file.write_text("hello")

        h1 = service._compute_ebook_hash(test_file)
        h2 = service._compute_ebook_hash(test_file)
        assert h1 == h2  # Same file, same hash


# ========== TESTS FOR FILE MERGE (DUPLICATE UPLOAD) =========

class TestFileManagerMerge:
    """Tests for file merge/duplicate upload functionality."""

    def _create_test_files(self, tmp_path):
        """Create a test ebook directory structure."""
        ebooks_dir = tmp_path / "ebooks"
        storage_dir = tmp_path / "storage"
        audiobooks_dir = tmp_path / "audiobooks"
        stream_cache_dir = storage_dir / "stream_cache"
        
        for d in [ebooks_dir, storage_dir, audiobooks_dir, stream_cache_dir]:
            d.mkdir(parents=True, exist_ok=True)
        
        return ebooks_dir, storage_dir, audiobooks_dir, stream_cache_dir

    def test_find_duplicate_files_same_name(self, tmp_path):
        """Test finding duplicate files with the same name in different directories."""
        from app.core.config import settings
        from app.services.file_manager import FileManager
        
        ebooks_dir, storage_dir, audiobooks_dir, stream_cache_dir = self._create_test_files(tmp_path)
        
        # Patch settings
        settings.EBOOKS_DIR = ebooks_dir
        settings.STORAGE_DIR = storage_dir
        settings.AUDIOBOOKS_DIR = audiobooks_dir
        
        fm = FileManager()
        
        # Create files
        (ebooks_dir / "Book.epub").write_text("content1")
        (ebooks_dir / "subdir").mkdir()
        (ebooks_dir / "subdir" / "Book.epub").write_text("content2")
        
        duplicates = fm.find_duplicate_files("Book.epub")
        assert len(duplicates) == 2
        paths = [d["path"] for d in duplicates]
        assert "Book.epub" in paths
        assert "subdir/Book.epub" in paths

    def test_find_duplicate_files_no_match(self, tmp_path):
        """Test when no duplicate exists."""
        from app.core.config import settings
        from app.services.file_manager import FileManager
        
        ebooks_dir, storage_dir, audiobooks_dir, stream_cache_dir = self._create_test_files(tmp_path)
        
        settings.EBOOKS_DIR = ebooks_dir
        settings.STORAGE_DIR = storage_dir
        settings.AUDIOBOOKS_DIR = audiobooks_dir
        
        fm = FileManager()
        
        (ebooks_dir / "Book.epub").write_text("content")
        
        duplicates = fm.find_duplicate_files("Other.epub")
        assert duplicates == []

    def test_find_duplicate_files_returns_all_matches(self, tmp_path):
        """Test that all files with the same name are returned."""
        from app.core.config import settings
        from app.services.file_manager import FileManager
        
        ebooks_dir, storage_dir, audiobooks_dir, stream_cache_dir = self._create_test_files(tmp_path)
        
        settings.EBOOKS_DIR = ebooks_dir
        settings.STORAGE_DIR = storage_dir
        settings.AUDIOBOOKS_DIR = audiobooks_dir
        
        fm = FileManager()
        
        (ebooks_dir / "Book.epub").write_text("content1")
        (ebooks_dir / "subdir").mkdir()
        (ebooks_dir / "subdir" / "Book.epub").write_text("content2")
        
        # All files with the same name should be returned
        duplicates = fm.find_duplicate_files("Book.epub")
        assert len(duplicates) == 2
        paths = [d["path"] for d in duplicates]
        assert "Book.epub" in paths
        assert "subdir/Book.epub" in paths

    def test_replace_cache_to_new_hash(self, tmp_path):
        """Test replacing cache by renaming directories."""
        from app.core.config import settings
        from app.services.file_manager import FileManager
        from app.utils.path_utils import safe_stem
        
        ebooks_dir, storage_dir, audiobooks_dir, stream_cache_dir = self._create_test_files(tmp_path)
        
        settings.EBOOKS_DIR = ebooks_dir
        settings.STORAGE_DIR = storage_dir
        settings.AUDIOBOOKS_DIR = audiobooks_dir
        
        fm = FileManager()
        
        # Create files FIRST (so we can compute their hashes)
        old_file = ebooks_dir / "Book.epub"
        old_file.write_text("old content")
        new_file = ebooks_dir / "Book2.epub"
        new_file.write_text("new content")
        
        old_hash = fm._compute_file_hash(old_file)[:12]
        new_hash = fm._compute_file_hash(new_file)[:12]
        
        # Use safe_stem to get the correct cache directory names
        old_safe = safe_stem("Book")
        new_safe = safe_stem("Book2")
        
        # Create old parse cache with the actual computed hash
        old_parse = stream_cache_dir / f"{old_safe}_{old_hash}.json"
        old_parse.write_text('{"test": "data"}')
        
        # Create old stream cache
        old_stream = audiobooks_dir / f"_stream_cache_{old_safe}_{old_hash}" / "tts-1" / "alloy"
        old_stream.mkdir(parents=True)
        (old_stream / "audio_0_100.opus").write_bytes(b"audio_data")
        
        result = fm.replace_cache_to_new_hash(
            old_ebook_path="Book.epub",
            new_ebook_path="Book2.epub",
            old_file_hash=old_hash,
            new_file_hash=new_hash,
        )
        
        assert result["parse_cache"] is True
        assert result["stream_cache"] == 1
        assert old_parse.exists() is False
        assert old_stream.exists() is False
        
        # New cache should exist
        new_parse = stream_cache_dir / f"{new_safe}_{new_hash}.json"
        new_stream = audiobooks_dir / f"_stream_cache_{new_safe}_{new_hash}" / "tts-1" / "alloy"
        assert new_parse.exists()
        assert new_stream.exists()

    def test_copy_cache_to_new_hash(self, tmp_path):
        """Test copying cache (preserving old dirs)."""
        from app.core.config import settings
        from app.services.file_manager import FileManager
        from app.utils.path_utils import safe_stem
        
        ebooks_dir, storage_dir, audiobooks_dir, stream_cache_dir = self._create_test_files(tmp_path)
        
        settings.EBOOKS_DIR = ebooks_dir
        settings.STORAGE_DIR = storage_dir
        settings.AUDIOBOOKS_DIR = audiobooks_dir
        
        fm = FileManager()
        
        # Create files FIRST
        old_file = ebooks_dir / "Book.epub"
        old_file.write_text("old content")
        new_file = ebooks_dir / "Book2.epub"
        new_file.write_text("new content")
        
        old_hash = fm._compute_file_hash(old_file)[:12]
        new_hash = fm._compute_file_hash(new_file)[:12]
        
        old_safe = safe_stem("Book")
        new_safe = safe_stem("Book2")
        
        # Create old parse cache with the actual computed hash
        old_parse = stream_cache_dir / f"{old_safe}_{old_hash}.json"
        old_parse.write_text('{"test": "data"}')
        
        # Create old stream cache
        old_stream = audiobooks_dir / f"_stream_cache_{old_safe}_{old_hash}" / "tts-1" / "alloy"
        old_stream.mkdir(parents=True)
        (old_stream / "audio_0_100.opus").write_bytes(b"audio_data")
        
        result = fm.copy_cache_to_new_hash(
            old_ebook_path="Book.epub",
            new_ebook_path="Book2.epub",
            old_file_hash=old_hash,
            new_file_hash=new_hash,
        )
        
        assert result["parse_cache"] is True
        assert result["stream_cache"] == 1
        assert result["bytes_copied"] > 0
        
        # Old cache should still exist
        assert old_parse.exists()
        assert old_stream.exists()
        
        # New cache should also exist
        new_parse = stream_cache_dir / f"{new_safe}_{new_hash}.json"
        new_stream = audiobooks_dir / f"_stream_cache_{new_safe}_{new_hash}" / "tts-1" / "alloy"
        assert new_parse.exists()
        assert new_stream.exists()

    def test_save_uploaded_file_atomic(self, tmp_path):
        """Test atomic file save."""
        from app.core.config import settings
        from app.services.file_manager import FileManager
        from fastapi import UploadFile
        from io import BytesIO
        
        ebooks_dir, storage_dir, audiobooks_dir, stream_cache_dir = self._create_test_files(tmp_path)
        
        settings.EBOOKS_DIR = ebooks_dir
        settings.STORAGE_DIR = storage_dir
        settings.AUDIOBOOKS_DIR = audiobooks_dir
        
        fm = FileManager()
        
        # Create an UploadFile mock
        content = b"test file content"
        upload_file = UploadFile(
            filename="test.epub",
            file=BytesIO(content)
        )
        
        result = fm.save_uploaded_file_atomic(upload_file)
        
        assert str(result) == "test.epub"
        assert (ebooks_dir / "test.epub").exists()
        assert (ebooks_dir / "test.epub").read_bytes() == content
        
        # No temp file should remain
        temp_files = list(ebooks_dir.glob(".tmp_*"))
        assert len(temp_files) == 0

    def test_cleanup_temp_files(self, tmp_path):
        """Test cleanup of old temp files."""
        from app.core.config import settings
        from app.services.file_manager import FileManager
        
        ebooks_dir, storage_dir, audiobooks_dir, stream_cache_dir = self._create_test_files(tmp_path)
        
        settings.EBOOKS_DIR = ebooks_dir
        settings.STORAGE_DIR = storage_dir
        settings.AUDIOBOOKS_DIR = audiobooks_dir
        
        fm = FileManager()
        
        # Create temp files
        (ebooks_dir / ".tmp_abc123_file.epub").write_bytes(b"temp1")
        (ebooks_dir / ".tmp_def456_file.epub").write_bytes(b"temp2")
        (ebooks_dir / "normal.epub").write_bytes(b"normal")
        
        # Old temp file (modify mtime to be old)
        import time
        old_temp = ebooks_dir / ".tmp_abc123_file.epub"
        old_time = time.time() - 7200  # 2 hours ago
        old_temp.touch()
        old_temp.stat()
        os.utime(str(old_temp), (old_time, old_time))
        
        removed = fm.cleanup_temp_files(max_age_seconds=3600)
        
        assert removed == 1
        assert not (ebooks_dir / ".tmp_abc123_file.epub").exists()
        assert (ebooks_dir / ".tmp_def456_file.epub").exists()  # Recent, not removed
        assert (ebooks_dir / "normal.epub").exists()  # Not a temp file

    def test_check_active_generation_returns_none_when_nothing(self, tmp_path):
        """Test that check_active_generation returns None when no generation is active."""
        from app.core.config import settings
        from app.services.file_manager import FileManager
        
        ebooks_dir, storage_dir, audiobooks_dir, stream_cache_dir = self._create_test_files(tmp_path)
        
        settings.EBOOKS_DIR = ebooks_dir
        settings.STORAGE_DIR = storage_dir
        settings.AUDIOBOOKS_DIR = audiobooks_dir
        
        fm = FileManager()
        
        result = fm.check_active_generation("Book.epub")
        assert result is None
