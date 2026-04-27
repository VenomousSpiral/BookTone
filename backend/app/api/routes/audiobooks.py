from fastapi import APIRouter, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse, StreamingResponse
from typing import List, Optional
from pydantic import BaseModel
from app.models.audiobook import (
    AudiobookMetadata,
    GenerateAudiobookRequest,
    GenerationStatus
)
from app.services.audio_generator import AudioGenerator
from app.core.config import settings
from pathlib import Path
import json
import shutil
import os

router = APIRouter()
audio_generator = AudioGenerator()

# Themes directory
THEMES_DIR = settings.BASE_DIR / "frontend" / "static" / "themes"

@router.get("/themes")
async def list_themes():
    """Dynamically discover and list all available themes"""
    themes = []
    if THEMES_DIR.exists():
        for f in sorted(THEMES_DIR.iterdir()):
            if f.suffix == ".json":
                try:
                    with open(f, "r") as fh:
                        theme = json.load(fh)
                    themes.append({
                        "id": f.stem,
                        "name": theme.get("name", f.stem),
                        "description": theme.get("description", "")
                    })
                except Exception:
                    pass
    return themes

# In-memory storage for audiobook metadata (you can replace with a database)
audiobooks_db = {}

# File to persist audiobook data
AUDIOBOOKS_DB_FILE = settings.STORAGE_DIR / "audiobooks_db.json"

def load_audiobooks_db():
    """Load audiobooks database from disk"""
    if AUDIOBOOKS_DB_FILE.exists():
        try:
            with open(AUDIOBOOKS_DB_FILE, 'r') as f:
                data = json.load(f)
                for audiobook_id, audiobook_data in data.items():
                    audiobooks_db[audiobook_id] = AudiobookMetadata(**audiobook_data)
        except Exception as e:
            print(f"[ERROR] Failed to load audiobooks database: {e}")

def save_audiobooks_db():
    """Save audiobooks database to disk"""
    try:
        data = {}
        for audiobook_id, audiobook in audiobooks_db.items():
            data[audiobook_id] = audiobook.model_dump()
        with open(AUDIOBOOKS_DB_FILE, 'w') as f:
            json.dump(data, f, indent=2, default=str)
    except Exception as e:
        print(f"[ERROR] Failed to save audiobooks database: {e}")

# Load existing data on startup
load_audiobooks_db()

class UpdatePositionRequest(BaseModel):
    position: float  # Position in seconds

class ToggleBookmarkRequest(BaseModel):
    chunk_index: int

class UpdateAudiobookRequest(BaseModel):
    ebook_path: str  # Path to the updated ebook file
    mode: str = "continue"  # "continue" = continue from current position, "append" = add as new chapter
    new_title: str = None  # Optional: update the audiobook title

@router.post("/generate", response_model=AudiobookMetadata)
async def generate_audiobook(
    request: GenerateAudiobookRequest,
    background_tasks: BackgroundTasks
):
    """Start generating an audiobook from an ebook"""
    try:
        # Create audiobook metadata
        audiobook = audio_generator.create_audiobook_metadata(
            request.ebook_path,
            request.model,
            request.voice,
            request.instructions
        )
        
        # Store in database
        audiobooks_db[audiobook.id] = audiobook
        
        # Save to disk
        save_audiobooks_db()
        
        # Start generation in background
        background_tasks.add_task(
            audio_generator.generate_audiobook,
            audiobook.id,
            audiobooks_db,
            save_audiobooks_db
        )
        
        return audiobook
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# User Preferences (must come BEFORE any /{audiobook_id} routes)
class UserPreferences(BaseModel):
    font_size: Optional[str] = "16"
    font_family: Optional[str] = "system"
    progress_mode: Optional[str] = "book"
    time_mode: Optional[str] = "total"
    show_progress_bar: Optional[bool] = True
    show_title: Optional[bool] = True
    show_audio_bar: Optional[bool] = True
    show_images: Optional[bool] = False
    sleep_timer_minutes: Optional[int] = 0
    show_sleep_timer: Optional[bool] = False
    theme: Optional[str] = "default"
    audiobooks: Optional[dict] = {}  # Track audiobook play times: {id: {last_played: timestamp}}

