"""Tests for input validation utilities."""
import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.utils.validators import validate_ebook_path
from fastapi import HTTPException


class TestValidateEbookPath:
    def test_valid_path(self):
        result = validate_ebook_path("folder/book.epub")
        assert result == "folder/book.epub"

    def test_empty_string_raises(self):
        with pytest.raises(HTTPException) as exc_info:
            validate_ebook_path("")
        assert exc_info.value.status_code == 400

    def test_whitespace_only_raises(self):
        with pytest.raises(HTTPException) as exc_info:
            validate_ebook_path("   ")
        assert exc_info.value.status_code == 400

    def test_none_raises(self):
        with pytest.raises(HTTPException) as exc_info:
            validate_ebook_path(None)
        assert exc_info.value.status_code == 400

    def test_path_traversal_blocked(self):
        with pytest.raises(HTTPException) as exc_info:
            validate_ebook_path("../etc/passwd")
        assert exc_info.value.status_code == 400
        assert "traversal" in exc_info.value.detail.lower()

    def test_leading_slash_stripped(self):
        result = validate_ebook_path("/books/book.epub")
        assert result == "books/book.epub"

    def test_normal_path_preserved(self):
        result = validate_ebook_path("my books/favorite.epub")
        assert result == "my books/favorite.epub"
