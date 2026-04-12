"""
API routes for streaming mode (on-demand TTS generation)
"""
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse, Response
from pydantic import BaseModel
from typing import Optional, List
import io

from app.services.stream_service import StreamService
from app.core.config import settings

router = APIRouter()
stream_service = StreamService()


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
    text_preview: Optional[str] = ""  # Text preview to store with bookmark


class TextBatchRequest(BaseModel):
    """Request to get text for multiple chunks at once"""
    ebook_path: str
    chunk_indices: List[int]
    with_images: bool = False


@router.get("/parse")
async def parse_ebook(ebook_path: str, chunk_size: int = 4096, with_images: bool = False):
    """
    Parse an ebook and return its structure with chunks
    Returns chapters, chunks metadata (without full text to reduce size)
    If with_images=True, also returns image IDs for each chunk
    """
    try:
        if with_images:
            result = stream_service.parse_ebook_with_images(ebook_path, chunk_size)
            # Return chunks metadata with image IDs
            chunks_metadata = [
                {
                    "index": chunk["index"],
                    "start_idx": chunk["start_idx"],
                    "end_idx": chunk["end_idx"],
                    "length": chunk["length"],
                    "images": chunk.get("images", [])
                }
                for chunk in result["chunks"]
            ]
            
            return {
                "title": result["title"],
                "chapters": result["chapters"],
                "chunks": chunks_metadata,
                "total_chars": result["total_chars"],
                "total_chunks": result["total_chunks"],
                "has_images": bool(result.get("images"))
            }
        else:
            result = stream_service.parse_ebook_for_streaming(ebook_path, chunk_size)
            
            # Return chunks without text content to reduce response size
            chunks_metadata = [
                {
                    "index": chunk["index"],
                    "start_idx": chunk["start_idx"],
                    "end_idx": chunk["end_idx"],
                    "length": chunk["length"]
                }
                for chunk in result["chunks"]
            ]
            
            return {
                "title": result["title"],
                "chapters": result["chapters"],
                "chunks": chunks_metadata,
                "total_chars": result["total_chars"],
                "total_chunks": result["total_chunks"]
            }
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/text")
async def get_text_segment(
    ebook_path: str,
    start_char: int = None,
    end_char: int = None,
    chunk_index: int = None,
    with_images: bool = False
):
    """
    Get a segment of text from the ebook by char range or chunk index
    If with_images=True, also returns image IDs for the chunk
    """
    try:
        if chunk_index is not None:
            # Get text by chunk index
            if with_images:
                ebook_data = stream_service.parse_ebook_with_images(ebook_path)
            else:
                ebook_data = stream_service.parse_ebook_for_streaming(ebook_path)
            
            if chunk_index < 0 or chunk_index >= len(ebook_data["chunks"]):
                raise HTTPException(status_code=400, detail="Invalid chunk index")
            
            chunk = ebook_data["chunks"][chunk_index]
            response = {
                "text": chunk["text"],  # Clean text for audio
                "start_char": chunk["start_idx"],
                "end_char": chunk["end_idx"],
                "chunk_index": chunk_index
            }
            
            if with_images:
                response["display_text"] = chunk.get("display_text", chunk["text"])  # Text with markers for display
                response["image_data"] = chunk.get("image_data", [])
            
            return response
        else:
            # Get text by char range
            text = stream_service.get_text_segment(ebook_path, start_char, end_char)
            return {"text": text, "start_char": start_char, "end_char": end_char}
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/text-batch")
async def get_text_batch(request: TextBatchRequest):
    """
    Get text for multiple chunks in one request.
    Returns a dict of chunk_index -> chunk data.
    Much more efficient than fetching each chunk individually.
    """
    try:
        if request.with_images:
            ebook_data = stream_service.parse_ebook_with_images(request.ebook_path)
        else:
            ebook_data = stream_service.parse_ebook_for_streaming(request.ebook_path)

        chunks = ebook_data["chunks"]
        result = {}

        for idx in request.chunk_indices:
            if idx < 0 or idx >= len(chunks):
                continue
            chunk = chunks[idx]
            chunk_data = {
                "text": chunk["text"],
                "start_char": chunk["start_idx"],
                "end_char": chunk["end_idx"],
                "chunk_index": idx
            }
            if request.with_images:
                chunk_data["display_text"] = chunk.get("display_text", chunk["text"])
                chunk_data["image_data"] = chunk.get("image_data", [])
            result[str(idx)] = chunk_data

        return {"chunks": result}
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/image")
async def get_image(ebook_path: str, image_id: str):
    """
    Get image data by ID (returns base64 data URL)
    """
    try:
        image_data = stream_service.get_image(ebook_path, image_id)
        if not image_data:
            raise HTTPException(status_code=404, detail="Image not found")
        return {"image_id": image_id, "data": image_data}
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/audio")
async def generate_audio(request: StreamAudioRequest):
    """
    Generate audio for a text segment on-demand
    Returns audio file (MP3)
    """
    import time
    request_start = time.time()
    print(f"[AUDIO] Request received: chars {request.start_char}-{request.end_char}, model={request.model}, voice={request.voice}")
    
    try:
        # Get text segment
        text_start = time.time()
        text = stream_service.get_text_segment(
            request.ebook_path,
            request.start_char,
            request.end_char
        )
        text_time = time.time() - text_start
        print(f"[AUDIO] Text extraction took {text_time*1000:.0f}ms, length={len(text)} chars")
        
        if not text.strip():
            raise HTTPException(status_code=400, detail="Text segment is empty")
        
        # Generate audio
        gen_start = time.time()
        audio_data = stream_service.generate_audio_for_text(
            text,
            request.model,
            request.voice,
            ebook_path=request.ebook_path,
            start_char=request.start_char,
            end_char=request.end_char
        )
        gen_time = time.time() - gen_start
        total_time = time.time() - request_start
        print(f"[AUDIO] TTS generation took {gen_time*1000:.0f}ms, audio size={len(audio_data)} bytes")
        print(f"[AUDIO] Total request time: {total_time*1000:.0f}ms")
        
        # Return as streaming response
        return Response(
            content=audio_data,
            media_type="audio/mpeg",
            headers={
                "Content-Disposition": "inline",
                "Cache-Control": "public, max-age=3600",  # Cache for 1 hour
                "Accept-Ranges": "bytes"
            }
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        print(f"[ERROR] Audio generation failed: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/chapter")
async def get_chapter_at_position(ebook_path: str, char_position: int):
    """
    Get chapter information at a specific character position
    """
    try:
        chapter = stream_service.find_chapter_at_position(ebook_path, char_position)
        if not chapter:
            return {"chapter": None}
        return {"chapter": chapter}
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/settings")
async def get_stream_settings():
    """
    Get streaming settings (model/voice preferences, display settings)
    """
    try:
        settings_data = stream_service.load_settings()
        return settings_data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/settings")
async def update_stream_settings(request: UpdateStreamSettingsRequest):
    """
    Update streaming settings
    """
    try:
        # Load current settings
        current_settings = stream_service.load_settings()
        
        # Update with new values (only if provided)
        if request.preferred_model is not None:
            current_settings["preferred_model"] = request.preferred_model
        if request.preferred_voice is not None:
            current_settings["preferred_voice"] = request.preferred_voice
        if request.font_size is not None:
            current_settings["font_size"] = request.font_size
        if request.font_family is not None:
            current_settings["font_family"] = request.font_family
        if request.progress_mode is not None:
            current_settings["progress_mode"] = request.progress_mode
        if request.time_mode is not None:
            current_settings["time_mode"] = request.time_mode
        if request.show_title is not None:
            current_settings["show_title"] = request.show_title
        if request.show_progress_bar is not None:
            current_settings["show_progress_bar"] = request.show_progress_bar
        if request.show_images is not None:
            current_settings["show_images"] = request.show_images
        if request.save_stream_audio is not None:
            current_settings["save_stream_audio"] = request.save_stream_audio
        if request.sleep_timer_minutes is not None:
            current_settings["sleep_timer_minutes"] = request.sleep_timer_minutes
        if request.show_sleep_timer is not None:
            current_settings["show_sleep_timer"] = request.show_sleep_timer
        
        # Save
        stream_service.save_settings(current_settings)
        
        return {"message": "Settings updated", "settings": current_settings}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/progress")
async def get_progress(ebook_path: str):
    """
    Get streaming progress for an ebook
    """
    try:
        progress = stream_service.get_progress(ebook_path)
        data = progress.model_dump()
        # Add bookmark_indices for backwards compatibility with frontend
        data["bookmark_indices"] = progress.bookmark_indices
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/progress")
async def update_progress(request: UpdateProgressRequest):
    """
    Update streaming progress (save position)
    """
    try:
        stream_service.update_progress(request.ebook_path, request.chunk_index)
        return {"message": "Progress updated", "chunk_index": request.chunk_index}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/bookmark")
async def toggle_bookmark(request: ToggleBookmarkRequest):
    """
    Toggle bookmark for a chunk
    """
    try:
        added = stream_service.toggle_bookmark(request.ebook_path, request.chunk_index, request.text_preview or "")
        progress = stream_service.get_progress(request.ebook_path)
        
        return {
            "message": f"Bookmark {'added' if added else 'removed'}",
            "chunk_index": request.chunk_index,
            "bookmarks": progress.bookmarks,
            "bookmark_indices": progress.bookmark_indices
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/bookmarks")
async def get_bookmarks(ebook_path: str):
    """
    Get all bookmarks for an ebook
    """
    try:
        progress = stream_service.get_progress(ebook_path)
        return {
            "bookmarks": progress.bookmarks,
            "bookmark_indices": progress.bookmark_indices
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/progress")
async def clear_progress(ebook_path: str):
    """
    Clear progress for an ebook
    """
    try:
        stream_service.clear_progress(ebook_path)
        return {"message": "Progress cleared"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/cache-status")
async def get_cache_status(ebook_path: str, model: str = None, voice: str = None):
    """
    Get information about cached stream audio for an ebook
    """
    try:
        status = stream_service.get_cache_status(ebook_path, model, voice)
        return status
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/cache")
async def clear_cache(ebook_path: str, model: str = None, voice: str = None):
    """
    Clear cached stream audio for an ebook.
    If model/voice specified, only clears that specific cache.
    Otherwise clears all caches for this ebook.
    """
    try:
        result = stream_service.clear_stream_cache(ebook_path, model, voice)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
