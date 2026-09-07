"""
Shared path resolution and cache directory utilities.

Centralizes the cache directory path pattern used across stream_service,
stream_audiobook_service, download_service, job_manager, and route files to
avoid duplication, and provides a single canonical ebook hashing function.
"""
from pathlib import Path
from typing import Optional, Callable, Dict, Tuple
import hashlib
import re

# Cache directory naming constants.
STREAM_CACHE_PREFIX = "_stream_cache_"
_CACHE_DIR_RE = re.compile(r'^_stream_cache_(.+?)_([a-fA-F0-9]{8,})$')
_PARSE_CACHE_RE = re.compile(r'^(.+?)_([a-fA-F0-9]{8,})$')
_AUDIO_EXTS = ("opus", "m4a", "mp3")

# In-memory hash cache keyed by (path, mtime) to avoid re-reading large ebooks.
_hash_cache: Dict[str, Tuple[float, str]] = {}


def _normalize(name: str) -> str:
    """Lowercase and strip everything but alphanumerics for fuzzy matching."""
    return re.sub(r'[^a-z0-9]', '', (name or '').lower())


def safe_stem(ebook_stem: str, max_len: int = 50) -> str:
    """Sanitize ebook stem for use as filesystem component."""
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in ebook_stem)[:max_len]


def compute_ebook_hash(file_path: Path) -> str:
    """Compute a content-based MD5 hash of the full file contents.

    Results are cached by (path, mtime) so repeated lookups are cheap while still
    invalidating when the file changes. Returns an empty string on failure.
    """
    key = str(file_path)
    try:
        mtime = file_path.stat().st_mtime
    except OSError:
        return ""

    cached = _hash_cache.get(key)
    if cached is not None and cached[0] == mtime:
        return cached[1]

    try:
        h = hashlib.md5()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                h.update(chunk)
        digest = h.hexdigest()
    except OSError:
        return ""

    _hash_cache[key] = (mtime, digest)
    return digest


def _hash_from_metadata(file_path: Path) -> str:
    """Legacy alias: 12-char prefix of the content hash (kept for callers/tests)."""
    digest = compute_ebook_hash(file_path)
    return digest[:12] if digest else "unknown"


def _resolve_ebook_path(ebook_path: str, base_dir: Path) -> Path:
    """
    Resolve ebook path relative to the project's ebooks directory.
    base_dir is AUDIOBOOKS_DIR, so ebooks dir = base_dir.parent / "ebooks".
    """
    return (base_dir.parent / "ebooks" / ebook_path).resolve()


def _count_audio_files(dir_path: Path) -> int:
    """Count cached audio files under a cache dir (any model/voice depth)."""
    return sum(
        1 for p in dir_path.rglob("*")
        if p.is_file() and p.suffix.lstrip(".").lower() in _AUDIO_EXTS
    )


def find_audio_cache_dir(ebook_stem: str, cache_base_dir: Path) -> Optional[Path]:
    """Find the audio-cache directory for an ebook stem.

    Matches `_stream_cache_<stem>_<hash>/` by normalized stem, preferring the
    directory with the most cached audio files when multiple match.
    """
    norm_ebook = _normalize(ebook_stem)
    matches: list[Path] = []
    for f in cache_base_dir.glob(f"{STREAM_CACHE_PREFIX}*"):
        if not f.is_dir():
            continue
        m = _CACHE_DIR_RE.match(f.name)
        if not m or _normalize(m.group(1)) != norm_ebook:
            continue
        matches.append(f)

    if not matches:
        return None
    # Prefer the directory with actual audio files; break ties by name for stability.
    return max(matches, key=lambda d: (_count_audio_files(d), str(d)))


def find_stream_cache_match(ebook_stem: str, cache_base_dir: Path) -> Optional[Path]:
    """Find the best-matching parsed-text cache JSON for an ebook stem.

    Parse-cache files live in ``storage/stream_cache/<stem>_<hash>.json``. Exact
    normalized-name matches are preferred; among them, the file with the most
    chunks wins (latest version).
    """
    norm_ebook = _normalize(ebook_stem)
    stream_cache_dir = cache_base_dir.parent / "stream_cache"
    if not stream_cache_dir.exists():
        return None

    exact: list[Path] = []
    for f in sorted(stream_cache_dir.glob("*.json")):
        if "_with_images" in f.stem:
            continue
        m = _PARSE_CACHE_RE.match(f.stem.strip())
        if not m or _normalize(m.group(1)) != norm_ebook:
            continue
        exact.append(f)

    if not exact:
        return None

    best: Optional[Path] = None
    best_chunks = -1
    for candidate in exact:
        try:
            with open(candidate) as f:
                data = __import__("json").load(f)
            chunks_count = len(data.get("chunks", []))
        except Exception:
            chunks_count = -1
        if chunks_count > best_chunks:
            best = candidate
            best_chunks = chunks_count
    return best or exact[0]


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

    `compute_hash_fn` defaults to the canonical full-content hash. If the canonical
    directory does not exist but a same-stem cache directory does (e.g. created with
    a legacy hash), the existing directory is returned so cached audio stays visible.
    """
    ebook_stem = Path(ebook_path).stem
    safe = safe_stem(ebook_stem)

    full_path = _resolve_ebook_path(ebook_path, base_dir)
    if compute_hash_fn is not None:
        file_hash = compute_hash_fn(full_path)[:12]
    else:
        file_hash = compute_ebook_hash(full_path)[:12]

    canonical = base_dir / f"{STREAM_CACHE_PREFIX}{safe}_{file_hash}"
    if canonical.exists():
        return canonical / model / voice

    # Backward-compatible lookup: reuse an existing same-stem cache dir if present.
    existing = find_audio_cache_dir(ebook_stem, base_dir)
    if existing is not None:
        return existing / model / voice

    return canonical / model / voice


def resolve_base_cache_dir(
    base_dir: Path,
    ebook_path: str,
    compute_hash_fn: Optional[Callable[[Path], str]] = None,
) -> Path:
    """
    Build the base cache directory (parent of model/voice subdirs):
        {AUDIOBOOKS_DIR}/_stream_cache_{safe_stem}_{hash}/

    Falls back to an existing same-stem cache directory for backward compatibility.
    """
    ebook_stem = Path(ebook_path).stem
    safe = safe_stem(ebook_stem)

    full_path = _resolve_ebook_path(ebook_path, base_dir)
    if compute_hash_fn is not None:
        file_hash = compute_hash_fn(full_path)[:12]
    else:
        file_hash = compute_ebook_hash(full_path)[:12]

    canonical = base_dir / f"{STREAM_CACHE_PREFIX}{safe}_{file_hash}"
    if canonical.exists():
        return canonical

    existing = find_audio_cache_dir(ebook_stem, base_dir)
    return existing if existing is not None else canonical


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
    # Only strip leading slashes; preserve meaningful boundary characters
    # (e.g. a dir named "SpyXFamily " should stay as-is, not become "SpyXFamily")
    cleaned = path.lstrip("/")
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
