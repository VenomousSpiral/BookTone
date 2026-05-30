"""
API routes for streaming progress and bookmarks.

Endpoints:
    GET    /api/stream/progress
    POST   /api/stream/progress
    POST   /api/stream/bookmark
    GET    /api/stream/bookmarks
    DELETE /api/stream/progress
"""
from fastapi import APIRouter, HTTPException, Query

from app.services import stream_service
from app.models.streaming_models import (
    UpdateProgressRequest,
    ToggleBookmarkRequest,
)
from app.utils.validators import validate_ebook_path

router = APIRouter()



@router.get("/progress")
async def get_progress(ebook_path: str = Query(...)):
    """Get streaming progress for an ebook."""
    ebook_path = validate_ebook_path(ebook_path)
    try:
        progress = stream_service.get_progress(ebook_path)
        data = progress.model_dump()
        data["bookmark_indices"] = progress.bookmark_indices
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/progress")
async def update_progress(request: UpdateProgressRequest):
    """Update streaming progress (save position)."""
    try:
        stream_service.update_progress(request.ebook_path, request.chunk_index)
        return {"message": "Progress updated", "chunk_index": request.chunk_index}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/bookmark")
async def toggle_bookmark(request: ToggleBookmarkRequest):
    """Toggle bookmark for a chunk."""
    request.ebook_path = validate_ebook_path(request.ebook_path)
    try:
        added = stream_service.toggle_bookmark(
            request.ebook_path, request.chunk_index, request.text_preview or ""
        )
        progress = stream_service.get_progress(request.ebook_path)

        return {
            "message": f"Bookmark {'added' if added else 'removed'}",
            "chunk_index": request.chunk_index,
            "bookmarks": progress.bookmarks,
            "bookmark_indices": progress.bookmark_indices,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/bookmarks")
async def get_bookmarks(ebook_path: str = Query(...)):
    """Get all bookmarks for an ebook."""
    ebook_path = validate_ebook_path(ebook_path)
    try:
        progress = stream_service.get_progress(ebook_path)
        return {
            "bookmarks": progress.bookmarks,
            "bookmark_indices": progress.bookmark_indices,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/progress")
async def clear_progress(ebook_path: str = Query(...)):
    """Clear progress for an ebook."""
    ebook_path = validate_ebook_path(ebook_path)
    try:
        stream_service.clear_progress(ebook_path)
        return {"message": "Progress cleared"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
