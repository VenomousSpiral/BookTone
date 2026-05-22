"""
Streaming service for on-demand TTS generation
This service handles text-based streaming where audio is generated on demand
"""
from pathlib import Path
from typing import Optional, Dict, List, Tuple
import json
import hashlib
from datetime import datetime
from openai import OpenAI
import httpx
import io

from app.services.ebook_parser import EbookParser
from app.core.config import settings
from app.models.streaming import StreamProgress


class StreamChapter:
    """Represents a chapter in the streaming book"""
    def __init__(self, name: str, start_idx: int, end_idx: int, text: str):
        self.name = name
        self.start_idx = start_idx  # Start character index in full text
        self.end_idx = end_idx      # End character index in full text
        self.text = text


class StreamService:
    """Service for streaming TTS generation"""
    
    def __init__(self):
        self.ebook_parser = EbookParser()
        self.settings_file = settings.STORAGE_DIR / "stream_settings.json"
        self.progress_file = settings.STORAGE_DIR / "stream_progress.json"
        self._cache = {}  # Cache for parsed ebooks
        self._hash_cache = {}  # Cache for file hashes {path: (mtime, hash)}
        self._progress_db = {}  # In-memory progress database
        self._load_progress_db()
    
    def _scrub_text(self, text: str, scrub_chars: str) -> str:
        """
        Remove specified characters from text before TTS generation.
        
        Args:
            text: The text to scrub
            scrub_chars: String of characters to remove (e.g., ":[]{}*~")
        
        Returns:
            Text with specified characters removed
        """
        if not scrub_chars:
            return text
        
        # Remove each character from the text
        scrubbed = text
        for char in scrub_chars:
            scrubbed = scrubbed.replace(char, '')
        
        return scrubbed

    def _find_chunk_for_char_pos(self, ebook_path: str, char_pos: int) -> Optional[int]:
        """
        Find the chunk index that contains the given character position.
        Uses binary search on chunk_time_index if available (B-11),
        falls back to linear scan.
        """
        # Try cached data first
        cache_key_prefix = f"{self._get_cache_key(ebook_path)}:"
        for key, data in self._cache.items():
            if key.startswith(cache_key_prefix) and 'chunks' in data:
                chunks = data.get('chunks', [])
                time_index = data.get('chunk_time_index')
                if time_index:
                    # Binary search (B-11)
                    lo, hi = 0, len(time_index) - 1
                    while lo <= hi:
                        mid = (lo + hi) >> 1
                        if time_index[mid]['start_time'] <= char_pos:
                            lo = mid + 1
                        else:
                            hi = mid - 1
                    idx = lo - 1
                    if 0 <= idx < len(chunks):
                        return chunks[idx]['index']
                else:
                    # Linear scan fallback
                    for chunk in chunks:
                        if chunk['start_idx'] <= char_pos < chunk['end_idx']:
                            return chunk['index']
                break

        # Parse fresh as fallback
        ebook_data = self.parse_ebook_for_streaming(ebook_path)
        chunks = ebook_data.get('chunks', [])
        time_index = ebook_data.get('chunk_time_index')
        if time_index:
            lo, hi = 0, len(time_index) - 1
            while lo <= hi:
                mid = (lo + hi) >> 1
                if time_index[mid]['start_time'] <= char_pos:
                    lo = mid + 1
                else:
                    hi = mid - 1
            idx = lo - 1
            if 0 <= idx < len(chunks):
                return chunks[idx]['index']
        else:
            for chunk in chunks:
                if chunk['start_idx'] <= char_pos < chunk['end_idx']:
                    return chunk['index']
        return None

    def _load_progress_db(self):
        """Load streaming progress database from disk"""
        if self.progress_file.exists():
            try:
                with open(self.progress_file, 'r') as f:
                    data = json.load(f)
                    for ebook_path, progress_data in data.items():
                        self._progress_db[ebook_path] = StreamProgress(**progress_data)
                print(f"[DEBUG] Loaded {len(self._progress_db)} streaming progress records")
            except Exception as e:
                print(f"[ERROR] Failed to load streaming progress: {e}")
    
    def _save_progress_db(self):
        """Save streaming progress database to disk"""
        try:
            data = {}
            for ebook_path, progress in self._progress_db.items():
                data[ebook_path] = progress.model_dump()
            with open(self.progress_file, 'w') as f:
                json.dump(data, f, indent=2, default=str)
        except Exception as e:
            print(f"[ERROR] Failed to save streaming progress: {e}")
            raise
    
    def get_progress(self, ebook_path: str) -> StreamProgress:
        """Get streaming progress for an ebook"""
        if ebook_path not in self._progress_db:
            self._progress_db[ebook_path] = StreamProgress(ebook_path=ebook_path)
        return self._progress_db[ebook_path]
    
    def update_progress(self, ebook_path: str, chunk_index: int):
        """Update current position for an ebook"""
        progress = self.get_progress(ebook_path)
        progress.current_chunk = chunk_index
        progress.last_updated = datetime.now()
        self._save_progress_db()
    
    def toggle_bookmark(self, ebook_path: str, chunk_index: int, text_preview: str = "") -> bool:
        """
        Toggle bookmark for a chunk
        Returns True if bookmark was added, False if removed
        
        Args:
            ebook_path: Path to the ebook
            chunk_index: Index of the chunk to bookmark
            text_preview: Text preview to store with bookmark (only used when adding)
        """
        progress = self.get_progress(ebook_path)
        
        if progress.has_bookmark(chunk_index):
            progress.remove_bookmark(chunk_index)
            self._save_progress_db()
            return False
        else:
            progress.add_bookmark(chunk_index, text_preview)
            self._save_progress_db()
            return True
    
    def clear_progress(self, ebook_path: str):
        """Clear progress for an ebook"""
        if ebook_path in self._progress_db:
            del self._progress_db[ebook_path]
            self._save_progress_db()

    
    def load_settings(self) -> Dict:
        """Load streaming settings (model, voice preferences)"""
        if self.settings_file.exists():
            try:
                with open(self.settings_file, 'r') as f:
                    return json.load(f)
            except Exception as e:
                print(f"[ERROR] Failed to load stream settings: {e}")
        
        # Return defaults
        return {
            "font_size": 16,
            "font_family": "system",
            "preferred_model": None,
            "preferred_voice": None,
            "progress_mode": "book",
            "time_mode": "total",
            "show_title": True,
            "show_progress_bar": True,
            "show_images": False,
            "save_stream_audio": False,
            "sleep_timer_minutes": 0,
            "show_sleep_timer": False
        }
    
    def save_settings(self, settings_data: Dict):
        """Save streaming settings"""
        try:
            with open(self.settings_file, 'w') as f:
                json.dump(settings_data, f, indent=2)
        except Exception as e:
            print(f"[ERROR] Failed to save stream settings: {e}")
            raise
    
    def _compute_ebook_hash(self, ebook_path: Path) -> str:
        """Compute MD5 hash of ebook file, with caching based on mtime"""
        path_str = str(ebook_path)
        current_mtime = ebook_path.stat().st_mtime
        
        # Check if we have a cached hash and file hasn't changed
        if path_str in self._hash_cache:
            cached_mtime, cached_hash = self._hash_cache[path_str]
            if cached_mtime == current_mtime:
                return cached_hash
        
        # Compute new hash
        print(f"[DEBUG] Computing hash for {ebook_path}...")
        hash_md5 = hashlib.md5()
        with open(ebook_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_md5.update(chunk)
        file_hash = hash_md5.hexdigest()
        
        # Cache it
        self._hash_cache[path_str] = (current_mtime, file_hash)
        return file_hash
    
    def _get_cache_key(self, ebook_path: str) -> str:
        """Generate cache key for ebook"""
        full_path = self._resolve_ebook_path(ebook_path)
        file_hash = self._compute_ebook_hash(full_path)
        return f"{ebook_path}:{file_hash}"
    
    def _resolve_ebook_path(self, ebook_path: str) -> Path:
        """Resolve ebook path to full path"""
        path = Path(ebook_path)
        if not path.exists():
            full_path = settings.EBOOKS_DIR / ebook_path
            if not full_path.exists():
                raise FileNotFoundError(f"Ebook not found: {ebook_path}")
            return full_path
        return path
    
    def parse_ebook_for_streaming(self, ebook_path: str, chunk_size: int = 4096) -> Dict:
        """
        Parse ebook using cached result from upload-time pre-processing.
        Falls back to on-the-fly parsing if no cache exists.
        Returns same structure as before, plus optional chunk_time_index.
        """
        full_path = self._resolve_ebook_path(ebook_path)
        data = self.ebook_parser.parse_and_cache(full_path, with_images=False)

        # Ensure chunk_size is respected (pre-processing always uses 4096)
        return data
    
    def parse_ebook_with_images(self, ebook_path: str, chunk_size: int = 4096) -> Dict:
        """
        Parse ebook with images using cached result from upload-time pre-processing.
        Falls back to on-the-fly parsing if no cache exists.
        """
        full_path = self._resolve_ebook_path(ebook_path)
        data = self.ebook_parser.parse_and_cache(full_path, with_images=True)
        return data
    
    def get_image(self, ebook_path: str, image_id: str) -> Optional[str]:
        """Get a specific image by ID (returns base64 data URL)"""
        # Try to get from cached data
        cache_key_prefix = f"{self._get_cache_key(ebook_path)}:"
        for key, data in self._cache.items():
            if key.startswith(cache_key_prefix) and 'images' in data:
                if image_id in data['images']:
                    return data['images'][image_id]
        
        # If not in cache, parse with images
        result = self.parse_ebook_with_images(ebook_path)
        return result.get('images', {}).get(image_id)
    
    def _get_model_config(self, model_name: str) -> Optional[Dict]:
        """Get configuration for a specific model"""
        if not settings.MODELS_CONFIG_FILE.exists():
            return None
        
        try:
            with open(settings.MODELS_CONFIG_FILE, 'r') as f:
                models = json.load(f)
                return models.get(model_name)
        except Exception as e:
            print(f"[ERROR] Failed to load model config: {e}")
            return None
    
    def _get_openai_client(self, model_config: Optional[Dict]) -> OpenAI:
        """Create OpenAI client with optional custom config"""
        base_url = None
        api_key = None
        
        if model_config:
            base_url = model_config.get('base_url')
            api_key = model_config.get('api_key')
        
        if not base_url and settings.OPENAI_BASE_URL:
            base_url = settings.OPENAI_BASE_URL
        
        if not api_key and settings.OPENAI_API_KEY:
            api_key = settings.OPENAI_API_KEY
        
        if not api_key:
            api_key = 'not-needed'
        
        http_client = httpx.Client(timeout=120.0)  # Longer timeout for streaming
        
        return OpenAI(api_key=api_key, base_url=base_url, http_client=http_client)
    
    def _get_stream_cache_dir(self, ebook_path: str, model: str, voice: str) -> Path:
        """
        Get the stream audio cache directory for a specific ebook+model+voice combo.
        Structure: storage/audiobooks/_stream_cache_{ebook_stem}_{hash}/{model}_{voice}/
        """
        full_path = self._resolve_ebook_path(ebook_path)
        file_hash = self._compute_ebook_hash(full_path)[:12]
        ebook_stem = Path(ebook_path).stem
        # Sanitize stem for filesystem
        safe_stem = "".join(c if c.isalnum() or c in '-_' else '_' for c in ebook_stem)[:50]
        cache_dir = settings.AUDIOBOOKS_DIR / f"_stream_cache_{safe_stem}_{file_hash}" / f"{model}_{voice}"
        return cache_dir

    def _find_chunk_index_by_chars(self, ebook_path: str, start_char: int, end_char: int) -> Optional[int]:
        """Find the chunk index that matches the given char range"""
        # Try to find in cached ebook data
        cache_key_prefix = f"{self._get_cache_key(ebook_path)}:"
        for key, data in self._cache.items():
            if key.startswith(cache_key_prefix) and 'chunks' in data:
                for chunk in data['chunks']:
                    if chunk['start_idx'] == start_char and chunk['end_idx'] == end_char:
                        return chunk['index']
                break
        
        # Fallback: parse the ebook to find the chunk
        ebook_data = self.parse_ebook_for_streaming(ebook_path)
        for chunk in ebook_data['chunks']:
            if chunk['start_idx'] == start_char and chunk['end_idx'] == end_char:
                return chunk['index']
        
        return None

    def get_stream_cache_dir_for_ebook(self, ebook_path: str) -> Optional[Path]:
        """
        Find any stream cache directory for an ebook (regardless of model/voice).
        Returns the base cache dir (parent of model_voice subdirs).
        """
        full_path = self._resolve_ebook_path(ebook_path)
        file_hash = self._compute_ebook_hash(full_path)[:12]
        ebook_stem = Path(ebook_path).stem
        safe_stem = "".join(c if c.isalnum() or c in '-_' else '_' for c in ebook_stem)[:50]
        cache_dir = settings.AUDIOBOOKS_DIR / f"_stream_cache_{safe_stem}_{file_hash}"
        if cache_dir.exists():
            return cache_dir
        return None

    def get_cached_stream_audio_by_chars(self, ebook_path: str, start_char: int, end_char: int, model: str, voice: str) -> Optional[bytes]:
        """
        Check if audio for a specific char range was saved during streaming.
        Returns audio bytes if found, None otherwise.
        """
        cache_dir = self._get_stream_cache_dir(ebook_path, model, voice)
        audio_file = cache_dir / f"audio_{start_char}_{end_char}.mp3"
        if audio_file.exists():
            print(f"[STREAM CACHE] Found cached audio for chars {start_char}-{end_char}: {audio_file}")
            return audio_file.read_bytes()
        return None

    def find_stream_cache_covering_range(self, cache_model_dir: Path, start_char: int, end_char: int) -> Optional[Path]:
        """
        Find a cached stream audio file that exactly covers the given text range.
        Stream chunks are larger (4096 chars) so one stream audio file may cover
        multiple smaller audiobook text chunks.
        
        Returns the path to the cache file if found, None otherwise.
        """
        if not cache_model_dir or not cache_model_dir.exists():
            return None
        
        # Look for a cached file that contains our range
        for audio_file in cache_model_dir.glob("audio_*.mp3"):
            try:
                # Parse the filename: audio_{start}_{end}.mp3
                parts = audio_file.stem.split('_')
                if len(parts) == 3 and parts[0] == 'audio':
                    cached_start = int(parts[1])
                    cached_end = int(parts[2])
                    if cached_start <= start_char and cached_end >= end_char:
                        return audio_file
            except (ValueError, IndexError):
                continue
        return None

    def generate_audio_for_text(
        self,
        text: str,
        model: str,
        voice: str,
        ebook_path: str = None,
        start_char: int = None,
        end_char: int = None
    ) -> bytes:
        """
        Generate audio for a specific text segment
        Returns audio data as bytes (MP3)
        Optionally saves to stream cache if save_stream_audio setting is enabled.
        """
        print(f"[DEBUG] Generating audio - model: {model}, voice: {voice}, text length: {len(text)}")
        
        # Check if we have cached stream audio for this char range
        if ebook_path and start_char is not None and end_char is not None:
            cached_audio = self.get_cached_stream_audio_by_chars(ebook_path, start_char, end_char, model, voice)
            if cached_audio:
                print(f"[DEBUG] Returning cached stream audio for chars {start_char}-{end_char}")
                return cached_audio
        
        # Get model config
        model_config = self._get_model_config(model)
        api_model = model_config.get('api_model', model) if model_config else model
        
        # Apply text scrubbing if configured for this model
        text_scrub_chars = model_config.get('text_scrub_chars') if model_config else None
        if text_scrub_chars:
            original_len = len(text)
            text = self._scrub_text(text, text_scrub_chars)
            print(f"[DEBUG] Text scrubbed: {original_len} -> {len(text)} chars (removed: {text_scrub_chars})")
        
        # Create client
        client = self._get_openai_client(model_config)
        
        # Generate audio
        try:
            response = client.audio.speech.create(
                model=api_model,
                voice=voice,
                input=text
            )
            
            # Read audio data
            audio_data = response.read()
            print(f"[DEBUG] Generated audio: {len(audio_data)} bytes")
            
            # Save to stream cache if setting is enabled and we have char range info
            if ebook_path and start_char is not None and end_char is not None:
                stream_settings = self.load_settings()
                if stream_settings.get('save_stream_audio', False):
                    try:
                        cache_dir = self._get_stream_cache_dir(ebook_path, model, voice)
                        cache_dir.mkdir(parents=True, exist_ok=True)
                        audio_file = cache_dir / f"audio_{start_char}_{end_char}.mp3"
                        audio_file.write_bytes(audio_data)
                        print(f"[STREAM CACHE] Saved audio for chars {start_char}-{end_char}: {audio_file}")
                    except Exception as e:
                        print(f"[STREAM CACHE ERROR] Failed to save audio: {e}")
            
            return audio_data
            
        except Exception as e:
            print(f"[ERROR] TTS generation failed: {e}")
            raise
    
    def get_text_segment(
        self,
        ebook_path: str,
        start_char: int,
        end_char: int
    ) -> str:
        """
        Get a segment of text from the ebook by character range
        """
        ebook_data = self.parse_ebook_for_streaming(ebook_path)
        
        # Find the chunk(s) that contain this character range
        text_segments = []
        for chunk in ebook_data["chunks"]:
            # Check if this chunk overlaps with our range
            if chunk["start_idx"] < end_char and chunk["end_idx"] > start_char:
                # Calculate the overlap
                chunk_start = max(0, start_char - chunk["start_idx"])
                chunk_end = min(len(chunk["text"]), end_char - chunk["start_idx"])
                text_segments.append(chunk["text"][chunk_start:chunk_end])
        
        return "".join(text_segments)
    
    def find_chapter_at_position(
        self,
        ebook_path: str,
        char_position: int
    ) -> Optional[Dict]:
        """
        Find which chapter contains the given character position
        """
        ebook_data = self.parse_ebook_for_streaming(ebook_path)
        
        for chapter in ebook_data["chapters"]:
            if chapter["start_idx"] <= char_position < chapter["end_idx"]:
                return chapter
        
        return None

    def get_cache_status(self, ebook_path: str, model: str = None, voice: str = None) -> Dict:
        """
        Get information about cached stream audio for an ebook.
        Returns cache size, number of cached chunks, and cache location.
        """
        import os
        
        try:
            full_path = self._resolve_ebook_path(ebook_path)
            file_hash = self._compute_ebook_hash(full_path)[:12]
            ebook_stem = Path(ebook_path).stem
            safe_stem = "".join(c if c.isalnum() or c in '-_' else '_' for c in ebook_stem)[:50]
            base_cache_dir = settings.AUDIOBOOKS_DIR / f"_stream_cache_{safe_stem}_{file_hash}"
            
            if not base_cache_dir.exists():
                return {
                    "has_cache": False,
                    "total_size_bytes": 0,
                    "total_size_mb": 0,
                    "cached_chunks": 0,
                    "model_voice_caches": []
                }
            
            model_voice_caches = []
            total_size = 0
            total_chunks = 0
            
            for mv_dir in base_cache_dir.iterdir():
                if not mv_dir.is_dir():
                    continue
                
                # Parse model_voice from directory name
                dir_name = mv_dir.name
                cache_info = {
                    "model_voice": dir_name,
                    "files": 0,
                    "size_bytes": 0,
                    "size_mb": 0
                }
                
                for audio_file in mv_dir.glob("audio_*.mp3"):
                    cache_info["files"] += 1
                    cache_info["size_bytes"] += audio_file.stat().st_size
                
                cache_info["size_mb"] = round(cache_info["size_bytes"] / (1024 * 1024), 2)
                total_size += cache_info["size_bytes"]
                total_chunks += cache_info["files"]
                
                # Filter by model/voice if specified
                if model and voice:
                    if dir_name == f"{model}_{voice}":
                        model_voice_caches.append(cache_info)
                else:
                    model_voice_caches.append(cache_info)
            
            return {
                "has_cache": total_chunks > 0,
                "total_size_bytes": total_size,
                "total_size_mb": round(total_size / (1024 * 1024), 2),
                "cached_chunks": total_chunks,
                "model_voice_caches": model_voice_caches
            }
            
        except FileNotFoundError:
            return {
                "has_cache": False,
                "total_size_bytes": 0,
                "total_size_mb": 0,
                "cached_chunks": 0,
                "model_voice_caches": []
            }
    
    def clear_stream_cache(self, ebook_path: str, model: str = None, voice: str = None) -> Dict:
        """
        Clear cached stream audio for an ebook.
        If model/voice specified, only clears that specific cache.
        Otherwise clears all caches for this ebook.
        """
        import shutil
        
        try:
            full_path = self._resolve_ebook_path(ebook_path)
            file_hash = self._compute_ebook_hash(full_path)[:12]
            ebook_stem = Path(ebook_path).stem
            safe_stem = "".join(c if c.isalnum() or c in '-_' else '_' for c in ebook_stem)[:50]
            base_cache_dir = settings.AUDIOBOOKS_DIR / f"_stream_cache_{safe_stem}_{file_hash}"
            
            if not base_cache_dir.exists():
                return {"message": "No cache found", "deleted_files": 0, "deleted_size_mb": 0}
            
            deleted_files = 0
            deleted_size = 0
            
            if model and voice:
                # Delete specific model/voice cache
                mv_dir = base_cache_dir / f"{model}_{voice}"
                if mv_dir.exists():
                    for audio_file in mv_dir.glob("audio_*.mp3"):
                        deleted_size += audio_file.stat().st_size
                        audio_file.unlink()
                        deleted_files += 1
                    # Remove directory if empty
                    try:
                        mv_dir.rmdir()
                    except OSError:
                        pass  # Not empty, leave it
            else:
                # Delete entire cache directory
                for mv_dir in base_cache_dir.iterdir():
                    if mv_dir.is_dir():
                        for audio_file in mv_dir.glob("audio_*.mp3"):
                            deleted_size += audio_file.stat().st_size
                            audio_file.unlink()
                            deleted_files += 1
                        try:
                            mv_dir.rmdir()
                        except OSError:
                            pass
                
                # Try to remove base cache dir if empty
                try:
                    base_cache_dir.rmdir()
                except OSError:
                    pass
            
            return {
                "message": f"Deleted {deleted_files} cached audio files",
                "deleted_files": deleted_files,
                "deleted_size_mb": round(deleted_size / (1024 * 1024), 2)
            }
            
        except FileNotFoundError:
            return {"message": "Ebook not found", "deleted_files": 0, "deleted_size_mb": 0}