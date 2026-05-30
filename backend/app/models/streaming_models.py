"""
Pydantic models for streaming API routes.

Centralizes request/response models that were previously defined inline
in streaming.py to improve separation of concerns.
"""
from pydantic import BaseModel
from typing import Optional, List


class StreamAudioRequest(BaseModel):
    """Request to generate audio for a text segment"""
    ebook_path: str
    start_char: int
    end_char: int
    model: str
    voice: str


class UpdateStreamSettingsRequest(BaseModel):
    """Request to update streaming settings"""
    preferred_model: Optional[str] = None
    preferred_voice: Optional[str] = None
    font_size: Optional[int] = None
    font_family: Optional[str] = None
    progress_mode: Optional[str] = None
    time_mode: Optional[str] = None
    show_title: Optional[bool] = None
    show_progress_bar: Optional[bool] = None
    show_images: Optional[bool] = None
    save_stream_audio: Optional[bool] = None
    sleep_timer_minutes: Optional[int] = None
    show_sleep_timer: Optional[bool] = None


class UpdateProgressRequest(BaseModel):
    """Request to update streaming progress"""
    ebook_path: str
    chunk_index: int


class ToggleBookmarkRequest(BaseModel):
    """Request to toggle a bookmark"""
    ebook_path: str
    chunk_index: int
    text_preview: Optional[str] = ""


class TextBatchRequest(BaseModel):
    """Request to get text for multiple chunks at once"""
    ebook_path: str
    chunk_indices: List[int]
    with_images: bool = False


class GenerateCacheRequest(BaseModel):
    """Request to generate audio for a cache entry"""
    ebook_path: str
    model: str
    voice: str
    instructions: Optional[str] = None