USER_PREFS_FILE = settings.STORAGE_DIR / "user_preferences.json"

def load_user_preferences():
    """Load user preferences from disk"""
    if USER_PREFS_FILE.exists():
        try:
            with open(USER_PREFS_FILE, 'r') as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading user preferences: {e}")
    return {
        "font_size": "16",
        "font_family": "system",
        "progress_mode": "book",
        "time_mode": "total",
        "show_progress_bar": True,
        "show_title": True,
        "show_audio_bar": True,
        "show_images": False,
        "sleep_timer_minutes": 0,
        "show_sleep_timer": False,
        "theme": "default",
        "audiobooks": {}
    }

def save_user_preferences(prefs: dict):
    """Save user preferences to disk"""
    try:
        with open(USER_PREFS_FILE, 'w') as f:
            json.dump(prefs, f, indent=2)
    except Exception as e:
        print(f"Error saving user preferences: {e}")

@router.get("/preferences/get")
async def get_user_preferences():
    """Get user preferences"""
    if settings.DEBUG:
        print("[PREFERENCES] GET /preferences/get called")
    prefs = load_user_preferences()
    if settings.DEBUG:
        print(f"[PREFERENCES] Returning preferences: {prefs}")
    return prefs

@router.post("/preferences/save")
async def save_preferences(prefs: UserPreferences):
    """Save user preferences"""
    if settings.DEBUG:
        print(f"[PREFERENCES] POST /preferences/save called with: {prefs}")
    # Load existing preferences to preserve audiobooks tracking data
    existing_prefs = load_user_preferences()
    
    # Convert incoming preferences to dict
    prefs_dict = prefs.model_dump()
    
    # If audiobooks field is empty or missing in the incoming request,
    # preserve the existing audiobooks tracking data
    if not prefs_dict.get('audiobooks'):
        prefs_dict['audiobooks'] = existing_prefs.get('audiobooks', {})
    
    save_user_preferences(prefs_dict)
    if settings.DEBUG:
        print(f"[PREFERENCES] Saved preferences (with preserved audiobooks data): {prefs_dict}")
    return {"message": "Preferences saved", "preferences": prefs_dict}

class TrackingRequest(BaseModel):
    ebook_path: str
    event: str = "playback_start"

@router.post("/preferences/tracking")
async def track_playback(request: TrackingRequest):
    """Lightweight tracking endpoint - updates only one field without full round-trip"""
    try:
        prefs = load_user_preferences()
        if not prefs.get('audiobooks'):
            prefs['audiobooks'] = {}
        prefs['audiobooks'][request.ebook_path] = {
            'last_played': int(__import__('time').time() * 1000),
            'event': request.event
        }
        save_user_preferences(prefs)
        return {"message": "tracked"}
    except Exception as e:
        if settings.DEBUG:
            print(f"[TRACKING] Error: {e}")
        return {"message": "error"}, 500


# Directory management endpoints (must come before /{audiobook_id} routes)
class CreateDirectoryRequest(BaseModel):
    path: str

class MoveItemRequest(BaseModel):
    source: str
    destination: str
    is_directory: bool = False


@router.get("/list")
async def list_audiobooks_with_folders(path: str = ""):
    """List audiobooks and folders in a specific path"""
    audiobooks_dir = settings.AUDIOBOOKS_DIR
    current_path = audiobooks_dir / path if path else audiobooks_dir
    
    if not current_path.exists():
        current_path.mkdir(parents=True, exist_ok=True)
    
    items = []
    
    # List all directories in current path
    try:
        for item in sorted(current_path.iterdir()):
            if item.is_dir():
                # Filter out stream cache directories
                if item.name.startswith('_stream_cache_'):
                    continue
                    
                # Check if this is an audiobook directory (has metadata in DB)
                rel_path = str(item.relative_to(audiobooks_dir))
                
                if rel_path in audiobooks_db:
                    # It's an audiobook
                    audiobook = audiobooks_db[rel_path]
                    items.append({
                        **audiobook.model_dump(),
                        "is_directory": False,
                        "modified": item.stat().st_mtime
                    })
                else:
                    # It's a regular folder for organization
                    items.append({
                        "id": rel_path,
                        "path": rel_path,
                        "title": item.name,
                        "is_directory": True,
                        "modified": item.stat().st_mtime,
                        "status": None,
                        "model": None,
                        "voice": None
                    })
    except Exception as e:
        print(f"[ERROR] Error listing audiobooks: {e}")
    
    return items


