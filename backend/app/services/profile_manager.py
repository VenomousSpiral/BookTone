"""
Profile management for audiobook generation.

Handles CRUD operations for ebook/model/voice generation profiles.
Extracted from stream_audiobook_service.py to improve separation of concerns.
"""
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from app.core.config import settings

logger = logging.getLogger(__name__)


class ProfileManager:
    """Manages audiobook generation profiles (ebook:model:voice combos)."""

    def __init__(self, profiles_file: Optional[Path] = None):
        self.profiles_file = profiles_file or (settings.STORAGE_DIR / "audiobooks_db.json")
        self._profiles: Dict[str, dict] = {}
        self._load_profiles()

    def _get_profile_key(self, ebook_path: str, model: str, voice: str) -> str:
        """Generate profile key: {ebook_path}:{model}:{voice}"""
        return f"{ebook_path}:{model}:{voice}"

    def _load_profiles(self):
        """Load profiles from disk."""
        if self.profiles_file.exists():
            try:
                with open(self.profiles_file, 'r') as f:
                    data = json.load(f)
                    for key, profile_data in data.items():
                        self._profiles[key] = profile_data
                logger.info("[PROFILE] Loaded %d profiles", len(self._profiles))
            except Exception as e:
                logger.error("[PROFILE] Failed to load profiles: %s", e)

    def _save_profiles(self):
        """Save profiles to disk."""
        try:
            with open(self.profiles_file, 'w') as f:
                json.dump(self._profiles, f, indent=2, default=str)
        except Exception as e:
            logger.error("[PROFILE] Failed to save profiles: %s", e)

    def create_profile(self, ebook_path: str, model: str, voice: str,
                       total_chunks: int = 0, chapters: list = None) -> dict:
        """Create a new profile for an ebook/model/voice combo."""
        key = self._get_profile_key(ebook_path, model, voice)
        profile = {
            "ebook_path": ebook_path,
            "title": Path(ebook_path).stem,
            "model": model,
            "voice": voice,
            "status": "not_started",
            "ebook_hash": self._compute_ebook_hash(ebook_path),
            "total_chunks": total_chunks,
            "completed_chunks": 0,
            "progress": 0.0,
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
            "last_position": 0.0,
            "bookmarks": [],
            "chapters": chapters or [],
            "lrc_lines": [],
            "error": None
        }
        self._profiles[key] = profile
        self._save_profiles()
        return profile

    def get_profile(self, ebook_path: str, model: str, voice: str) -> Optional[dict]:
        """Get a profile by key."""
        key = self._get_profile_key(ebook_path, model, voice)
        return self._profiles.get(key)

    def update_profile_status(self, ebook_path: str, model: str, voice: str,
                              status: str, completed_chunks: int = None,
                              error: str = None, total_chunks: int = None):
        """Update profile status and related fields."""
        key = self._get_profile_key(ebook_path, model, voice)
        if key not in self._profiles:
            return
        profile = self._profiles[key]
        profile["status"] = status
        profile["updated_at"] = datetime.now().isoformat()
        if completed_chunks is not None:
            profile["completed_chunks"] = completed_chunks
            if total_chunks and total_chunks > 0:
                profile["progress"] = round(completed_chunks / total_chunks * 100, 1)
        if error is not None:
            profile["error"] = error
        if total_chunks is not None:
            profile["total_chunks"] = total_chunks
        self._save_profiles()

    def delete_profile(self, ebook_path: str, model: str, voice: str) -> bool:
        """Delete a profile."""
        key = self._get_profile_key(ebook_path, model, voice)
        if key in self._profiles:
            del self._profiles[key]
            self._save_profiles()
            return True
        return False

    def update_profile_chapters(self, ebook_path: str, model: str, voice: str,
                                chapters: list, total_chunks: int):
        """Update profile with chapter and chunk info."""
        key = self._get_profile_key(ebook_path, model, voice)
        if key in self._profiles:
            self._profiles[key]["chapters"] = chapters
            self._profiles[key]["total_chunks"] = total_chunks
            self._profiles[key]["ebook_hash"] = self._compute_ebook_hash(ebook_path)
            self._profiles[key]["updated_at"] = datetime.now().isoformat()
            self._save_profiles()

    def _compute_ebook_hash(self, ebook_path: str) -> str:
        """Compute MD5 hash of ebook file for change detection."""
        import hashlib
        try:
            full_path = Path(ebook_path)
            if not full_path.exists():
                full_path = settings.EBOOKS_DIR / ebook_path
            mtime = full_path.stat().st_mtime
            size = full_path.stat().st_size
            return hashlib.md5(f"{mtime}:{size}".encode()).hexdigest()[:12]
        except Exception:
            return "unknown"

    def get_all_profiles(self) -> Dict[str, dict]:
        """Get all profiles."""
        return dict(self._profiles)

    def get_profiles_for_ebook(self, ebook_path: str) -> Dict[str, dict]:
        """Get all profiles for a specific ebook."""
        return {
            k: v for k, v in self._profiles.items()
            if v.get("ebook_path") == ebook_path
        }
