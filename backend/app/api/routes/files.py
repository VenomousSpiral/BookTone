from fastapi import APIRouter, UploadFile, File, HTTPException, Query, Body
from fastapi.responses import FileResponse
from typing import List, Optional
from pathlib import Path
from pydantic import BaseModel
import shutil
from app.core.config import settings
from app.services.file_manager import FileManager

router = APIRouter()
file_manager = FileManager()

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
    """Upload an ebook file"""
    try:
        file_path = file_manager.save_uploaded_file(file, path)
        return {
            "message": "File uploaded successfully",
            "filename": file.filename,
            "path": str(file_path)
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.delete("/delete")
async def delete_file(file_path: str):
    """Delete an ebook file"""
    try:
        file_manager.delete_file(file_path)
        return {"message": "File deleted successfully", "path": file_path}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/move")
async def move_file(request: MoveFileRequest):
    """Move a file to a different directory"""
    try:
        new_path = file_manager.move_file(request.source, request.destination)
        return {
            "message": "File moved successfully",
            "old_path": request.source,
            "new_path": str(new_path)
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