@router.post("/create-directory")
async def create_audiobook_directory(request: CreateDirectoryRequest):
    """Create a new directory for organizing audiobooks"""
    audiobooks_dir = settings.AUDIOBOOKS_DIR
    new_dir = audiobooks_dir / request.path
    
    if new_dir.exists():
        raise HTTPException(status_code=400, detail="Directory already exists")
    
    try:
        new_dir.mkdir(parents=True, exist_ok=False)
        return {"message": "Directory created", "path": request.path}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/move")
async def move_audiobook_or_folder(request: MoveItemRequest):
    """Move an audiobook or folder to a different location"""
    audiobooks_dir = settings.AUDIOBOOKS_DIR
    source_path = audiobooks_dir / request.source
    dest_dir = audiobooks_dir / request.destination if request.destination else audiobooks_dir
    
    if not source_path.exists():
        raise HTTPException(status_code=404, detail="Source not found")
    
    if not dest_dir.exists():
        raise HTTPException(status_code=404, detail="Destination directory not found")
    
    # Calculate new path
    dest_path = dest_dir / source_path.name
    
    if dest_path.exists():
        raise HTTPException(status_code=400, detail="Destination already exists")
    
    try:
        shutil.move(str(source_path), str(dest_path))
        
        # If moving an audiobook (not a regular folder), update the database
        if request.source in audiobooks_db:
            audiobook = audiobooks_db[request.source]
            del audiobooks_db[request.source]
            
            # Update the ID to the new path
            new_id = str(dest_path.relative_to(audiobooks_dir))
            audiobook.id = new_id
            audiobooks_db[new_id] = audiobook
            save_audiobooks_db()
        
        return {"message": "Moved successfully", "new_path": str(dest_path.relative_to(audiobooks_dir))}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/delete-directory")
async def delete_audiobook_directory(path: str):
    """Delete an empty or non-empty directory"""
    audiobooks_dir = settings.AUDIOBOOKS_DIR
    dir_path = audiobooks_dir / path
    
    if not dir_path.exists():
        raise HTTPException(status_code=404, detail="Directory not found")
    
    if not dir_path.is_dir():
        raise HTTPException(status_code=400, detail="Not a directory")
    
    try:
        # Remove all audiobooks in this directory from the database
        to_remove = []
        for audiobook_id in audiobooks_db.keys():
            if audiobook_id.startswith(path):
                to_remove.append(audiobook_id)
        
        for audiobook_id in to_remove:
            del audiobooks_db[audiobook_id]
        
        # Remove the directory
        shutil.rmtree(dir_path)
        save_audiobooks_db()
        
        return {"message": "Directory deleted", "path": path}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{audiobook_id:path}/audio/{chunk_index}")
async def get_audio_chunk(audiobook_id: str, chunk_index: int):
    """Get a specific audio chunk file"""
    if audiobook_id not in audiobooks_db:
        raise HTTPException(status_code=404, detail="Audiobook not found")
    
    audiobook = audiobooks_db[audiobook_id]
    
    # Find the requested chunk
    chunk = next((c for c in audiobook.audio_chunks if c.index == chunk_index), None)
    if not chunk:
        raise HTTPException(status_code=404, detail=f"Audio chunk {chunk_index} not found")
    
    # Build path to chunk file
    chunk_path = settings.AUDIOBOOKS_DIR / audiobook_id / chunk.filename
    if not chunk_path.exists():
        raise HTTPException(status_code=404, detail="Audio chunk file not found")
    
    # If generation is in progress, add no-cache headers
    headers = {}
    if audiobook.status == GenerationStatus.IN_PROGRESS:
        headers = {
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0"
        }
    
    return FileResponse(
        path=str(chunk_path),
        media_type="audio/mpeg",
        filename=chunk.filename,
        headers=headers
    )

