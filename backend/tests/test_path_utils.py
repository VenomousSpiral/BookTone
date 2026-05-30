"""Tests for shared path utilities."""
import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.utils.path_utils import (
    safe_stem,
    sanitize_ebook_path,
    resolve_cache_dir,
    resolve_base_cache_dir,
    resolve_combined_audio_path,
    _hash_from_metadata,
)
from pathlib import Path


class TestSafeStem:
    def test_alphanumeric(self):
        assert safe_stem("my_ebook") == "my_ebook"

    def test_special_chars_becomes_underscore(self):
        assert safe_stem("my ebook (2024)!") == "my_ebook__2024__"

    def test_max_length(self):
        long_name = "a" * 100
        result = safe_stem(long_name)
        assert len(result) == 50

    def test_empty(self):
        assert safe_stem("") == ""


class TestSanitizeEbookPath:
    def test_normal_path(self):
        assert sanitize_ebook_path("folder/book.epub") == "folder/book.epub"

    def test_leading_slash(self):
        assert sanitize_ebook_path("/folder/book.epub") == "folder/book.epub"

    def test_double_dot_traversal(self):
        assert sanitize_ebook_path("../etc/passwd") == "etc/passwd"

    def test_multiple_traversal(self):
        assert sanitize_ebook_path("../../../../etc/shadow") == "etc/shadow"

    def test_traversal_in_middle(self):
        # '..' pops the previous component
        assert sanitize_ebook_path("folder/../secret/file.txt") == "secret/file.txt"

    def test_whitespace_only(self):
        assert sanitize_ebook_path("   ") == ""

    def test_empty_string(self):
        assert sanitize_ebook_path("") == ""

    def test_mixed_traversal_and_normal(self):
        # '.' is skipped, '..' pops 'books', then 'docs/book.epub' remains
        assert sanitize_ebook_path("./books/../docs/book.epub") == "docs/book.epub"


class TestHashFromMetadata:
    def test_returns_12_char_hex(self, tmp_path):
        test_file = tmp_path / "test.txt"
        test_file.write_text("hello")
        h = _hash_from_metadata(test_file)
        assert len(h) == 12
        assert all(c in "0123456789abcdef" for c in h)

    def test_same_file_same_hash(self, tmp_path):
        test_file = tmp_path / "test.txt"
        test_file.write_text("hello")
        h1 = _hash_from_metadata(test_file)
        h2 = _hash_from_metadata(test_file)
        assert h1 == h2

    def test_different_content_different_hash(self, tmp_path):
        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.txt"
        f1.write_text("hello")
        f2.write_text("world")
        assert _hash_from_metadata(f1) != _hash_from_metadata(f2)

    def test_nonexistent_file(self):
        h = _hash_from_metadata(Path("/nonexistent/file.txt"))
        assert h == "unknown"


class TestResolveCacheDir:
    def test_basic_structure(self, tmp_path):
        # Use mtime:size hash since we can't easily mock _compute_ebook_hash
        test_file = tmp_path / "test.epub"
        test_file.write_text("dummy")

        result = resolve_cache_dir(
            tmp_path / "audiobooks",
            "test.epub",
            "tts-1",
            "alloy",
            compute_hash_fn=lambda p: _hash_from_metadata(p),
        )

        assert "test" in str(result)
        assert "tts-1" in str(result)
        assert "alloy" in str(result)

    def test_base_cache_dir(self, tmp_path):
        test_file = tmp_path / "test.epub"
        test_file.write_text("dummy")

        result = resolve_base_cache_dir(
            tmp_path / "audiobooks",
            "test.epub",
            compute_hash_fn=lambda p: _hash_from_metadata(p),
        )

        assert "test" in str(result)
        assert "tts-1" not in str(result)  # no model/voice


class TestResolveCombinedAudioPath:
    def test_includes_format(self, tmp_path):
        test_file = tmp_path / "test.epub"
        test_file.write_text("dummy")

        result = resolve_combined_audio_path(
            tmp_path / "audiobooks",
            "test.epub",
            "tts-1",
            "alloy",
            "opus",
            compute_hash_fn=lambda p: _hash_from_metadata(p),
        )

        assert str(result).endswith("combined.opus")
