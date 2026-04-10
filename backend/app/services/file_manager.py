from pathlib import Path
from typing import List, Dict
from fastapi import UploadFile
import shutil
from app.core.config import settings

class FileManager:
    """Manage ebook files and directories"""
    
    def __init__(self):
        self.base_dir = settings.EBOOKS_DIR
    
    def list_files(self, subpath: str = "") -> List[Dict]:
        """List files and directories in a path"""
        target_dir = self.base_dir / subpath
        
        if not target_dir.exists():
            raise ValueError(f"Directory not found: {subpath}")
        
        items = []
        
        for item in sorted(target_dir.iterdir()):
            rel_path = item.relative_to(self.base_dir)
            
            items.append({
                'name': item.name,
                'path': str(rel_path),
                'is_directory': item.is_dir(),
                'size': item.stat().st_size if item.is_file() else 0,
                'modified': item.stat().st_mtime
            })
        
        return items
    
    def save_uploaded_file(self, file: UploadFile, subpath: str = "") -> Path:
        """Save an uploaded file"""
        target_dir = self.base_dir / subpath
        target_dir.mkdir(parents=True, exist_ok=True)
        
        file_path = target_dir / file.filename
        
        with open(file_path, 'wb') as f:
            shutil.copyfileobj(file.file, f)
        
        return file_path.relative_to(self.base_dir)
    
    def delete_file(self, file_path: str):
        """Delete a file"""
        target = self.base_dir / file_path
        
        if not target.exists():
            raise ValueError(f"File not found: {file_path}")
        
        if target.is_file():
            target.unlink()
        elif target.is_dir():
            shutil.rmtree(target)
        else:
            raise ValueError(f"Invalid file type: {file_path}")
    
    def move_file(self, source: str, destination: str) -> Path:
        """Move a file to a different location"""
        source_path = self.base_dir / source
        dest_path = self.base_dir / destination
        
        if not source_path.exists():
            raise ValueError(f"Source not found: {source}")
        
        # If destination is a directory, move file into it
        if dest_path.is_dir():
            dest_path = dest_path / source_path.name
        else:
            dest_path.parent.mkdir(parents=True, exist_ok=True)
        
        shutil.move(str(source_path), str(dest_path))
        
        return dest_path.relative_to(self.base_dir)
    
    def create_directory(self, dir_path: str) -> Path:
        """Create a new directory"""
        target = self.base_dir / dir_path
        target.mkdir(parents=True, exist_ok=True)
        return target.relative_to(self.base_dir)