@router.get("/{audiobook_id:path}/chunks")
async def get_audio_chunks(audiobook_id: str):
    """Get metadata for all audio chunks"""
    if audiobook_id not in audiobooks_db:
        raise HTTPException(status_code=404, detail="Audiobook not found")
    
    audiobook = audiobooks_db[audiobook_id]
    return {
        "audiobook_id": audiobook_id,
        "total_duration": audiobook.total_duration,
        "chunks": [chunk.model_dump() for chunk in audiobook.audio_chunks],
        "status": audiobook.status
    }

@router.get("/{audiobook_id:path}/lrc")
async def get_lrc(audiobook_id: str):
    """Get the LRC file for an audiobook"""
    if audiobook_id not in audiobooks_db:
        raise HTTPException(status_code=404, detail="Audiobook not found")
    
    audiobook = audiobooks_db[audiobook_id]
    if not audiobook.lrc_file:
        raise HTTPException(status_code=404, detail="LRC file not generated yet")
    
    lrc_path = Path(audiobook.lrc_file)
    if not lrc_path.exists():
        raise HTTPException(status_code=404, detail="LRC file not found")
    
    return FileResponse(
        path=str(lrc_path),
        media_type="text/plain",
        filename=f"{audiobook.title}.lrc"
    )

@router.get("/{audiobook_id:path}/images")
async def get_audiobook_images(audiobook_id: str):
    """Get images from the audiobook's source ebook file"""
    from app.services.stream_service import StreamService
    
    if audiobook_id not in audiobooks_db:
        raise HTTPException(status_code=404, detail="Audiobook not found")
    
    audiobook = audiobooks_db[audiobook_id]
    if not audiobook.source_file:
        raise HTTPException(status_code=404, detail="Source ebook file not found")
    
    try:
        stream_service = StreamService()
        result = stream_service.parse_ebook_with_images(audiobook.source_file)
        
        # Return image IDs and chunk associations
        return {
            "audiobook_id": audiobook_id,
            "has_images": bool(result.get("images")),
            "chunks": [
                {
                    "index": chunk["index"],
                    "images": chunk.get("images", [])
                }
                for chunk in result["chunks"]
            ]
        }
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/{audiobook_id:path}/image/{image_id}")
async def get_audiobook_image(audiobook_id: str, image_id: str):
    """Get a specific image from the audiobook's source ebook"""
    from app.services.stream_service import StreamService
    
    if audiobook_id not in audiobooks_db:
        raise HTTPException(status_code=404, detail="Audiobook not found")
    
    audiobook = audiobooks_db[audiobook_id]
    if not audiobook.source_file:
        raise HTTPException(status_code=404, detail="Source ebook file not found")
    
    try:
        stream_service = StreamService()
        image_data = stream_service.get_image(audiobook.source_file, image_id)
        
        if not image_data:
            raise HTTPException(status_code=404, detail="Image not found")
        
        return {"image_id": image_id, "data": image_data}
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/{audiobook_id:path}/pause")
async def pause_generation(audiobook_id: str):
    """Pause audiobook generation"""
    if audiobook_id not in audiobooks_db:
        raise HTTPException(status_code=404, detail="Audiobook not found")
    
    audiobook = audiobooks_db[audiobook_id]
    audiobook.status = GenerationStatus.PAUSED
    return {"message": "Generation paused", "audiobook": audiobook}

@router.post("/{audiobook_id:path}/resume")
async def resume_generation(audiobook_id: str, background_tasks: BackgroundTasks):
    """Resume audiobook generation"""
    if audiobook_id not in audiobooks_db:
        raise HTTPException(status_code=404, detail="Audiobook not found")
    
    audiobook = audiobooks_db[audiobook_id]
    # Allow resuming from paused or failed status
    if audiobook.status not in [GenerationStatus.PAUSED, GenerationStatus.FAILED]:
        raise HTTPException(status_code=400, detail="Audiobook cannot be resumed (must be paused or failed)")
    
    # Clear any previous error
    audiobook.error = None
    audiobook.status = GenerationStatus.IN_PROGRESS
    save_audiobooks_db()
    
    background_tasks.add_task(
        audio_generator.generate_audiobook,
        audiobook_id,
        audiobooks_db,
        save_audiobooks_db
    )
    
    return {"message": "Generation resumed", "audiobook": audiobook}

