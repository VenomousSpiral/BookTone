"""
Input validation utilities for API routes.

Provides path traversal protection for user-supplied paths.
"""
from fastapi import HTTPException
from typing import Optional

from app.utils.path_utils import sanitize_ebook_path


def validate_ebook_path(ebook_path: str) -> str:
    """
    Validate and sanitize an ebook path parameter.
    Raises HTTPException 400 on invalid input.

    Path traversal attempts (where '..' escapes the root) are rejected.
    """
    if not ebook_path or not ebook_path.strip():
        raise HTTPException(status_code=400, detail="ebook_path is required")

    cleaned = sanitize_ebook_path(ebook_path)

    if not cleaned:
        raise HTTPException(status_code=400, detail="Invalid ebook path")

    # Reject if original path contained '..' that would escape root
    # (sanitize_ebook_path pops components, so if the result is shorter
    # than expected, it means traversal happened)
    original_parts = ebook_path.strip().lstrip("/").split("/")
    has_traversal = any(p == ".." for p in original_parts)
    if has_traversal:
        raise HTTPException(
            status_code=400,
            detail="Path traversal not allowed",
        )

    return cleaned


def get_sanitized_ebook_path(ebook_path: str) -> str:
    """FastAPI dependency for sanitizing ebook_path query parameters."""
    return validate_ebook_path(ebook_path)
