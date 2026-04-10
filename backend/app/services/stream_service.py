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
        Parse ebook and return full text with chapter information and text chunks
        Uses the EXACT same chunking as audio generation
        Returns: {
            "title": str,
            "full_text": str,
            "chapters": [{"name": str, "start_idx": int, "end_idx": int, "start_chunk": int, "end_chunk": int}],
            "chunks": [{"index": int, "start_idx": int, "end_idx": int, "text": str, "chapter_index": int}],
            "total_chars": int,
            "total_chunks": int
        }
        """
        # Check cache
        cache_key = f"{self._get_cache_key(ebook_path)}:{chunk_size}"
        if cache_key in self._cache:
            print(f"[DEBUG] Returning cached ebook data for {ebook_path}")
            return self._cache[cache_key]
        
        print(f"[DEBUG] Parsing ebook for streaming: {ebook_path}")
        full_path = self._resolve_ebook_path(ebook_path)
        
        # Parse ebook chapters (same as audio generation)
        chapters_data = self.ebook_parser.parse_ebook(full_path)
        
        # Build text chunks using EXACT same logic as audio generation
        all_text_chunks = []
        chapters = []
        chunk_index = 0
        current_char_pos = 0
        
        for chapter_idx, chapter_data in enumerate(chapters_data):
            chapter_start_chunk = chunk_index
            chapter_start_char = current_char_pos
            
            # Use the SAME chunk_text method as audio generation
            text_chunks = self.ebook_parser.chunk_text(
                chapter_data['text'],
                chunk_size
            )
            
            # Add chunks with metadata
            for text_chunk in text_chunks:
                chunk_start_char = current_char_pos
                chunk_end_char = current_char_pos + len(text_chunk)
                
                all_text_chunks.append({
                    "index": chunk_index,
                    "start_idx": chunk_start_char,
                    "end_idx": chunk_end_char,
                    "text": text_chunk,
                    "length": len(text_chunk),
                    "chapter_index": chapter_idx
                })
                
                current_char_pos = chunk_end_char
                chunk_index += 1
            
            chapter_end_chunk = chunk_index - 1
            chapter_end_char = current_char_pos
            
            chapters.append({
                "name": chapter_data.get('chapter', 'Unknown Chapter'),
                "start_idx": chapter_start_char,
                "end_idx": chapter_end_char,
                "start_chunk": chapter_start_chunk,
                "end_chunk": chapter_end_chunk,
                "length": chapter_end_char - chapter_start_char
            })
        
        result = {
            "title": Path(ebook_path).stem,
            "chapters": chapters,
            "chunks": all_text_chunks,
            "total_chars": current_char_pos,
            "total_chunks": len(all_text_chunks)
        }
        
        # Cache result
        self._cache[cache_key] = result
        
        print(f"[DEBUG] Parsed ebook: {result['total_chars']} chars, {len(chapters)} chapters, {len(all_text_chunks)} chunks")
        return result
    
    def parse_ebook_with_images(self, ebook_path: str, chunk_size: int = 4096) -> Dict:
        """
        Parse ebook with images for streaming mode
        Returns same structure as parse_ebook_for_streaming but with image data for display.
        Stores clean_text (without markers) for audio generation.
        """
        # Check cache with images
        cache_key = f"{self._get_cache_key(ebook_path)}:{chunk_size}:with_images"
        if cache_key in self._cache:
            print(f"[DEBUG] Returning cached ebook data with images for {ebook_path}")
            return self._cache[cache_key]
        
        print(f"[DEBUG] Parsing ebook with images for streaming: {ebook_path}")
        full_path = self._resolve_ebook_path(ebook_path)
        
        # Parse ebook chapters with images
        chapters_data, all_images = self.ebook_parser.parse_ebook_with_images(full_path)
        
        # Build text chunks using same logic as regular parsing
        all_text_chunks = []
        chapters = []
        chunk_index = 0
        current_char_pos = 0  # Position in CLEAN text (for audio sync)
        
        import re
        marker_pattern = re.compile(r'<<<IMAGE_\d+>>>')
        
        for chapter_idx, chapter_data in enumerate(chapters_data):
            chapter_start_chunk = chunk_index
            chapter_start_char = current_char_pos
            chapter_text_with_markers = chapter_data.get('text', '')
            image_markers = chapter_data.get('image_markers', [])
            
            # Get clean text (without markers) for chunking and audio
            # IMPORTANT: We must normalize spaces to match what chunk_text() produces
            clean_chapter_text_raw = marker_pattern.sub('', chapter_text_with_markers)
            clean_chapter_text = re.sub(r' +', ' ', clean_chapter_text_raw).strip()
            
            # Build a map of positions in NORMALIZED clean text where images should appear
            # We need to track positions accounting for space normalization
            image_positions = []  # List of (normalized_clean_text_position, marker_info)
            
            # Walk through marked text and track position in normalized clean text
            normalized_clean_pos = 0
            marked_pos = 0
            last_was_space = False  # Track for space normalization
            
            while marked_pos < len(chapter_text_with_markers):
                marker_match = marker_pattern.match(chapter_text_with_markers[marked_pos:])
                if marker_match:
                    marker = marker_match.group()
                    # Find corresponding marker_info
                    for marker_info in image_markers:
                        if marker_info['marker'] == marker:
                            image_positions.append((normalized_clean_pos, marker_info))
                            break
                    marked_pos += len(marker)
                    # Don't increment normalized_clean_pos - marker doesn't exist in clean text
                else:
                    char = chapter_text_with_markers[marked_pos]
                    is_space = char == ' '
                    
                    # Only count this character if it's not a duplicate space
                    if is_space:
                        if not last_was_space and normalized_clean_pos > 0:
                            # This space will appear in normalized text
                            normalized_clean_pos += 1
                        # Skip duplicate spaces or leading spaces
                        last_was_space = True
                    else:
                        normalized_clean_pos += 1
                        last_was_space = False
                    
                    marked_pos += 1
            
            # Use the SAME chunk_text method as audio generation on CLEAN text
            text_chunks = self.ebook_parser.chunk_text(clean_chapter_text, chunk_size)
            
            # Track cumulative position in clean chapter text
            chapter_clean_pos = 0
            
            # Add chunks with metadata and inline image markers
            for i, clean_text_chunk in enumerate(text_chunks):
                chunk_start_char = current_char_pos
                chunk_end_char = current_char_pos + len(clean_text_chunk)
                
                # Find where this chunk appears in the clean chapter text
                # (chunk_text may strip/modify, so we need to find it)
                chunk_start_in_chapter = clean_chapter_text.find(clean_text_chunk, chapter_clean_pos)
                if chunk_start_in_chapter == -1:
                    # Fallback: just use current position
                    chunk_start_in_chapter = chapter_clean_pos
                chunk_end_in_chapter = chunk_start_in_chapter + len(clean_text_chunk)
                
                # Find images that fall within this chunk's range
                chunk_image_data = []
                for img_pos, marker_info in image_positions:
                    if chunk_start_in_chapter <= img_pos <= chunk_end_in_chapter:
                        # Calculate position within the chunk for display
                        pos_in_chunk = img_pos - chunk_start_in_chapter
                        chunk_image_data.append({
                            'id': marker_info['id'],
                            'marker': marker_info['marker'],
                            'position': pos_in_chunk
                        })
                
                # Build display text by inserting markers at their positions
                # Sort by position (reverse order so insertions don't shift positions)
                display_text = clean_text_chunk
                for img_data in sorted(chunk_image_data, key=lambda x: x['position'], reverse=True):
                    pos = img_data['position']
                    marker = img_data['marker']
                    display_text = display_text[:pos] + marker + display_text[pos:]
                
                # Update positions in chunk_image_data to reflect final display_text positions
                # Recalculate since we modified the string
                final_image_data = []
                for img_data in chunk_image_data:
                    actual_pos = display_text.find(img_data['marker'])
                    if actual_pos != -1:
                        final_image_data.append({
                            'id': img_data['id'],
                            'marker': img_data['marker'],
                            'position': actual_pos
                        })
                
                all_text_chunks.append({
                    "index": chunk_index,
                    "start_idx": chunk_start_char,  # Position in clean text (for audio)
                    "end_idx": chunk_end_char,
                    "text": clean_text_chunk,  # Clean text for audio
                    "display_text": display_text,  # Text with markers for display
                    "length": len(clean_text_chunk),
                    "chapter_index": chapter_idx,
                    "image_data": final_image_data  # Contains position info in display_text
                })
                
                # Update position tracking
                chapter_clean_pos = chunk_end_in_chapter
                current_char_pos = chunk_end_char
                chunk_index += 1
            
            # If no text chunks but there are images, create a chunk for them
            if not text_chunks and image_markers:
                chunk_image_data = [{'id': m['id'], 'marker': m['marker'], 'position': 0} for m in image_markers]
                display_text = ''.join(m['marker'] for m in image_markers)
                all_text_chunks.append({
                    "index": chunk_index,
                    "start_idx": current_char_pos,
                    "end_idx": current_char_pos,
                    "text": "",
                    "display_text": display_text,
                    "length": 0,
                    "chapter_index": chapter_idx,
                    "image_data": chunk_image_data
                })
                chunk_index += 1
            
            chapter_end_chunk = max(chapter_start_chunk, chunk_index - 1)
            chapter_end_char = current_char_pos
            
            chapters.append({
                "name": chapter_data.get('chapter', 'Unknown Chapter'),
                "start_idx": chapter_start_char,
                "end_idx": chapter_end_char,
                "start_chunk": chapter_start_chunk,
                "end_chunk": chapter_end_chunk,
                "length": chapter_end_char - chapter_start_char
            })
        
        result = {
            "title": Path(ebook_path).stem,
            "chapters": chapters,
            "chunks": all_text_chunks,
            "images": all_images,
            "total_chars": current_char_pos,
            "total_chunks": len(all_text_chunks)
        }
        
        # Cache result
        self._cache[cache_key] = result
        
        print(f"[DEBUG] Parsed ebook with images: {result['total_chars']} chars, {len(chapters)} chapters, {len(all_text_chunks)} chunks, {len(all_images)} images")
        return result
    
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