@router.post("/{audiobook_id:path}/update")
async def update_audiobook(
    audiobook_id: str, 
    request: UpdateAudiobookRequest,
    background_tasks: BackgroundTasks
):
    """
    Update audiobook with new ebook content.
    
    Modes:
    - "continue": Update source file and continue generation from where it left off
                  (good for updated versions of the same book)
    - "append": Keep existing audio and append new content as a new chapter at the end
                (good for adding a completely different file)
    """
    if audiobook_id not in audiobooks_db:
        raise HTTPException(status_code=404, detail="Audiobook not found")
    
    audiobook = audiobooks_db[audiobook_id]
    
    # Update title if provided
    if request.new_title:
        audiobook.title = request.new_title
    
    if request.mode == "append":
        # Append mode: Keep existing audio, add new content as a new chapter
        # This requires special handling in the generator
        audiobook.source_file = request.ebook_path
        audiobook.ebook_hash = None  # Will be recomputed
        audiobook.status = GenerationStatus.IN_PROGRESS
        
        # Store the append flag so generator knows to preserve existing content
        # We store the current completed_chunks as the append point
        if not hasattr(audiobook, 'append_from_chunk'):
            # Add this to metadata - it tells generator where to start appending
            pass
        
        # For append mode, we add a marker chapter with the current position
        # The generator will handle this specially
        background_tasks.add_task(
            audio_generator.generate_audiobook_append,
            audiobook_id,
            audiobooks_db,
            save_audiobooks_db,
            audiobook.completed_chunks  # Pass current position as append point
        )
    else:
        # Continue mode (default): Update source and continue from current position
        audiobook.source_file = request.ebook_path
        audiobook.ebook_hash = None  # Reset hash so it will be recomputed
        audiobook.status = GenerationStatus.IN_PROGRESS
        
        # Start generation (it will continue from completed_chunks)
        background_tasks.add_task(
            audio_generator.generate_audiobook,
            audiobook_id,
            audiobooks_db,
            save_audiobooks_db
        )
    
    save_audiobooks_db()
    
    return {"message": f"Audiobook update started (mode: {request.mode})", "audiobook": audiobook}

@router.post("/{audiobook_id:path}/regenerate-chunk/{chunk_index}")
async def regenerate_chunk(audiobook_id: str, chunk_index: int, background_tasks: BackgroundTasks):
    """Regenerate a specific audio chunk without affecting other chunks"""
    if audiobook_id not in audiobooks_db:
        raise HTTPException(status_code=404, detail="Audiobook not found")
    
    audiobook = audiobooks_db[audiobook_id]
    
    # Validate chunk index
    if chunk_index < 0 or chunk_index >= len(audiobook.audio_chunks):
        raise HTTPException(status_code=400, detail=f"Invalid chunk index: {chunk_index}")
    
    # Check that book is not currently generating
    if audiobook.status == GenerationStatus.IN_PROGRESS:
        raise HTTPException(status_code=400, detail="Cannot regenerate while audiobook is generating")
    
    # Mark as in progress
    audiobook.status = GenerationStatus.IN_PROGRESS
    audiobook.error = None
    save_audiobooks_db()
    
    # Start regeneration in background using the dedicated single-chunk method
    background_tasks.add_task(
        audio_generator.regenerate_single_chunk,
        audiobook_id,
        chunk_index,
        audiobooks_db,
        save_audiobooks_db
    )
    
    return {"message": f"Regenerating chunk {chunk_index}", "audiobook": audiobook}

@router.delete("/{audiobook_id:path}")
async def delete_audiobook(audiobook_id: str):
    """Delete an audiobook and its files"""
    if audiobook_id not in audiobooks_db:
        raise HTTPException(status_code=404, detail="Audiobook not found")
    
    audiobook = audiobooks_db[audiobook_id]
    
    # Delete audiobook directory (contains all chunk files)
    audiobook_dir = settings.AUDIOBOOKS_DIR / audiobook_id
    if audiobook_dir.exists():
        import shutil
        shutil.rmtree(audiobook_dir)
    
    # Delete LRC file
    if audiobook.lrc_file:
        lrc_path = Path(audiobook.lrc_file)
        if lrc_path.exists():
            lrc_path.unlink()
    
    # Remove from database
    del audiobooks_db[audiobook_id]
    
    # Save to disk
    save_audiobooks_db()
    
    return {"message": "Audiobook deleted successfully"}

