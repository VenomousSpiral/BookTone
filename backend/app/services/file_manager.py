from pathlib import Path
from typing import List, Dict, Optional
from fastapi import UploadFile
import shutil
import hashlib
import uuid
import logging
from app.core.config import settings

logger = logging.getLogger(__name__)

class FileManager:
    """Manage ebook files and directories"""
    
    def __init__(self):
        self.base_dir = settings.EBOOKS_DIR
    
    def _compute_file_hash(self, file_path: Path) -> str:
        """Compute MD5 hash of file content."""
        hash_md5 = hashlib.md5()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_md5.update(chunk)
        return hash_md5.hexdigest()
    
    def _get_parse_cache_size(self, filename: str) -> float:
        """Get total size of parse cache files for a given filename stem."""
        safe_stem = "".join(c if c.isalnum() or c in '-_' else '_' for c in Path(filename).stem)[:50]
        cache_dir = settings.STORAGE_DIR / "stream_cache"
        total = 0.0
        if cache_dir.exists():
            for cache_file in cache_dir.glob(f"{safe_stem}_*.json"):
                try:
                    total += cache_file.stat().st_size
                except OSError:
                    pass
        return total / (1024 * 1024)
    
    def _get_stream_cache_info(self, ebook_path: str) -> Dict:
        """Get stream cache info for an ebook path."""
        from app.utils.path_utils import _resolve_ebook_path, safe_stem
        
        base_dir = settings.AUDIOBOOKS_DIR
        ebook_stem = Path(ebook_path).stem
        safe = safe_stem(ebook_stem)
        full_path = _resolve_ebook_path(ebook_path, base_dir)
        
        try:
            stat = full_path.stat()
            file_hash = hashlib.md5(f"{stat.st_mtime}:{stat.st_size}".encode()).hexdigest()[:12]
        except Exception:
            file_hash = "unknown"
        
        cache_base = base_dir / f"_stream_cache_{safe}_{file_hash}"
        total_size = 0
        file_count = 0
        
        if cache_base.exists():
            for model_dir in cache_base.iterdir():
                if not model_dir.is_dir():
                    continue
                for voice_dir in model_dir.iterdir():
                    if not voice_dir.is_dir():
                        continue
                    for audio_file in voice_dir.glob("audio_*.opus"):
                        try:
                            total_size += audio_file.stat().st_size
                            file_count += 1
                        except OSError:
                            pass
                    for audio_file in voice_dir.glob("audio_*.mp3"):
                        try:
                            total_size += audio_file.stat().st_size
                            file_count += 1
                        except OSError:
                            pass
        
        return {
            "size_mb": round(total_size / (1024 * 1024), 2),
            "count": file_count
        }
    
    def find_duplicate_files(self, filename: str) -> List[Dict]:
        """
        Find all existing files with the same filename in any subdirectory.
        
        Args:
            filename: The basename of the file to find duplicates for.
        
        Returns:
            List of dicts with path, filename, size, file_hash, modified,
            parse_cache_size_mb, stream_cache_size_mb, stream_cache_count.
        """
        duplicates = []
        
        # Use rglob to find all files matching the filename
        for item in self.base_dir.rglob(filename):
            if not item.is_file():
                continue
            if item.name != filename:
                continue
            
            rel_path = str(item.relative_to(self.base_dir))
            
            try:
                stat = item.stat()
                file_hash = self._compute_file_hash(item)
                cache_info = self._get_stream_cache_info(rel_path)
                parse_cache_size = self._get_parse_cache_size(filename)
                
                duplicates.append({
                    "path": rel_path,
                    "filename": item.name,
                    "size": stat.st_size,
                    "file_hash": file_hash,
                    "modified": stat.st_mtime,
                    "parse_cache_size_mb": round(parse_cache_size, 2),
                    "stream_cache_size_mb": cache_info["size_mb"],
                    "stream_cache_count": cache_info["count"],
                })
            except OSError as e:
                logger.warning(f"Could not stat file {item}: {e}")
        
        return duplicates
    
    def replace_cache_to_new_hash(
        self,
        old_ebook_path: str,
        new_ebook_path: str,
        old_file_hash: str,
        new_file_hash: str,
    ) -> Dict:
        """
        Rename cache directories from old hash to new hash.
        
        Returns migration summary with parse_cache and stream_cache status.
        """
        from app.utils.path_utils import safe_stem, _resolve_ebook_path
        
        result = {"parse_cache": False, "stream_cache": 0, "errors": []}
        
        old_stem = Path(old_ebook_path).stem
        new_stem = Path(new_ebook_path).stem
        old_safe = safe_stem(old_stem)
        new_safe = safe_stem(new_stem)
        
        # 1. Rename parse cache
        old_parse = settings.STORAGE_DIR / "stream_cache" / f"{old_safe}_{old_file_hash}.json"
        new_parse = settings.STORAGE_DIR / "stream_cache" / f"{new_safe}_{new_file_hash}.json"
        
        # Also handle _with_images variant
        old_parse_img = settings.STORAGE_DIR / "stream_cache" / f"{old_safe}_{old_file_hash}_with_images.json"
        new_parse_img = settings.STORAGE_DIR / "stream_cache" / f"{new_safe}_{new_file_hash}_with_images.json"
        
        if old_parse.exists():
            if new_parse.exists():
                result["errors"].append(f"Parse cache target already exists: {new_parse.name}")
            else:
                try:
                    shutil.move(str(old_parse), str(new_parse))
                    result["parse_cache"] = True
                    logger.info(f"[MERGE] Replaced parse cache: {old_parse.name} -> {new_parse.name}")
                except OSError as e:
                    result["errors"].append(f"Failed to replace parse cache: {e}")
        
        if old_parse_img.exists():
            if new_parse_img.exists():
                result["errors"].append(f"Parse cache (images) target already exists: {new_parse_img.name}")
            else:
                try:
                    shutil.move(str(old_parse_img), str(new_parse_img))
                    logger.info(f"[MERGE] Replaced parse cache (images): {old_parse_img.name} -> {new_parse_img.name}")
                except OSError as e:
                    result["errors"].append(f"Failed to replace parse cache (images): {e}")
        
        # 2. Rename stream cache (audiobook)
        old_stream_base = _resolve_ebook_path(old_ebook_path, settings.AUDIOBOOKS_DIR)
        old_hash = old_file_hash[:12]
        new_hash = new_file_hash[:12]
        
        old_stream_dir = settings.AUDIOBOOKS_DIR / f"_stream_cache_{old_safe}_{old_hash}"
        new_stream_dir = settings.AUDIOBOOKS_DIR / f"_stream_cache_{new_safe}_{new_hash}"
        
        if old_stream_dir.exists():
            if new_stream_dir.exists():
                result["errors"].append(f"Stream cache target already exists: {new_stream_dir.name}")
            else:
                try:
                    shutil.move(str(old_stream_dir), str(new_stream_dir))
                    # Count model/voice combos
                    combo_count = sum(1 for _ in new_stream_dir.iterdir() if _.is_dir())
                    result["stream_cache"] = combo_count
                    logger.info(f"[MERGE] Replaced stream cache: {old_stream_dir.name} -> {new_stream_dir.name}")
                except OSError as e:
                    result["errors"].append(f"Failed to replace stream cache: {e}")
        
        return result
    
    def copy_cache_to_new_hash(
        self,
        old_ebook_path: str,
        new_ebook_path: str,
        old_file_hash: str,
        new_file_hash: str,
    ) -> Dict:
        """
        Copy cache data from old hash dirs to new hash dirs.
        Old dirs are preserved; new dirs are created.
        
        Returns migration summary.
        """
        from app.utils.path_utils import safe_stem
        
        result = {"parse_cache": False, "stream_cache": 0, "bytes_copied": 0, "errors": []}
        
        old_stem = Path(old_ebook_path).stem
        new_stem = Path(new_ebook_path).stem
        old_safe = safe_stem(old_stem)
        new_safe = safe_stem(new_stem)
        
        # 1. Copy parse cache
        old_parse = settings.STORAGE_DIR / "stream_cache" / f"{old_safe}_{old_file_hash}.json"
        new_parse = settings.STORAGE_DIR / "stream_cache" / f"{new_safe}_{new_file_hash}.json"
        
        if old_parse.exists():
            new_parse.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(str(old_parse), str(new_parse))
                result["parse_cache"] = True
                result["bytes_copied"] += old_parse.stat().st_size
                logger.info(f"[MERGE] Copied parse cache: {old_parse.name} -> {new_parse.name}")
            except OSError as e:
                result["errors"].append(f"Failed to copy parse cache: {e}")
        
        old_parse_img = settings.STORAGE_DIR / "stream_cache" / f"{old_safe}_{old_file_hash}_with_images.json"
        new_parse_img = settings.STORAGE_DIR / "stream_cache" / f"{new_safe}_{new_file_hash}_with_images.json"
        
        if old_parse_img.exists():
            if new_parse_img.exists():
                # Remove existing target first for copy mode
                new_parse_img.unlink()
            new_parse_img.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(str(old_parse_img), str(new_parse_img))
                result["bytes_copied"] += old_parse_img.stat().st_size
                logger.info(f"[MERGE] Copied parse cache (images): {old_parse_img.name} -> {new_parse_img.name}")
            except OSError as e:
                result["errors"].append(f"Failed to copy parse cache (images): {e}")
        
        # 2. Copy stream cache
        old_hash = old_file_hash[:12]
        new_hash = new_file_hash[:12]
        old_stream_dir = settings.AUDIOBOOKS_DIR / f"_stream_cache_{old_safe}_{old_hash}"
        new_stream_dir = settings.AUDIOBOOKS_DIR / f"_stream_cache_{new_safe}_{new_hash}"
        
        if old_stream_dir.exists():
            new_stream_dir.parent.mkdir(parents=True, exist_ok=True)
            try:
                if new_stream_dir.exists():
                    shutil.rmtree(str(new_stream_dir))
                shutil.copytree(str(old_stream_dir), str(new_stream_dir))
                
                # Count model/voice combos
                combo_count = sum(1 for _ in new_stream_dir.iterdir() if _.is_dir())
                result["stream_cache"] = combo_count
                
                # Calculate bytes copied
                for root, dirs, files in new_stream_dir.walk():
                    for f in files:
                        result["bytes_copied"] += (root / f).stat().st_size
                
                logger.info(f"[MERGE] Copied stream cache: {old_stream_dir.name} -> {new_stream_dir.name}")
            except OSError as e:
                result["errors"].append(f"Failed to copy stream cache: {e}")
        
        return result
    
    def save_uploaded_file_atomic(self, file: UploadFile, subpath: str = "") -> Path:
        """
        Save an uploaded file atomically to avoid race conditions.
        Uses temp file + rename pattern.
        """
        target_dir = self.base_dir / subpath
        target_dir.mkdir(parents=True, exist_ok=True)
        
        # Generate unique temp filename (hidden, so it won't show in listings)
        temp_filename = f".tmp_{uuid.uuid4().hex[:8]}_{file.filename}"
        temp_path = target_dir / temp_filename
        
        # Write to temp file
        with open(temp_path, 'wb') as f:
            shutil.copyfileobj(file.file, f)
        
        # Atomically move to final location (rename is atomic on same filesystem)
        final_path = target_dir / file.filename
        shutil.move(str(temp_path), str(final_path))
        
        return final_path.relative_to(self.base_dir)
    
    def check_active_generation(self, ebook_path: str) -> Optional[Dict]:
        """
        Check if audiobook generation is in progress for a given ebook path.
        Returns generation info if active, None otherwise.
        """
        try:
            from app.services.stream_audiobook_service import stream_audiobook_service
            status = stream_audiobook_service.queue.get_queue_status()
            current = status.get("current")
            
            if current and current.get("ebook_path") == ebook_path:
                return {
                    "active": True,
                    "model": current.get("model"),
                    "voice": current.get("voice"),
                    "paused": current.get("paused", False)
                }
            
            # Check queue
            for item in status.get("queue", []):
                if item.get("ebook_path") == ebook_path:
                    return {
                        "active": True,
                        "queued": True,
                        "model": item.get("model"),
                        "voice": item.get("voice")
                    }
        except Exception as e:
            logger.warning(f"[MERGE] Error checking active generation: {e}")
        
        return None
    
    def cleanup_temp_files(self, max_age_seconds: int = 3600) -> int:
        """
        Remove temporary upload files older than max_age_seconds.
        Returns the number of files removed.
        """
        import time
        removed = 0
        now = time.time()
        
        for item in self.base_dir.rglob(".tmp_*"):
            if item.is_file() and item.name.startswith(".tmp_"):
                try:
                    age = now - item.stat().st_mtime
                    if age > max_age_seconds:
                        item.unlink()
                        removed += 1
                        logger.info(f"[MERGE] Cleaned up temp file: {item.name} (age={age:.0f}s)")
                except OSError as e:
                    logger.warning(f"[MERGE] Could not remove temp file {item}: {e}")
        
        return removed
    
    def list_files(self, subpath: str = "", limit: int = 100, offset: int = 0) -> List[Dict]:
        """List files and directories in a path with pagination"""
        target_dir = self.base_dir / subpath

        if not target_dir.exists():
            raise ValueError(f"Directory not found: {subpath}")

        items = []
        count = 0
        skipped = 0

        for item in sorted(target_dir.iterdir()):
            if skipped < offset:
                skipped += 1
                continue
            if count >= limit:
                break

            rel_path = item.relative_to(self.base_dir)
            is_dir = item.is_dir()

            items.append({
                'name': item.name,
                'path': str(rel_path),
                'is_directory': is_dir,
                'size': item.stat().st_size if not is_dir else 0,
                'modified': item.stat().st_mtime
            })
            count += 1

        return items
    
    def save_uploaded_file(self, file: UploadFile, subpath: str = "") -> Path:
        """Save an uploaded file (non-atomic, use save_uploaded_file_atomic for new code)"""
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
