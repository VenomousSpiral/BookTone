"""
API routes for text/image/chapter extraction from ebooks.

Endpoints:
    GET    /api/stream/parse
    GET    /api/stream/text
    POST   /api/stream/text-batch
    GET    /api/stream/image
    GET    /api/stream/chapter
"""
import time
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from app.services import stream_service
from app.models.streaming_models import TextBatchRequest
from app.core.config import settings
from app.utils.validators import validate_ebook_path

router = APIRouter()



@router.get("/parse")
async def parse_ebook(
    ebook_path: str = Query(...),
    chunk_size: int = Query(4096),
    with_images: bool = Query(False),
):
    """Parse an ebook and return its structure with chunks."""
    t0 = time.time()
    ebook_path = validate_ebook_path(ebook_path)
    try:
        if with_images:
            result = stream_service.parse_ebook_with_images(ebook_path, chunk_size)
            chunks_metadata = [
                {
                    "index": chunk["index"],
                    "start_idx": chunk["start_idx"],
                    "end_idx": chunk["end_idx"],
                    "length": chunk["length"],
                    "images": chunk.get("image_data", []),
                }
                for chunk in result["chunks"]
            ]
            elapsed = time.time() - t0
            return {
                "title": result["title"],
                "chapters": result["chapters"],
                "chunks": chunks_metadata,
                "total_chars": result["total_chars"],
                "total_chunks": result["total_chunks"],
                "has_images": bool(result.get("images")),
            }
        else:
            result = stream_service.parse_ebook_for_streaming(ebook_path, chunk_size)
            chunks_metadata = [
                {
                    "index": chunk["index"],
                    "start_idx": chunk["start_idx"],
                    "end_idx": chunk["end_idx"],
                    "length": chunk["length"],
                }
                for chunk in result["chunks"]
            ]
            elapsed = time.time() - t0
            return {
                "title": result["title"],
                "chapters": result["chapters"],
                "chunks": chunks_metadata,
                "total_chars": result["total_chars"],
                "total_chunks": result["total_chunks"],
            }
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        elapsed = time.time() - t0
        raise HTTPException(status_code=500, detail=f"Parse failed in {elapsed:.2f}s: {e}")


@router.get("/text")
async def get_text_segment(
    ebook_path: str = Query(...),
    start_char: Optional[int] = None,
    end_char: Optional[int] = None,
    chunk_index: Optional[int] = None,
    with_images: bool = Query(False),
):
    """Get a segment of text from the ebook by char range or chunk index."""
    ebook_path = validate_ebook_path(ebook_path)
    try:
        if chunk_index is not None:
            if with_images:
                ebook_data = stream_service.parse_ebook_with_images(ebook_path)
            else:
                ebook_data = stream_service.parse_ebook_for_streaming(ebook_path)

            if chunk_index < 0 or chunk_index >= len(ebook_data["chunks"]):
                raise HTTPException(status_code=400, detail="Invalid chunk index")

            chunk = ebook_data["chunks"][chunk_index]
            response = {
                "text": chunk["text"],
                "start_char": chunk["start_idx"],
                "end_char": chunk["end_idx"],
                "chunk_index": chunk_index,
            }

            if with_images:
                response["display_text"] = chunk.get("display_text", chunk["text"])
                response["image_data"] = chunk.get("image_data", [])

            return response
        else:
            text = stream_service.get_text_segment(ebook_path, start_char, end_char)
            return {"text": text, "start_char": start_char, "end_char": end_char}
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/text-batch")
async def get_text_batch(request: TextBatchRequest):
    """Get text for multiple chunks in one request."""
    request.ebook_path = validate_ebook_path(request.ebook_path)
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
                "chunk_index": idx,
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
async def get_image(ebook_path: str = Query(...), image_id: str = Query(...)):
    """Get image data by ID (returns base64 data URL)."""
    ebook_path = validate_ebook_path(ebook_path)
    try:
        image_data = stream_service.get_image(ebook_path, image_id)
        if not image_data:
            raise HTTPException(status_code=404, detail="Image not found")
        return {"image_id": image_id, "data": image_data}
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/chapter")
async def get_chapter_at_position(
    ebook_path: str = Query(...), char_position: int = Query(...)
):
    """Get chapter information at a specific character position."""
    ebook_path = validate_ebook_path(ebook_path)
    try:
        chapter = stream_service.find_chapter_at_position(ebook_path, char_position)
        if not chapter:
            return {"chapter": None}
        return {"chapter": chapter}
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