@router.post("/{audiobook_id:path}/position")
async def update_position(audiobook_id: str, request: UpdatePositionRequest):
    """Update the last playback position"""
    if audiobook_id not in audiobooks_db:
        raise HTTPException(status_code=404, detail="Audiobook not found")
    
    audiobook = audiobooks_db[audiobook_id]
    audiobook.last_position = request.position
    
    # Save to disk
    save_audiobooks_db()
    
    return {"message": "Position updated", "position": request.position}

@router.post("/{audiobook_id:path}/bookmark")
async def toggle_bookmark(audiobook_id: str, request: ToggleBookmarkRequest):
    """Toggle a bookmark on a chunk"""
    if audiobook_id not in audiobooks_db:
        raise HTTPException(status_code=404, detail="Audiobook not found")
    
    audiobook = audiobooks_db[audiobook_id]
    
    if request.chunk_index in audiobook.bookmarks:
        # Remove bookmark
        audiobook.bookmarks.remove(request.chunk_index)
        action = "removed"
    else:
        # Add bookmark
        audiobook.bookmarks.append(request.chunk_index)
        audiobook.bookmarks.sort()  # Keep bookmarks sorted
        action = "added"
    
    # Save to disk
    save_audiobooks_db()
    
    return {
        "message": f"Bookmark {action}",
        "chunk_index": request.chunk_index,
        "bookmarks": audiobook.bookmarks
    }

@router.get("/{audiobook_id:path}/bookmarks")
async def get_bookmarks(audiobook_id: str):
    """Get all bookmarks for an audiobook"""
    if audiobook_id not in audiobooks_db:
        raise HTTPException(status_code=404, detail="Audiobook not found")
    
    audiobook = audiobooks_db[audiobook_id]
    return {"bookmarks": audiobook.bookmarks}


# Track download/combine progress for each audiobook
download_progress = {}

