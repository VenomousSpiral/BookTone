"""
Settings service for streaming preferences.

Handles loading/saving user streaming settings (model, voice, display options).
Extracted from stream_service.py to reduce its size.
"""
from pathlib import Path
from typing import Dict
import json
import logging

from app.core.config import settings

logger = logging.getLogger(__name__)

DEFAULT_SETTINGS: Dict = {
    "font_size": 16,
    "font_family": "system",
    "preferred_model": None,
    "preferred_voice": None,
    "progress_mode": "book",
    "time_mode": "total",
    "show_title": True,
    "show_progress_bar": True,
    "show_images": False,
    "save_stream_audio": False,
    "sleep_timer_minutes": 0,
    "show_sleep_timer": False,
}


class SettingsService:
    """Manages streaming settings persistence."""

    def __init__(self, settings_file: Path = None):
        self.settings_file = settings_file or (
            settings.STORAGE_DIR / "stream_settings.json"
        )

    def load_settings(self) -> Dict:
        """Load streaming settings from disk, falling back to defaults."""
        if self.settings_file.exists():
            try:
                with open(self.settings_file, "r") as f:
                    return json.load(f)
            except Exception as e:
                logger.error("[SETTINGS] Failed to load: %s", e)
        return dict(DEFAULT_SETTINGS)

    def save_settings(self, settings_data: Dict) -> None:
        """Save streaming settings to disk."""
        try:
            with open(self.settings_file, "w") as f:
                json.dump(settings_data, f, indent=2)
        except Exception as e:
            logger.error("[SETTINGS] Failed to save: %s", e)
            raise
