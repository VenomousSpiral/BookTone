"""
Shared path resolution and cache directory utilities.

Centralizes the cache directory path pattern used across stream_service,
stream_audiobook_service, and route files to avoid duplication.
"""
from pathlib import Path
from typing import Optional, Callable


def safe_stem(ebook_stem: str, max_len: int = 50) -> str:
    """Sanitize ebook stem for use as filesystem component."""
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in ebook_stem)[:max_len]


def _resolve_ebook_path(ebook_path: str, base_dir: Path) -> Path:
    """
    Resolve ebook path relative to the project's ebooks directory.
    base_dir is AUDIOBOOKS_DIR, so ebooks dir = base_dir.parent / "ebooks".
    """
    return (base_dir.parent / "ebooks" / ebook_path).resolve()


def _hash_from_metadata(file_path: Path) -> str:
    """Compute a content-based hash from the full file contents.

    Uses MD5 of actual bytes (not mtime/size) so cache directories are stable
    across file re-downloads, touches, and metadata changes. This matches the
    hashing approach used by stream_service._compute_ebook_hash() and ensures
    that resolve_cache_dir(), get_ebook_cache_info(), and _generation_task all
    point to the same audiobook directory.
    """
    import hashlib
    try:
        with open(file_path, "rb") as f:
            return hashlib.md5(f.read()).hexdigest()[:12]
    except Exception:
        return "unknown"


def resolve_cache_dir(
    base_dir: Path,
    ebook_path: str,
    model: str,
    voice: str,
    compute_hash_fn: Optional[Callable[[Path], str]] = None,
) -> Path:
    """
    Build the stream cache directory path:
        {AUDIOBOOKS_DIR}/_stream_cache_{safe_stem}_{hash}/{model}/{voice}/

    Parameters
    ----------
    base_dir : Path
        The AUDIOBOOKS_DIR from settings.
    ebook_path : str
        Relative or absolute path to the ebook file.
    model : str
        TTS model identifier.
    voice : str
        Voice identifier.
    compute_hash_fn : callable, optional
        A function(ebook_path: Path) -> str that returns the MD5 hash.
        If None, the hash is computed from mtime:size.

    Returns
    -------
    Path to the cache directory.
    """
    ebook_stem = Path(ebook_path).stem
    safe = safe_stem(ebook_stem)

    full_path = _resolve_ebook_path(ebook_path, base_dir)
    if compute_hash_fn is not None:
        file_hash = compute_hash_fn(full_path)[:12]
    else:
        file_hash = _hash_from_metadata(full_path)[:12]

    return base_dir / f"_stream_cache_{safe}_{file_hash}" / model / voice


def resolve_base_cache_dir(
    base_dir: Path,
    ebook_path: str,
    compute_hash_fn: Optional[Callable[[Path], str]] = None,
) -> Path:
    """
    Build the base cache directory (parent of model/voice subdirs):
        {AUDIOBOOKS_DIR}/_stream_cache_{safe_stem}_{hash}/
    """
    ebook_stem = Path(ebook_path).stem
    safe = safe_stem(ebook_stem)

    full_path = _resolve_ebook_path(ebook_path, base_dir)
    if compute_hash_fn is not None:
        file_hash = compute_hash_fn(full_path)[:12]
    else:
        file_hash = _hash_from_metadata(full_path)[:12]

    return base_dir / f"_stream_cache_{safe}_{file_hash}"


def resolve_combined_audio_path(
    base_dir: Path,
    ebook_path: str,
    model: str,
    voice: str,
    audio_format: str,
    compute_hash_fn: Optional[Callable[[Path], str]] = None,
) -> Path:
    """
    Build the combined audio file path for download:
        {AUDIOBOOKS_DIR}/_stream_cache_{safe_stem}_{hash}/{model}/{voice}/combined.{format}

    Parameters
    ----------
    base_dir : Path
        The AUDIOBOOKS_DIR from settings.
    ebook_path : str
        Relative or absolute path to the ebook file.
    model : str
        TTS model identifier.
    voice : str
        Voice identifier.
    audio_format : str
        Audio format (e.g., 'opus', 'mp3').
    compute_hash_fn : callable, optional
        A function(ebook_path: Path) -> str that returns the MD5 hash.

    Returns
    -------
    Path to the combined audio file.
    """
    cache_dir = resolve_cache_dir(
        base_dir, ebook_path, model, voice,
        compute_hash_fn=compute_hash_fn
    )
    return cache_dir / f"combined.{audio_format}"


def sanitize_ebook_path(path: str) -> str:
    """
    Sanitize a user-supplied ebook path to prevent path traversal.
    Properly resolves '..' by popping previous path components.

    Returns
    -------
    str: Sanitized path safe for filesystem operations.
    """
    # Remove leading slashes and whitespace
    cleaned = path.strip().lstrip("/")
    # Properly resolve '..' components
    parts = []
    for part in cleaned.split("/"):
        if part == "" or part == ".":
            continue
        elif part == "..":
            # Pop the previous component if any
            if parts:
                parts.pop()
        else:
            parts.append(part)
    return "/".join(parts)