def combine_audiobook_chunks(audiobook_id: str):
    """
    Background task to combine all audio chunks into a single MP3 file.
    Uses ffmpeg concat demuxer for efficient, memory-safe concatenation of large files.
    Updates progress in download_progress dict.
    """
    import tempfile
    import os
    import subprocess
    
    temp_files = []
    concat_file = None
    
    try:
        audiobook = audiobooks_db.get(audiobook_id)
        if not audiobook:
            download_progress[audiobook_id] = {"status": "error", "error": "Audiobook not found"}
            return
        
        audiobook_dir = settings.AUDIOBOOKS_DIR / audiobook_id
        combined_path = audiobook_dir / "combined_download.mp3"
        
        # Initialize progress
        download_progress[audiobook_id] = {
            "status": "combining",
            "progress": 0,
            "current_chunk": 0,
            "total_chunks": len(audiobook.audio_chunks),
            "message": "Starting..."
        }
        
        # Sort chunks by index
        sorted_chunks = sorted(audiobook.audio_chunks, key=lambda c: c.index)
        total_chunks = len(sorted_chunks)
        
        if total_chunks == 0:
            download_progress[audiobook_id] = {"status": "error", "error": "No audio chunks"}
            return
        
        # Create a concat file for ffmpeg
        # This approach is memory-efficient and can handle files of any size
        download_progress[audiobook_id].update({
            "progress": 5,
            "message": "Preparing file list..."
        })
        
        # Create temp file with list of all chunks
        concat_file = tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False)
        
        valid_chunks = 0
        for i, chunk in enumerate(sorted_chunks):
            chunk_path = audiobook_dir / chunk.filename
            if chunk_path.exists():
                # ffmpeg concat format requires escaping single quotes
                escaped_path = str(chunk_path).replace("'", "'\\''")
                concat_file.write(f"file '{escaped_path}'\n")
                valid_chunks += 1
            else:
                print(f"[DOWNLOAD] Warning: chunk file not found: {chunk_path}")
            
            # Update progress
            if i % 100 == 0:  # Update every 100 chunks to avoid too many updates
                progress = 5 + int((i / total_chunks) * 10)
                download_progress[audiobook_id].update({
                    "current_chunk": i,
                    "progress": progress,
                    "message": f"Indexing chunk {i+1} of {total_chunks}..."
                })
        
        concat_file.close()
        
        print(f"[DOWNLOAD] Concat file written with {valid_chunks} valid chunks")
        
        if valid_chunks == 0:
            download_progress[audiobook_id] = {"status": "error", "error": "No audio chunk files found"}
            return
        
        # Debug: print first few lines of concat file
        with open(concat_file.name, 'r') as f:
            first_lines = f.readlines()[:3]
            print(f"[DOWNLOAD] Concat file first lines: {first_lines}")
        
        download_progress[audiobook_id].update({
            "progress": 15,
            "message": "Combining audio files with ffmpeg..."
        })
        
        # Use ffmpeg concat demuxer - this is very efficient and doesn't load files into memory
        # It just concatenates the MP3 streams directly
        temp_output = str(combined_path) + ".tmp.mp3"  # Use .mp3 extension so ffmpeg recognizes format
        
        # Remove temp output if it exists from a previous failed attempt
        if os.path.exists(temp_output):
            os.unlink(temp_output)
        
        cmd = [
            'ffmpeg', '-y',  # Overwrite output
            '-f', 'concat',  # Use concat demuxer
            '-safe', '0',    # Allow absolute paths
            '-i', concat_file.name,  # Input concat file
            '-c', 'copy',    # Copy codec (no re-encoding, very fast)
            '-loglevel', 'error',  # Only show errors, not the banner
            temp_output
        ]
        
        print(f"[DOWNLOAD] Running ffmpeg command: {' '.join(cmd)}")
        print(f"[DOWNLOAD] Concat file: {concat_file.name}")
        
        # Run ffmpeg
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        
        # Monitor the process
        download_progress[audiobook_id].update({
            "progress": 20,
            "message": "ffmpeg is combining files (this may take a while for large audiobooks)..."
        })
        
        stdout, stderr = process.communicate()
        
        stderr_text = stderr.decode('utf-8', errors='ignore').strip()
        
        # Check if output file was created and has content
        if not os.path.exists(temp_output):
            error_detail = stderr_text if stderr_text else "Output file was not created"
            raise Exception(f"ffmpeg failed to create output: {error_detail[:500]}")
        
        if os.path.getsize(temp_output) == 0:
            raise Exception("ffmpeg created empty output file")
        
        # Log any warnings but don't fail if file was created successfully
        if stderr_text:
            print(f"[DOWNLOAD] ffmpeg stderr (may be warnings): {stderr_text[:500]}")
        
        # Move temp file to final location
        download_progress[audiobook_id].update({
            "progress": 95,
            "message": "Finalizing..."
        })
        
        os.rename(temp_output, str(combined_path))
        
        # Get file size
        file_size = combined_path.stat().st_size
        file_size_mb = file_size / (1024 * 1024)
        file_size_gb = file_size / (1024 * 1024 * 1024)
        
        size_str = f"{file_size_gb:.2f} GB" if file_size_gb >= 1 else f"{file_size_mb:.1f} MB"
        
        download_progress[audiobook_id] = {
            "status": "ready",
            "progress": 100,
            "message": f"Ready to download ({size_str})",
            "file_size": file_size,
            "file_size_mb": round(file_size_mb, 1)
        }
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        download_progress[audiobook_id] = {
            "status": "error",
            "error": str(e)
        }
    finally:
        # Clean up temp files
        if concat_file and os.path.exists(concat_file.name):
            try:
                os.unlink(concat_file.name)
            except:
                pass
        for temp_file in temp_files:
            try:
                os.unlink(temp_file)
            except:
                pass


