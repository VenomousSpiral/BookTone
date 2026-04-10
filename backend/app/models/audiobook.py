from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
from enum import Enum

class GenerationStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    PAUSED = "paused"

class ChapterInfo(BaseModel):
    name: str
    start_chunk: int  # Index of first text chunk in this chapter
    end_chunk: int    # Index of last text chunk in this chapter
    timestamp: float  # Start time in seconds

class AudioChunk(BaseModel):
    """Represents a single audio file chunk"""
    index: int  # Chunk file index (0, 1, 2, ...)
    filename: str  # e.g., "chunk_0000.mp3"
    start_text_chunk: int  # First text chunk in this audio file
    end_text_chunk: int  # Last text chunk in this audio file
    duration: float  # Duration in seconds
    start_time: float  # Cumulative start time in full audiobook

class AudiobookMetadata(BaseModel):
    id: str
    title: str
    source_file: str
    audio_chunks: List[AudioChunk] = []  # List of audio chunk files
    lrc_file: Optional[str] = None
    status: GenerationStatus = GenerationStatus.PENDING
    progress: float = 0.0
    model: str
    voice: str
    created_at: datetime
    updated_at: datetime
    total_chunks: int = 0  # Total text chunks
    completed_chunks: int = 0  # Completed text chunks
    error: Optional[str] = None
    last_position: float = 0.0  # Last playback position in seconds (cumulative across all chunks)
    bookmarks: List[int] = []  # List of bookmarked text chunk indices
    chapters: List[ChapterInfo] = []  # Chapter information
    ebook_hash: Optional[str] = None  # Hash of ebook content to detect changes
    total_duration: float = 0.0  # Total duration in seconds

class LRCLine(BaseModel):
    timestamp: float  # seconds
    text: str
    
class LRCFile(BaseModel):
    lines: List[LRCLine]
    
class GenerateAudiobookRequest(BaseModel):
    ebook_path: str
    model: str
    voice: str
    instructions: Optional[str] = None
