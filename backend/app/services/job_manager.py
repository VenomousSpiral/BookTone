"""Persistent job state store for download conversions.

Jobs are stored as JSON-per-job files plus an in-memory cache for fast polling.
All in-memory and on-disk access is guarded by a lock, and files are written
atomically so readers never observe a partially-written job.
"""
import json
import hashlib
import logging
import os
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional, Union

logger = logging.getLogger(__name__)


class JobManager:
    """In-memory + on-disk job state store."""

    def __init__(self, jobs_dir: Union[str, Path], cache_base_dir: Optional[Union[str, Path]] = None):
        # Go up 4 levels from job_manager.py (services → app → backend → project root)
        _default_jobs = Path(__file__).resolve().parent.parent.parent.parent / "storage" / "download_jobs"
        self.jobs_dir = Path(jobs_dir) if jobs_dir else _default_jobs
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        # Default to project storage/audiobooks if not provided
        default_cache = Path(__file__).resolve().parent.parent.parent.parent / "storage" / "audiobooks"
        self.cache_base_dir = Path(cache_base_dir) if cache_base_dir else default_cache
        # In-memory cache for fast polling lookups
        self._cache: dict[str, dict] = {}
        self._lock = threading.Lock()

    def _job_path(self, job_id: str) -> Path:
        return self.jobs_dir / f"{job_id}.json"

    def _write_job(self, job_id: str, job: dict) -> None:
        """Atomically persist a job (temp file + rename)."""
        target = self._job_path(job_id)
        tmp = target.with_suffix(".json.tmp")
        with open(tmp, "w") as f:
            json.dump(job, f, indent=2)
        os.replace(tmp, target)

    @staticmethod
    def make_job_id(ebook_path: str, model_name: str, voice: str, format_type: str) -> str:
        raw = f"{ebook_path}:{model_name}:{voice}:{format_type}"
        # Include format in job ID so different formats get separate jobs
        return hashlib.md5(raw.encode()).hexdigest()[:16]

    @staticmethod
    def _find_audio_cache_dir(ebook_stem: str, cache_base_dir: Path) -> Optional[Path]:
        """Find the exact audio-cache directory for an ebook stem (shared helper)."""
        from app.utils.path_utils import find_audio_cache_dir
        return find_audio_cache_dir(ebook_stem, cache_base_dir)

    def create_job(
        self, ebook_path: str, model_name: str, voice: str, format_type: str,
    ) -> dict:
        """Create a new download job in 'pending' state."""
        # Check if already done (combined file exists) — skip-if-ready logic
        combined = self._resolve_output(ebook_path, model_name, voice, format_type)

        # Find matching cache dir to count audio files
        cache_voice_files_count = 0
        try:
            potential_dirs: list[Path] = []
            match = self._find_audio_cache_dir(
                Path(ebook_path).stem,
                Path(self.cache_base_dir),
            )
            if match is not None:
                potential_dirs.append(match)

            for base_cache in (potential_dirs or []):
                model_dirs = list((base_cache / model_name).glob(voice)) if (base_cache / model_name).exists() else []
                for voice_dir in model_dirs:
                    cache_voice_files_count += len(list(voice_dir.glob("*.opus"))) + \
                                               len(list(voice_dir.glob("*.mp3"))) + \
                                               len(list(voice_dir.glob("*.m4a")))
        except Exception as e:
            logger.debug("[JOB] Could not count audio files for job creation: %s", e)

        status = "ready" if combined.exists() else "pending"

        job = {
            "job_id": self.make_job_id(ebook_path, model_name, voice, format_type),
            "ebook_path": ebook_path,
            "model_name": model_name,
            "voice": voice,
            "format_type": format_type,
            "status": status,
            "progress_pct": 100 if status == "ready" else 0,
            "message": "Ready to download" if status == "ready" else "Pending",
            "output_file": str(combined) if combined.exists() and combined.stat().st_size > 0 else None,
            "audio_files_count": cache_voice_files_count if status == "pending" else 0,
            "error_message": None,
            "created_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        }

        with self._lock:
            self._cache[job["job_id"]] = job
            self._write_job(job["job_id"], job)
        return dict(job)

    def _resolve_output(
        self, ebook_path: str, model_name: str, voice: str, format_type: str,
    ) -> Path:
        """Build output path matching resolve_combined_audio_path."""
        safe_stem = "".join(c if c.isalnum() or c in "-_" else "_" for c in Path(ebook_path).stem)[:50]

        # Find the exact cache directory (avoids matching similar-named books)
        match_dir = self._find_audio_cache_dir(
            Path(ebook_path).stem,
            Path(self.cache_base_dir),
        )

        if match_dir is not None and (match_dir / model_name / voice).exists():
            # Extract hash from the matched directory name
            m = re.search(r'_([a-fA-F0-9]{8,})$', match_dir.name)
            file_hash = m.group(1) if m else ""
        else:
            file_hash = ""  # No matching cache found yet

        return (
            self.cache_base_dir
            / f"_stream_cache_{safe_stem}_{file_hash}"
            / model_name / voice / f"combined.{format_type}"
        )

    def get_job(self, job_id: str) -> Optional[dict]:
        """Get a job by ID."""
        with self._lock:
            if job_id in self._cache:
                return dict(self._cache[job_id])

            jp = self._job_path(job_id)
            if not jp.exists():
                return None
            try:
                with open(jp) as f:
                    data = json.load(f)
                self._cache[job_id] = data
                return dict(data)
            except (json.JSONDecodeError, OSError):
                logger.warning("[JOB] Could not read job file %s", job_id)
                return None

    def update_job(self, job_id: str, **fields):
        """Update a job's fields and persist to disk."""
        with self._lock:
            if job_id not in self._cache:
                jp = self._job_path(job_id)
                if jp.exists():
                    with open(jp) as f:
                        self._cache[job_id] = json.load(f)
                else:
                    raise KeyError(f"Job {job_id} not found")

            job = self._cache[job_id]
            for k, v in fields.items():
                if k in ("status", "progress_pct", "message",
                         "output_file", "error_message"):
                    job[k] = v
            self._write_job(job_id, job)

    def get_or_create(
        self, ebook_path: str, model_name: str, voice: str, format_type: str,
    ) -> dict:
        """Get existing job or create a new one.

        If an existing ready job's output file no longer exists on disk
        (e.g. after cleanup), treat it as needing re-conversion.
        """
        raw = f"{ebook_path}:{model_name}:{voice}:{format_type}"
        job_id = hashlib.md5(raw.encode()).hexdigest()[:16]

        with self._lock:
            if self._cache.get(job_id) and self._cache[job_id]["job_id"] == job_id:
                cached_job = dict(self._cache[job_id])
                # If ready, verify output actually exists on disk
                if (cached_job.get("status") == "ready"
                        and cached_job.get("output_file")
                        and not Path(cached_job["output_file"]).exists()):
                    # Output missing — remove stale job so it gets recreated below
                    del self._cache[job_id]
                else:
                    return cached_job

            # Check disk too
            jp = self._job_path(job_id)
            if jp.exists():
                with open(jp) as f:
                    existing = json.load(f)
                if existing.get("format_type") == format_type and \
                   existing.get("status") in ("ready", "failed"):
                    # If ready, verify output actually exists on disk
                    out_file = existing.get("output_file")
                    if not out_file or not Path(out_file).exists():
                        # Output missing — don't return stale job, fall through to recreate
                        pass
                    else:
                        self._cache[job_id] = existing
                        return dict(existing)

        return self.create_job(ebook_path, model_name, voice, format_type)


# Module-level singleton — created on first import (go up 4 levels from services/)
DEFAULT_JOBS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "storage" / "download_jobs"


def _get_default_manager() -> JobManager:
    """Get the default job manager instance (creates if needed)."""
    # Use a module-level variable to cache the singleton per process
    import sys
    key = "_job_manager_singleton"
    mgr = getattr(sys.modules[__name__], key, None)
    if mgr is None:
        mgr = JobManager(DEFAULT_JOBS_DIR)
        setattr(sys.modules[__name__], key, mgr)
    return mgr