@router.post("/{audiobook_id:path}/prepare-download")
async def prepare_download(audiobook_id: str, background_tasks: BackgroundTasks):
    """
    Start preparing the audiobook for download (combining chunks in background).
    Returns immediately so the user can continue using the app.
    """
    if audiobook_id not in audiobooks_db:
        raise HTTPException(status_code=404, detail="Audiobook not found")
    
    audiobook = audiobooks_db[audiobook_id]
    
    if audiobook.status != GenerationStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="Audiobook generation is not complete")
    
    if not audiobook.audio_chunks:
        raise HTTPException(status_code=400, detail="No audio chunks available")
    
    audiobook_dir = settings.AUDIOBOOKS_DIR / audiobook_id
    combined_path = audiobook_dir / "combined_download.mp3"
    
    # Check if already ready
    if combined_path.exists():
        file_size = combined_path.stat().st_size
        file_size_mb = file_size / (1024 * 1024)
        download_progress[audiobook_id] = {
            "status": "ready",
            "progress": 100,
            "message": f"Ready to download ({file_size_mb:.1f} MB)",
            "file_size": file_size,
            "file_size_mb": round(file_size_mb, 1)
        }
        return {"status": "ready", "message": "Download already prepared"}
    
    # Check if already in progress
    if audiobook_id in download_progress and download_progress[audiobook_id].get("status") == "combining":
        return {"status": "in_progress", "message": "Already preparing download"}
    
    # Start background task
    background_tasks.add_task(combine_audiobook_chunks, audiobook_id)
    
    return {"status": "started", "message": "Preparing download..."}


@router.get("/{audiobook_id:path}/download-status")
async def get_download_status(audiobook_id: str):
    """Get the status of download preparation"""
    if audiobook_id not in audiobooks_db:
        raise HTTPException(status_code=404, detail="Audiobook not found")
    
    audiobook_dir = settings.AUDIOBOOKS_DIR / audiobook_id
    combined_path = audiobook_dir / "combined_download.mp3"
    
    # If file exists but no progress tracked, it's ready
    if combined_path.exists():
        file_size = combined_path.stat().st_size
        file_size_mb = file_size / (1024 * 1024)
        return {
            "status": "ready",
            "progress": 100,
            "message": f"Ready to download ({file_size_mb:.1f} MB)",
            "file_size": file_size,
            "file_size_mb": round(file_size_mb, 1)
        }
    
    if audiobook_id in download_progress:
        return download_progress[audiobook_id]
    
    return {"status": "not_started", "progress": 0, "message": "Not prepared yet"}


@router.get("/{audiobook_id:path}/download")
async def download_audiobook(audiobook_id: str):
    """
    Download the combined audiobook MP3 file.
    File must be prepared first using /prepare-download endpoint.
    """
    if audiobook_id not in audiobooks_db:
        raise HTTPException(status_code=404, detail="Audiobook not found")
    
    audiobook = audiobooks_db[audiobook_id]
    audiobook_dir = settings.AUDIOBOOKS_DIR / audiobook_id
    combined_path = audiobook_dir / "combined_download.mp3"
    
    if not combined_path.exists():
        raise HTTPException(status_code=400, detail="Download not prepared. Call /prepare-download first.")
    
    # Clean filename for download
    safe_title = "".join(c if c.isalnum() or c in ' -_' else '_' for c in audiobook.title)
    combined_filename = f"{safe_title}.mp3"
    
    return FileResponse(
        path=str(combined_path),
        filename=combined_filename,
        media_type='audio/mpeg',
        headers={
            'Content-Disposition': f'attachment; filename="{combined_filename}"'
        }
    )


# IMPORTANT: This route must be LAST because :path is greedy and will match everything
# All more specific routes (with /chunks, /lrc, etc.) must be defined BEFORE this one
@router.get("/{audiobook_id:path}", response_model=AudiobookMetadata)
async def get_audiobook(audiobook_id: str):
    """Get audiobook metadata by ID"""
    print(f"[CATCH-ALL] GET /{{audiobook_id:path}} called with: {audiobook_id}")
    print(f"[CATCH-ALL] Available audiobooks: {list(audiobooks_db.keys())}")
    if audiobook_id not in audiobooks_db:
        print(f"[CATCH-ALL] ERROR: Audiobook not found: {audiobook_id}")
        raise HTTPException(status_code=404, detail=f"Audiobook not found: {audiobook_id}")
    return audiobooks_db[audiobook_id]
