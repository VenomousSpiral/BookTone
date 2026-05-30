"""
API routes for cache management (cache-first audiobook generation).

Endpoints:
    GET    /api/stream/cache-info
    POST   /api/stream/generate-cache
    DELETE /api/stream/cache
    POST   /api/stream/cache-pause
    POST   /api/stream/cache-resume
    GET    /api/stream/cache-status
    POST   /api/stream/clear-cache
    DELETE /api/stream/parse-cache
    GET    /api/stream/parse-cache-status
    GET    /api/stream/parse-cache-list
    DELETE /api/stream/parse-cache-all
"""
from pathlib import Path
from fastapi import APIRouter, HTTPException, BackgroundTasks, Query
from fastapi.responses import JSONResponse

from app.services.stream_audiobook_service import stream_audiobook_service
from app.services import stream_service
from app.core.config import settings
from app.utils.validators import validate_ebook_path

router = APIRouter()



# ========== AUDIO CACHE ENDPOINTS ==========


@router.get("/cache-info")
async def get_cache_info(ebook_path: str = Query(...)):
    """Get detailed cache information for an ebook (cache-first approach)."""
    ebook_path = validate_ebook_path(ebook_path)
    try:
        info = stream_audiobook_service.get_ebook_cache_info(ebook_path)
        if info is None:
            return {
                "ebook_path": ebook_path,
                "title": Path(ebook_path).stem,
                "total_chunks": 0,
                "has_cache": False,
                "caches": [],
            }
        return info
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/generate-cache")
async def generate_cache(
    ebook_path: str = Query(...),
    model: str = Query(...),
    voice: str = Query(...),
    background_tasks: BackgroundTasks = None,
):
    """Start (or resume) generation for a specific cache entry."""
    ebook_path = validate_ebook_path(ebook_path)
    try:
        result = stream_audiobook_service.start_generation_for_cache(
            ebook_path, model, voice, background_tasks
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/cache")
async def delete_cache(
    ebook_path: str = Query(...),
    model: str = Query(...),
    voice: str = Query(...),
):
    """Delete cache files and profile for a specific model/voice."""
    ebook_path = validate_ebook_path(ebook_path)
    try:
        result = stream_audiobook_service.delete_cache(ebook_path, model, voice)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/cache-pause")
async def cache_pause(
    ebook_path: str = Query(...),
    model: str = Query(...),
    voice: str = Query(...),
):
    """Pause generation for a cache entry."""
    ebook_path = validate_ebook_path(ebook_path)
    try:
        result = stream_audiobook_service.pause_generation(ebook_path, model, voice)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/cache-resume")
async def cache_resume(
    ebook_path: str = Query(...),
    model: str = Query(...),
    voice: str = Query(...),
    background_tasks: BackgroundTasks = None,
):
    """Resume generation for a paused/failed cache entry."""
    ebook_path = validate_ebook_path(ebook_path)
    try:
        result = stream_audiobook_service.resume_generation(
            ebook_path, model, voice, background_tasks
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/cache-status")
async def get_cache_status(ebook_path: str = Query(...)):
    """Get information about cached stream audio for an ebook."""
    ebook_path = validate_ebook_path(ebook_path)
    try:
        status = stream_service.get_cache_status(ebook_path)
        return status
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/clear-cache")
async def clear_cache(ebook_path: str = Query(...)):
    """Clear all cached stream audio for an ebook."""
    ebook_path = validate_ebook_path(ebook_path)
    try:
        result = stream_service.clear_stream_cache(ebook_path)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ========== PARSE CACHE ENDPOINTS ==========


@router.get("/parse-cache-status")
async def get_parse_cache_status(ebook_path: str = Query(...)):
    """Get information about parsed ebook cache (not stream audio cache)."""
    ebook_path = validate_ebook_path(ebook_path)
    try:
        full_path = stream_service._resolve_ebook_path(ebook_path)
        parser = stream_service.ebook_parser

        cache_file_no_img = parser._get_cache_file(full_path, with_images=False)
        cache_file_img = parser._get_cache_file(full_path, with_images=True)

        return {
            "ebook_path": ebook_path,
            "parsed_cache": {
                "exists": cache_file_no_img.exists(),
                "size_bytes": cache_file_no_img.stat().st_size if cache_file_no_img.exists() else 0,
                "size_mb": (
                    round(cache_file_no_img.stat().st_size / (1024 * 1024), 2)
                    if cache_file_no_img.exists()
                    else 0
                ),
                "with_images": {
                    "exists": cache_file_img.exists(),
                    "size_bytes": cache_file_img.stat().st_size if cache_file_img.exists() else 0,
                    "size_mb": (
                        round(cache_file_img.stat().st_size / (1024 * 1024), 2)
                        if cache_file_img.exists()
                        else 0
                    ),
                },
            },
            "in_memory_cache": ebook_path in str(list(parser._parse_cache.keys())),
        }
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/parse-cache")
async def clear_parse_cache(ebook_path: str = Query(...)):
    """Clear parsed ebook cache (not stream audio cache)."""
    ebook_path = validate_ebook_path(ebook_path)
    try:
        full_path = stream_service._resolve_ebook_path(ebook_path)
        parser = stream_service.ebook_parser
        parser.clear_cache(full_path, with_images=False)
        parser.clear_cache(full_path, with_images=True)
        return {"message": "Parsed ebook cache cleared"}
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/parse-cache-list")
async def list_parse_cache():
    """List all parsed ebook cache files with sizes."""
    cache_dir = settings.STORAGE_DIR / "stream_cache"
    if not cache_dir.exists():
        return {"caches": [], "total_size_mb": 0}

    caches = []
    total_size = 0
    for f in sorted(cache_dir.glob("*.json")):
        size = f.stat().st_size
        total_size += size
        caches.append({
            "filename": f.name,
            "size_bytes": size,
            "size_mb": round(size / (1024 * 1024), 2),
            "with_images": "_with_images" in f.name,
        })

    return {
        "caches": caches,
        "total_count": len(caches),
        "total_size_mb": round(total_size / (1024 * 1024), 2),
    }


@router.delete("/parse-cache-all")
async def clear_all_parse_cache():
    """Clear ALL parsed ebook cache files from disk and memory."""
    cache_dir = settings.STORAGE_DIR / "stream_cache"
    deleted_count = 0
    deleted_size = 0

    if cache_dir.exists():
        for f in cache_dir.glob("*.json"):
            deleted_size += f.stat().st_size
            f.unlink()
            deleted_count += 1

    for service in [stream_service]:
        service.ebook_parser._parse_cache.clear()

    return {
        "message": f"Cleared {deleted_count} cache files",
        "deleted_count": deleted_count,
        "deleted_size_mb": round(deleted_size / (1024 * 1024), 2),
    }
