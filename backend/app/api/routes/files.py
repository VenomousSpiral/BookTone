from fastapi import APIRouter, UploadFile, File, HTTPException, Query
from fastapi.responses import FileResponse
from typing import Dict, List, Optional
from pathlib import Path
from pydantic import BaseModel
import threading
import time
import logging
from app.core.config import settings
from app.services.file_manager import FileManager
from app.services import stream_service
from app.services.ebook_parser import EbookParser
from app.utils.validators import validate_ebook_path

router = APIRouter()
file_manager = FileManager()
logger = logging.getLogger(__name__)

class MoveFileRequest(BaseModel):
    source: str
    destination: str

class CreateDirectoryRequest(BaseModel):
    path: str

class CreateFileRequest(BaseModel):
    path: str
    content: Optional[str] = ""

@router.get("/list")
async def list_files(
    path: str = Query("", description="Subdirectory path"),
    limit: int = Query(100, ge=1, le=500, description="Max items to return"),
    offset: int = Query(0, ge=0, description="Number of items to skip"),
):
    """List ebooks in a directory with pagination"""
    try:
        files = file_manager.list_files(path, limit=limit, offset=offset)
        has_more = len(files) >= limit
        if has_more:
            files = files[:-1]  # Remove the extra item used to detect more
        return {"files": files, "path": path, "has_more": has_more}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    path: str = Query("", description="Target subdirectory")
):
    """Upload an ebook file (with background pre-parsing)"""
    try:
        file_path = file_manager.save_uploaded_file(file, path)

        _logger = logging.getLogger("ebook_parser")
        _logger.debug("[UPLOAD] File saved: file=%s", file_path)

        # Background pre-parse: build disk cache so streaming is instant
        def _pre_parse():
            _logger.debug("[UPLOAD] Pre-parse thread STARTED: file=%s", file_path)
            t0 = time.time()
            try:
                parser = EbookParser()
                parser.parse_and_cache(file_path, with_images=False)
                if file_path.suffix.lower() in ('.epub', '.pdf'):
                    parser.parse_and_cache(file_path, with_images=True)
                elapsed = time.time() - t0
                _logger.debug("[UPLOAD] Pre-parse thread COMPLETED: file=%s took=%.2fs", file_path, elapsed)
            except Exception as e:
                elapsed = time.time() - t0
                _logger.error("[UPLOAD] Pre-parse thread FAILED: file=%s took=%.2fs error=%s", file_path, elapsed, e)

        threading.Thread(target=_pre_parse, daemon=True).start()
        _logger.debug("[UPLOAD] Pre-parse thread started (non-blocking)")

        return {
            "message": "File uploaded successfully",
            "filename": file.filename,
            "path": str(file_path)
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ========== DUPLICATE DETECTION & MERGE ENDPOINTS ==========

@router.get("/upload-check")
async def check_upload_duplicates(
    filename: str = Query(..., description="Filename to check for duplicates"),
    path: str = Query("", description="Target subdirectory"),
):
    """
    Check if a file with the given filename already exists.
    Returns duplicate info for the client to show a popup menu.
    """
    try:
        duplicates = file_manager.find_duplicate_files(filename)
        
        # Check for active generation on each duplicate
        for dup in duplicates:
            active = file_manager.check_active_generation(dup["path"])
            dup["generation_status"] = "in_progress" if active else "none"
            if active:
                dup["generation_info"] = active
        
        total_cache_size = sum(
            d["parse_cache_size_mb"] + d["stream_cache_size_mb"]
            for d in duplicates
        )
        
        return {
            "has_duplicates": len(duplicates) > 0,
            "duplicates": duplicates,
            "total_cache_size_mb": round(total_cache_size, 2)
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/upload-with-replace-cache")
async def upload_file_replace_cache(
    file: UploadFile = File(...),
    path: str = Query("", description="Target subdirectory"),
    replace_paths: List[str] = Query(default=[""], description="Paths of files to replace caches for"),
):
    """
    Upload a file, replace caches (rename to new hash) for listed files,
    delete old files, save new file.
    """
    try:
        # Compute hashes of duplicates BEFORE saving (they may be overwritten)
        dup_hashes: Dict[str, str] = {}
        for dup_path in replace_paths:
            if not dup_path:
                continue
            old_file = settings.EBOOKS_DIR / dup_path
            if old_file.exists():
                dup_hashes[dup_path] = file_manager._compute_file_hash(old_file)
            else:
                logger.warning(f"[MERGE] Duplicate file not found: {dup_path}")
        
        # Save file atomically (may overwrite duplicates with same path)
        file_path = file_manager.save_uploaded_file_atomic(file, path)
        
        # Compute new file hash
        new_hash = file_manager._compute_file_hash(settings.EBOOKS_DIR / file_path)
        
        replaced_from = []
        all_caches_replaced = {"parse_cache": False, "stream_cache": 0}
        
        for dup_path in replace_paths:
            if not dup_path or dup_path not in dup_hashes:
                continue
            old_hash = dup_hashes[dup_path]
            
            # Replace caches (skip rename when same path — file already overwritten)
            cache_result = file_manager.replace_cache_to_new_hash(
                old_ebook_path=dup_path,
                new_ebook_path=str(file_path),
                old_file_hash=old_hash,
                new_file_hash=new_hash,
            )
            
            # Merge results
            if cache_result["parse_cache"]:
                all_caches_replaced["parse_cache"] = True
            all_caches_replaced["stream_cache"] += cache_result["stream_cache"]
            if cache_result["errors"]:
                all_caches_replaced["errors"] = all_caches_replaced.get("errors", []) + cache_result["errors"]
            
            replaced_from.append(dup_path)
            
            # Delete old file (skip if same path as new file - already overwritten)
            if Path(dup_path).resolve() != Path(file_path).resolve():
                file_manager.delete_file(dup_path)
                logger.info(f"[MERGE] Replaced caches and deleted: {dup_path}")
            else:
                logger.info(f"[MERGE] Replaced caches, file already overwritten: {dup_path}")
        
        # Background pre-parse
        def _pre_parse():
            _logger = logging.getLogger("ebook_parser")
            _logger.debug("[UPLOAD] Pre-parse thread STARTED: file=%s", file_path)
            t0 = time.time()
            try:
                parser = EbookParser()
                parser.parse_and_cache(file_path, with_images=False)
                if file_path.suffix.lower() in ('.epub', '.pdf'):
                    parser.parse_and_cache(file_path, with_images=True)
                elapsed = time.time() - t0
                _logger.debug("[UPLOAD] Pre-parse thread COMPLETED: file=%s took=%.2fs", file_path, elapsed)
            except Exception as e:
                elapsed = time.time() - t0
                _logger.error("[UPLOAD] Pre-parse thread FAILED: file=%s took=%.2fs error=%s", file_path, elapsed, e)
        
        threading.Thread(target=_pre_parse, daemon=True).start()
        
        return {
            "message": "File uploaded and caches replaced",
            "filename": file.filename,
            "path": str(file_path),
            "replaced_from": replaced_from,
            "caches_replaced": all_caches_replaced,
            "old_file_deleted": len(replaced_from) > 0
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/upload-with-copy-cache")
async def upload_file_copy_cache(
    file: UploadFile = File(...),
    path: str = Query("", description="Target subdirectory"),
    copy_paths: List[str] = Query(default=[""], description="Paths of files to copy caches from"),
):
    """
    Upload a file, copy caches from listed files into new dirs,
    delete old files, save new file.
    """
    try:
        # Compute hashes of duplicates BEFORE saving (they may be overwritten)
        dup_hashes: Dict[str, str] = {}
        for dup_path in copy_paths:
            if not dup_path:
                continue
            old_file = settings.EBOOKS_DIR / dup_path
            if old_file.exists():
                dup_hashes[dup_path] = file_manager._compute_file_hash(old_file)
            else:
                logger.warning(f"[MERGE] Duplicate file not found: {dup_path}")
        
        # Save file atomically (may overwrite duplicates with same path)
        file_path = file_manager.save_uploaded_file_atomic(file, path)
        
        # Compute new file hash
        new_hash = file_manager._compute_file_hash(settings.EBOOKS_DIR / file_path)
        
        copied_from = []
        all_caches_copied = {"parse_cache": False, "stream_cache": 0, "bytes_copied": 0}
        
        for dup_path in copy_paths:
            if not dup_path or dup_path not in dup_hashes:
                continue
            old_hash = dup_hashes[dup_path]
            
            # Copy caches
            cache_result = file_manager.copy_cache_to_new_hash(
                old_ebook_path=dup_path,
                new_ebook_path=str(file_path),
                old_file_hash=old_hash,
                new_file_hash=new_hash,
            )
            
            # Merge results
            if cache_result["parse_cache"]:
                all_caches_copied["parse_cache"] = True
            all_caches_copied["stream_cache"] += cache_result["stream_cache"]
            all_caches_copied["bytes_copied"] += cache_result["bytes_copied"]
            if cache_result["errors"]:
                all_caches_copied["errors"] = all_caches_copied.get("errors", []) + cache_result["errors"]
            
            copied_from.append(dup_path)
            
            # Delete old file (skip if same path as new file - already overwritten)
            if Path(dup_path).resolve() != Path(file_path).resolve():
                file_manager.delete_file(dup_path)
                logger.info(f"[MERGE] Copied caches and deleted: {dup_path}")
            else:
                logger.info(f"[MERGE] Copied caches, file already overwritten: {dup_path}")
        
        # Background pre-parse
        def _pre_parse():
            _logger = logging.getLogger("ebook_parser")
            _logger.debug("[UPLOAD] Pre-parse thread STARTED: file=%s", file_path)
            t0 = time.time()
            try:
                parser = EbookParser()
                parser.parse_and_cache(file_path, with_images=False)
                if file_path.suffix.lower() in ('.epub', '.pdf'):
                    parser.parse_and_cache(file_path, with_images=True)
                elapsed = time.time() - t0
                _logger.debug("[UPLOAD] Pre-parse thread COMPLETED: file=%s took=%.2fs", file_path, elapsed)
            except Exception as e:
                elapsed = time.time() - t0
                _logger.error("[UPLOAD] Pre-parse thread FAILED: file=%s took=%.2fs error=%s", file_path, elapsed, e)
        
        threading.Thread(target=_pre_parse, daemon=True).start()
        
        return {
            "message": "File uploaded and caches copied",
            "filename": file.filename,
            "path": str(file_path),
            "copied_from": copied_from,
            "caches_copied": all_caches_copied,
            "old_file_deleted": len(copied_from) > 0,
            "old_cache_preserved": True
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/upload-ignore-cache")
async def upload_file_ignore_cache(
    file: UploadFile = File(...),
    path: str = Query("", description="Target subdirectory"),
    ignored_paths: List[str] = Query(default=[""], description="Paths that were ignored"),
):
    """
    Upload a file normally. Old files with same name are left untouched,
    including their caches. New file gets fresh caches on first play.
    """
    try:
        # Save file atomically
        file_path = file_manager.save_uploaded_file_atomic(file, path)
        
        # Background pre-parse
        def _pre_parse():
            _logger = logging.getLogger("ebook_parser")
            _logger.debug("[UPLOAD] Pre-parse thread STARTED: file=%s", file_path)
            t0 = time.time()
            try:
                parser = EbookParser()
                parser.parse_and_cache(file_path, with_images=False)
                if file_path.suffix.lower() in ('.epub', '.pdf'):
                    parser.parse_and_cache(file_path, with_images=True)
                elapsed = time.time() - t0
                _logger.debug("[UPLOAD] Pre-parse thread COMPLETED: file=%s took=%.2fs", file_path, elapsed)
            except Exception as e:
                elapsed = time.time() - t0
                _logger.error("[UPLOAD] Pre-parse thread FAILED: file=%s took=%.2fs error=%s", file_path, elapsed, e)
        
        threading.Thread(target=_pre_parse, daemon=True).start()
        
        return {
            "message": "File uploaded (caches ignored)",
            "filename": file.filename,
            "path": str(file_path),
            "ignored_files": ignored_paths,
            "old_file_preserved": len(ignored_paths) > 0
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/cleanup-temp")
async def cleanup_temp_files():
    """Clean up temporary upload files older than 1 hour."""
    try:
        removed = file_manager.cleanup_temp_files(max_age_seconds=3600)
        return {
            "message": f"Cleaned up {removed} temp file(s)",
            "removed": removed
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.delete("/delete")
async def delete_file(file_path: str):
    """Delete an ebook file (with cache cleanup)"""
    try:
        # Clear parse cache before deleting the file
        try:
            parser = EbookParser()
            fp = Path(file_path)
            parser.clear_cache(fp, with_images=False)
            parser.clear_cache(fp, with_images=True)
        except Exception:
            pass  # File may not exist yet, or cache already cleared

        file_manager.delete_file(file_path)
        return {"message": "File deleted successfully", "path": file_path}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/move")
async def move_file(request: MoveFileRequest):
    """Move a file or directory to a different location.

    When moving a single ebook file, migrates bookmarks + progress (reading position).
    When moving a directory containing ebooks, recursively migrates all contained
    books' bookmarks and reading positions to their new paths.
    """
    try:
        # Check if the source is a directory using the same base dir as move_file.
        _source_abs = settings.EBOOKS_DIR / request.source
        source_is_dir = _source_abs.is_dir()
        new_path = file_manager.move_file(request.source, request.destination)

        if source_is_dir:
            # Migrate progress for every ebook inside the moved directory tree.
            migrated = stream_service.rename_progress_recursive(
                source_path_str=request.source,
                dest_dir=str(new_path),
            )
            logger.info("[MOVE] Directory move: %s -> %s (migrated %d ebooks)",
                        request.source, new_path, migrated)
        else:
            # Single file — migrate its bookmarks + reading position.
            stream_service.rename_progress(request.source, str(new_path))

        return {
            "message": "Moved successfully",
            "old_path": request.source,
            "new_path": str(new_path),
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/create-directory")
async def create_directory(request: CreateDirectoryRequest):
    """Create a new directory"""
    try:
        dir_path = file_manager.create_directory(request.path)
        return {"message": "Directory created successfully", "path": str(dir_path)}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/create-file")
async def create_file(request: CreateFileRequest):
    """Create a new file with optional content"""
    try:
        full_path = settings.EBOOKS_DIR / request.path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(request.content or "")
        return {"message": "File created successfully", "path": str(full_path)}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/download")
async def download_file(file_path: str):
    """Download a file"""
    try:
        full_path = settings.EBOOKS_DIR / file_path
        if not full_path.exists():
            raise HTTPException(status_code=404, detail="File not found")
        return FileResponse(
            path=str(full_path),
            filename=full_path.name,
            media_type="application/octet-stream"
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
