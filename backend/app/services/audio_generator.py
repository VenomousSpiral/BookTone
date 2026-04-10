from pathlib import Path
from typing import Optional, Dict, List
from datetime import datetime
import uuid
import time
import hashlib
from openai import OpenAI
import httpx
from pydub import AudioSegment
import json

from app.models.audiobook import AudiobookMetadata, GenerationStatus, ChapterInfo, AudioChunk
from app.services.ebook_parser import EbookParser
from app.services.lrc_generator import LRCGenerator
from app.core.config import settings
import re
import os

# Configuration for audio chunking
TEXT_CHUNKS_PER_AUDIO_FILE = 10  # Save audio file every 10 text chunks for faster playback availability

class AudioGenerator:
    """Generate audio from text using OpenAI TTS"""
    
    def __init__(self):
        self.ebook_parser = EbookParser()
        self.lrc_generator = LRCGenerator()
    
    def _is_valid_text_chunk(self, text: str) -> bool:
        """
        Check if a text chunk has enough actual words/letters to generate audio.
        Filters out chunks that are only punctuation, whitespace, or symbols.
        """
        if not text:
            return False
        
        # Remove all punctuation, whitespace, and common symbols
        # Keep only actual letters and numbers (including CJK characters)
        letters_only = re.sub(r'[^a-zA-Z0-9\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff]', '', text)
        
        # Must have at least 2 actual characters to be speakable
        return len(letters_only) >= 2
    
    def _compute_ebook_hash(self, ebook_path: Path) -> str:
        """Compute MD5 hash of ebook file to detect changes"""
        hash_md5 = hashlib.md5()
        with open(ebook_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_md5.update(chunk)
        return hash_md5.hexdigest()
    
    def create_audiobook_metadata(
        self,
        ebook_path: str,
        model: str,
        voice: str,
        instructions: Optional[str] = None
    ) -> AudiobookMetadata:
        """Create metadata for a new audiobook"""
        audiobook_id = str(uuid.uuid4())
        title = Path(ebook_path).stem
        
        now = datetime.now()
        
        return AudiobookMetadata(
            id=audiobook_id,
            title=title,
            source_file=ebook_path,
            model=model,
            voice=voice,
            status=GenerationStatus.PENDING,
            created_at=now,
            updated_at=now
        )
    
    def generate_audiobook(
        self,
        audiobook_id: str,
        audiobooks_db: Dict[str, AudiobookMetadata],
        save_callback=None
    ):
        """
        Generate audio and LRC file for an audiobook using chunked audio files
        This runs in the background
        save_callback: Optional function to call to persist database changes
        """
        try:
            print(f"[DEBUG] Starting generation for audiobook {audiobook_id}")
            audiobook = audiobooks_db[audiobook_id]
            audiobook.status = GenerationStatus.IN_PROGRESS
            audiobook.updated_at = datetime.now()
            
            # Create audiobook directory
            audiobook_dir = settings.AUDIOBOOKS_DIR / audiobook_id
            audiobook_dir.mkdir(parents=True, exist_ok=True)
            
            # Parse ebook
            print(f"[DEBUG] Parsing ebook: {audiobook.source_file}")
            ebook_path = Path(audiobook.source_file)
            if not ebook_path.exists():
                full_path = settings.EBOOKS_DIR / audiobook.source_file
                if not full_path.exists():
                    raise FileNotFoundError(f"Ebook not found: {audiobook.source_file}")
                ebook_path = full_path
            print(f"[DEBUG] Ebook path resolved to: {ebook_path}")
            
            # Check if ebook has changed
            current_hash = self._compute_ebook_hash(ebook_path)
            ebook_changed = audiobook.ebook_hash and audiobook.ebook_hash != current_hash
            
            if ebook_changed:
                print(f"[DEBUG] Ebook has been updated! Old hash: {audiobook.ebook_hash}, New hash: {current_hash}")
                print(f"[DEBUG] Continuing generation from chunk {audiobook.completed_chunks}")
            
            audiobook.ebook_hash = current_hash
            
            chunks_data = self.ebook_parser.parse_ebook(ebook_path)
            all_text_chunks = []
            chunk_char_ranges = []  # Track (start_char, end_char) for each text chunk
            chapter_info = []
            chunk_index = 0
            current_char_pos = 0
            
            for chapter_data in chunks_data:
                chapter_start = chunk_index
                text_chunks = self.ebook_parser.chunk_text(
                    chapter_data['text'],
                    settings.CHUNK_SIZE
                )
                for tc in text_chunks:
                    all_text_chunks.append(tc)
                    chunk_char_ranges.append((current_char_pos, current_char_pos + len(tc)))
                    current_char_pos += len(tc)
                chunk_index += len(text_chunks)
                
                chapter_info.append({
                    'name': chapter_data.get('chapter', 'Unknown Chapter'),
                    'start_chunk': chapter_start,
                    'end_chunk': chunk_index - 1,
                    'chunk_count': len(text_chunks)
                })
            
            audiobook.total_chunks = len(all_text_chunks)
            print(f"[DEBUG] Total text chunks to generate: {len(all_text_chunks)}")
            print(f"[DEBUG] Will create ~{(len(all_text_chunks) + TEXT_CHUNKS_PER_AUDIO_FILE - 1) // TEXT_CHUNKS_PER_AUDIO_FILE} audio files")
            print(f"[DEBUG] Found {len(chapter_info)} chapters")
            
            # Get model config
            model_config = self._get_model_config(audiobook.model)
            print(f"[DEBUG] Model config: {model_config}")
            
            # Get actual API model name
            api_model = model_config.get('api_model', audiobook.model) if model_config else audiobook.model
            print(f"[DEBUG] API model to use: {api_model}")
            
            # Initialize OpenAI client
            client = self._get_openai_client(model_config)
            print(f"[DEBUG] OpenAI client created")
            
            # Check for cached stream audio
            from app.services.stream_service import StreamService
            stream_service = StreamService()
            stream_cache_dir = stream_service.get_stream_cache_dir_for_ebook(audiobook.source_file)
            # Map: text_chunk_index -> (cached_audio_path, is_first_in_group, is_last_in_group, group_text_chunks)
            # A single stream cache file may cover multiple consecutive text chunks
            stream_chunk_map = {}  # text_chunk_index -> cached_audio_path (only for first chunk in group)
            stream_chunk_skip = set()  # text_chunk_indices to skip (covered by a stream cache group, not the first)
            
            if stream_cache_dir:
                model_voice_dir = stream_cache_dir / f"{audiobook.model}_{audiobook.voice}"
                if model_voice_dir.exists():
                    # Build index of cached stream audio files
                    stream_cache_files = []
                    for audio_file in model_voice_dir.glob("audio_*.mp3"):
                        try:
                            parts = audio_file.stem.split('_')
                            if len(parts) == 3 and parts[0] == 'audio':
                                cached_start = int(parts[1])
                                cached_end = int(parts[2])
                                stream_cache_files.append((cached_start, cached_end, audio_file))
                        except (ValueError, IndexError):
                            continue
                    stream_cache_files.sort(key=lambda x: x[0])
                    
                    # For each cached stream audio file, find which text chunks it covers
                    for cached_start, cached_end, cached_path in stream_cache_files:
                        covered_chunks = []
                        for idx, (tc_start, tc_end) in enumerate(chunk_char_ranges):
                            # A text chunk is covered if its range falls within the cached range
                            if tc_start >= cached_start and tc_end <= cached_end:
                                covered_chunks.append(idx)
                        
                        if covered_chunks:
                            # The first chunk in the group will use the cached audio
                            # All subsequent chunks in the group will be skipped
                            stream_chunk_map[covered_chunks[0]] = cached_path
                            for idx in covered_chunks[1:]:
                                stream_chunk_skip.add(idx)
                    
                    if stream_chunk_map:
                        total_covered = len(stream_chunk_map) + len(stream_chunk_skip)
                        print(f"[DEBUG] Stream cache covers {total_covered}/{len(all_text_chunks)} text chunks ({len(stream_cache_files)} cached audio files)")
                else:
                    print(f"[DEBUG] Stream cache exists but no matching model/voice directory: {audiobook.model}_{audiobook.voice}")
            
            # Load existing data if resuming
            lrc_lines = []
            start_chunk = 0
            cumulative_time = 0.0
            
            if audiobook.completed_chunks > 0:
                print(f"[DEBUG] Resuming from text chunk {audiobook.completed_chunks + 1}")
                start_chunk = audiobook.completed_chunks
                cumulative_time = audiobook.total_duration
                
                # Load existing LRC
                lrc_path = Path(audiobook.lrc_file) if audiobook.lrc_file else None
                if lrc_path and lrc_path.exists():
                    lrc_lines = self.lrc_generator.load_lrc(lrc_path)
                    print(f"[DEBUG] Loaded {len(lrc_lines)} existing LRC lines")
            
            # Pre-compute estimated durations for skipped (grouped) stream cache chunks
            stream_skip_durations = {}  # chunk_index -> estimated_duration
            if stream_chunk_map:
                failed_leaders = []
                for group_leader, cached_path in list(stream_chunk_map.items()):
                    try:
                        # Parse cached file char range
                        parts = cached_path.stem.split('_')
                        cached_start_char = int(parts[1])
                        cached_end_char = int(parts[2])
                        
                        # Find all chunks in this group
                        group = [group_leader]
                        for skip_idx in sorted(stream_chunk_skip):
                            if skip_idx > group_leader:
                                tc_s, tc_e = chunk_char_ranges[skip_idx]
                                if tc_s >= cached_start_char and tc_e <= cached_end_char:
                                    group.append(skip_idx)
                                else:
                                    break
                        
                        # Load audio to get total duration
                        cached_audio = AudioSegment.from_mp3(str(cached_path))
                        total_dur = len(cached_audio) / 1000.0
                        total_text_len = sum(len(all_text_chunks[idx]) for idx in group)
                        
                        # Distribute duration proportionally
                        for idx in group:
                            frac = len(all_text_chunks[idx]) / total_text_len if total_text_len > 0 else 1.0 / len(group)
                            stream_skip_durations[idx] = total_dur * frac
                        
                        # Update map entry with pre-loaded data
                        stream_chunk_map[group_leader] = (cached_path, cached_audio, total_dur, group)
                    except Exception as e:
                        print(f"[DEBUG] Failed to pre-process stream cache for chunk {group_leader}: {e}")
                        failed_leaders.append(group_leader)
                
                # Clean up failed entries
                for leader in failed_leaders:
                    del stream_chunk_map[leader]
            
            # Generate audio in chunks
            current_audio_segments = []  # Segments for current audio file
            current_audio_chunk_start = (start_chunk // TEXT_CHUNKS_PER_AUDIO_FILE) * TEXT_CHUNKS_PER_AUDIO_FILE
            
            for i, text_chunk in enumerate(all_text_chunks):
                # Skip already completed chunks
                if i < start_chunk:
                    continue
                
                # Check if paused
                if audiobook.status == GenerationStatus.PAUSED:
                    print(f"[DEBUG] Generation paused at chunk {i+1}/{len(all_text_chunks)}")
                    break
                
                print(f"[DEBUG] Processing text chunk {i+1}/{len(all_text_chunks)}")
                
                # Skip chunks that are only punctuation/whitespace (can't generate audio)
                if not self._is_valid_text_chunk(text_chunk):
                    print(f"[DEBUG] Skipping text chunk {i+1} - no speakable content: {text_chunk[:50]}...")
                    lrc_lines.append({
                        'timestamp': cumulative_time,
                        'text': text_chunk
                    })
                    audiobook.completed_chunks = i + 1
                    audiobook.updated_at = datetime.now()
                    if save_callback:
                        save_callback()
                    continue
                
                # Check if this chunk is a non-leader in a stream cache group (skip generation)
                if i in stream_chunk_skip:
                    est_duration = stream_skip_durations.get(i, 0)
                    print(f"[DEBUG] Text chunk {i+1} covered by stream cache (skip, est duration: {est_duration:.2f}s)")
                    lrc_lines.append({
                        'timestamp': cumulative_time,
                        'text': text_chunk
                    })
                    cumulative_time += est_duration
                    audiobook.completed_chunks = i + 1
                    audiobook.progress = (i + 1) / audiobook.total_chunks
                    audiobook.total_duration = cumulative_time
                    audiobook.updated_at = datetime.now()
                    if save_callback:
                        save_callback()
                    continue
                
                # Check for cached stream audio (this chunk is the leader of a group)
                cache_entry = stream_chunk_map.get(i)
                
                if cache_entry and isinstance(cache_entry, tuple):
                    cached_path, audio_segment, total_dur, group = cache_entry
                    duration = stream_skip_durations.get(i, total_dur)
                    print(f"[DEBUG] Using cached stream audio for text chunk {i+1} (group of {len(group)}, total: {total_dur:.2f}s)")
                else:
                    # Generate audio for chunk via TTS
                    audio_segment, duration = self._generate_audio_chunk(
                        client,
                        api_model,
                        audiobook.voice,
                        text_chunk
                    )
                
                print(f"[DEBUG] Text chunk {i+1} ready, duration: {duration:.2f}s")
                
                current_audio_segments.append(audio_segment)
                
                # Add to LRC
                lrc_lines.append({
                    'timestamp': cumulative_time,
                    'text': text_chunk
                })
                
                cumulative_time += duration
                
                # Small delay between chunks to prevent GPU memory pressure
                time.sleep(0.1)
                
                # Update progress
                audiobook.completed_chunks = i + 1
                audiobook.progress = (i + 1) / audiobook.total_chunks
                audiobook.total_duration = cumulative_time
                audiobook.updated_at = datetime.now()
                
                # Check if we should save current audio file chunk
                is_chunk_boundary = (i + 1) % TEXT_CHUNKS_PER_AUDIO_FILE == 0
                is_last_chunk = (i + 1) == len(all_text_chunks)
                is_paused = audiobook.status == GenerationStatus.PAUSED
                
                if (is_chunk_boundary or is_last_chunk or is_paused) and current_audio_segments:
                    # Calculate audio chunk index
                    audio_chunk_index = i // TEXT_CHUNKS_PER_AUDIO_FILE
                    audio_chunk_filename = f"chunk_{audio_chunk_index:04d}.mp3"
                    audio_chunk_path = audiobook_dir / audio_chunk_filename
                    
                    print(f"[DEBUG] Saving audio file chunk {audio_chunk_index} at text chunk {i+1}/{len(all_text_chunks)}")
                    
                    # Combine segments for this audio file
                    combined_audio = sum(current_audio_segments)
                    
                    # Save to temp file first, then atomic rename
                    temp_path = audiobook_dir / f"temp_{audio_chunk_filename}"
                    combined_audio.export(str(temp_path), format="mp3")
                    
                    import os
                    os.replace(str(temp_path), str(audio_chunk_path))
                    
                    # Calculate chunk metadata
                    audio_chunk_duration = len(combined_audio) / 1000.0
                    audio_chunk_start_time = cumulative_time - audio_chunk_duration
                    
                    # Update or add audio chunk metadata
                    audio_chunk_obj = AudioChunk(
                        index=audio_chunk_index,
                        filename=audio_chunk_filename,
                        start_text_chunk=current_audio_chunk_start,
                        end_text_chunk=i,
                        duration=audio_chunk_duration,
                        start_time=audio_chunk_start_time
                    )
                    
                    # Update or append to audio_chunks list
                    existing_chunk_idx = next((idx for idx, chunk in enumerate(audiobook.audio_chunks) 
                                              if chunk.index == audio_chunk_index), None)
                    if existing_chunk_idx is not None:
                        audiobook.audio_chunks[existing_chunk_idx] = audio_chunk_obj
                    else:
                        audiobook.audio_chunks.append(audio_chunk_obj)
                    
                    # Sort audio chunks by index
                    audiobook.audio_chunks.sort(key=lambda x: x.index)
                    
                    # Save LRC file - use only the UUID part, not the full path with subdirectories
                    uuid_only = audiobook_id.split('/')[-1]  # Extract UUID from path like "Re:Zero/Fanfic/uuid"
                    lrc_filename = f"{uuid_only}.lrc"
                    lrc_path = settings.LRC_DIR / lrc_filename
                    self.lrc_generator.save_lrc(lrc_lines, lrc_path)
                    audiobook.lrc_file = str(lrc_path)
                    
                    # Save database if callback provided
                    if save_callback:
                        save_callback()
                    
                    # Reset for next audio chunk
                    current_audio_segments = []
                    current_audio_chunk_start = i + 1
                    
                    print(f"[DEBUG] Audio file chunk {audio_chunk_index} saved successfully")
            
            # Build chapter metadata with accurate timestamps from LRC
            audiobook.chapters = []
            for chap_info in chapter_info:
                start_chunk_idx = chap_info['start_chunk']
                if start_chunk_idx < len(lrc_lines):
                    timestamp = lrc_lines[start_chunk_idx]['timestamp']
                    # Use first line of chapter as the chapter name
                    chapter_name = lrc_lines[start_chunk_idx]['text']
                    # Truncate if too long
                    if len(chapter_name) > 100:
                        chapter_name = chapter_name[:97] + '...'
                    audiobook.chapters.append(ChapterInfo(
                        name=chapter_name,
                        start_chunk=chap_info['start_chunk'],
                        end_chunk=chap_info['end_chunk'],
                        timestamp=timestamp
                    ))
            
            # Mark as completed if all chunks done
            if audiobook.completed_chunks == audiobook.total_chunks:
                audiobook.status = GenerationStatus.COMPLETED
                audiobook.progress = 1.0
                print(f"[DEBUG] Audiobook generation completed!")
            else:
                print(f"[DEBUG] Audiobook generation paused at {audiobook.completed_chunks}/{audiobook.total_chunks}")
            
            audiobook.updated_at = datetime.now()
            
            # Save final state
            if save_callback:
                save_callback()
                
        except Exception as e:
            print(f"[ERROR] Generation failed: {str(e)}")
            import traceback
            traceback.print_exc()
            
            audiobook.status = GenerationStatus.FAILED
            audiobook.error = str(e)
            audiobook.updated_at = datetime.now()
            
            # Save database if callback provided
            if save_callback:
                save_callback()
    def _get_model_config(self, model_name: str) -> Optional[Dict]:
        """Get configuration for a specific model"""
        if not settings.MODELS_CONFIG_FILE.exists():
            return None
        
        try:
            with open(settings.MODELS_CONFIG_FILE, 'r') as f:
                models = json.load(f)
                return models.get(model_name)
        except:
            return None
    
    def _get_openai_client(self, model_config: Optional[Dict]) -> OpenAI:
        """Create OpenAI client with optional custom config"""
        # Prioritize model-specific config
        base_url = None
        api_key = None
        
        if model_config:
            base_url = model_config.get('base_url')
            api_key = model_config.get('api_key')
        
        # Fallback to environment config
        if not base_url and settings.OPENAI_BASE_URL:
            base_url = settings.OPENAI_BASE_URL
        
        if not api_key and settings.OPENAI_API_KEY:
            api_key = settings.OPENAI_API_KEY
        
        # For self-hosted servers, API key might not be required
        if not api_key:
            api_key = 'not-needed'
        
        # Create custom HTTP client without proxies to avoid compatibility issues
        http_client = httpx.Client(timeout=60.0)
        
        return OpenAI(api_key=api_key, base_url=base_url, http_client=http_client)
    
    def regenerate_single_chunk(
        self,
        audiobook_id: str,
        chunk_index: int,
        audiobooks_db: Dict[str, AudiobookMetadata],
        save_callback=None
    ):
        """
        Regenerate a single audio chunk without affecting other chunks.
        This only regenerates the specific audio file for the given chunk index.
        """
        try:
            # Ensure chunk_index is an integer
            chunk_index = int(chunk_index)
            print(f"[REGENERATE] Starting regeneration of chunk {chunk_index} for audiobook {audiobook_id}")
            audiobook = audiobooks_db[audiobook_id]
            
            # Get the chunk metadata
            chunk = audiobook.audio_chunks[chunk_index]
            
            # Parse ebook to get text chunks
            ebook_path = Path(audiobook.source_file)
            if not ebook_path.exists():
                full_path = settings.EBOOKS_DIR / audiobook.source_file
                if not full_path.exists():
                    raise FileNotFoundError(f"Ebook not found: {audiobook.source_file}")
                ebook_path = full_path
            
            chunks_data = self.ebook_parser.parse_ebook(ebook_path)
            all_text_chunks = chunks_data['chunks']
            
            # Get the text chunks for this audio chunk (ensure they're integers)
            start_text = int(chunk.start_text_chunk)
            end_text = int(chunk.end_text_chunk)
            
            print(f"[REGENERATE] Regenerating text chunks {start_text}-{end_text}")
            
            # Get model config
            models_file = settings.STORAGE_DIR / "models.json"
            models = {}
            if models_file.exists():
                with open(models_file, 'r') as f:
                    models = json.load(f)
            
            model_config = models.get(audiobook.model, {})
            api_model = model_config.get('api_model', audiobook.model)
            
            # Create OpenAI client
            client = self._get_openai_client(model_config)
            
            # Generate audio for each text chunk in this audio chunk
            audio_segments = []
            for i in range(start_text, end_text + 1):
                if i >= len(all_text_chunks):
                    break
                text_chunk = all_text_chunks[i]
                print(f"[REGENERATE] Generating text chunk {i+1}/{end_text+1}")
                
                # Skip chunks that are only punctuation/whitespace
                if not self._is_valid_text_chunk(text_chunk):
                    print(f"[REGENERATE] Skipping text chunk {i+1} - no speakable content")
                    continue
                
                audio_segment, duration = self._generate_audio_chunk(
                    client,
                    api_model,
                    audiobook.voice,
                    text_chunk
                )
                audio_segments.append(audio_segment)
                time.sleep(0.1)  # Small delay between chunks
            
            if not audio_segments:
                raise ValueError("No audio segments generated")
            
            # Combine all segments
            combined_audio = sum(audio_segments)
            
            # Save to the chunk file
            audiobook_dir = settings.AUDIOBOOKS_DIR / audiobook_id
            audio_chunk_path = audiobook_dir / chunk.filename
            
            # Save to temp file first, then atomic rename
            temp_path = audiobook_dir / f"temp_{chunk.filename}"
            combined_audio.export(str(temp_path), format="mp3")
            
            import os
            os.replace(str(temp_path), str(audio_chunk_path))
            
            # Update chunk duration in metadata
            new_duration = len(combined_audio) / 1000.0
            old_duration = chunk.duration
            duration_diff = new_duration - old_duration
            chunk.duration = new_duration
            
            # Recalculate start times for all chunks after this one
            cumulative_time = 0
            for c in audiobook.audio_chunks:
                c.start_time = cumulative_time
                cumulative_time += c.duration
            
            audiobook.total_duration = cumulative_time
            
            # Update LRC file timestamps if the duration changed
            if audiobook.lrc_file and abs(duration_diff) > 0.01:
                lrc_path = Path(audiobook.lrc_file)
                if lrc_path.exists():
                    lrc_lines = self.lrc_generator.load_lrc(lrc_path)
                    
                    # Find the first text chunk in this audio chunk and update timestamps
                    # from there onwards
                    updated = False
                    for line in lrc_lines:
                        # If this line's timestamp is after the start of the regenerated chunk
                        if line['timestamp'] >= chunk.start_time and not updated:
                            updated = True
                        
                        # Shift all timestamps after the chunk started
                        if updated:
                            line['timestamp'] += duration_diff
                    
                    # Save updated LRC
                    self.lrc_generator.save_lrc(lrc_lines, lrc_path)
                    print(f"[REGENERATE] Updated LRC timestamps (shifted by {duration_diff:.2f}s)")
            
            audiobook.status = GenerationStatus.COMPLETED
            audiobook.updated_at = datetime.now()
            
            # Delete cached combined download file if it exists (needs to be regenerated)
            combined_download_path = audiobook_dir / "combined_download.mp3"
            if combined_download_path.exists():
                combined_download_path.unlink()
                print(f"[REGENERATE] Deleted cached combined download file")
            
            if save_callback:
                save_callback()
            
            print(f"[REGENERATE] Successfully regenerated chunk {chunk_index}")
            
        except Exception as e:
            print(f"[REGENERATE ERROR] Failed to regenerate chunk {chunk_index}: {str(e)}")
            import traceback
            traceback.print_exc()
            audiobook = audiobooks_db[audiobook_id]
            audiobook.status = GenerationStatus.FAILED
            audiobook.error = f"Failed to regenerate chunk {chunk_index}: {str(e)}"
            if save_callback:
                save_callback()
    
    def _generate_audio_chunk(
        self,
        client: OpenAI,
        model: str,
        voice: str,
        text: str
    ) -> tuple[AudioSegment, float]:
        """Generate audio for a single text chunk"""
        # Create a temporary file for the audio
        temp_file = settings.AUDIOBOOKS_DIR / f"temp_{uuid.uuid4()}.mp3"
        
        try:
            print(f"[DEBUG] Calling TTS API - model: {model}, voice: {voice}, text length: {len(text)}")
            print(f"[DEBUG] Client base_url: {client.base_url}")
            response = client.audio.speech.create(
                model=model,
                voice=voice,
                input=text
            )
            print(f"[DEBUG] TTS API response received")
            
            response.stream_to_file(str(temp_file))
            
            # Load with pydub to get duration
            audio = AudioSegment.from_mp3(str(temp_file))
            duration = len(audio) / 1000.0  # Convert to seconds
            
            return audio, duration
        finally:
            # Clean up temp file
            if temp_file.exists():
                temp_file.unlink()

    def generate_audiobook_append(
        self,
        audiobook_id: str,
        audiobooks_db: Dict[str, AudiobookMetadata],
        save_callback=None,
        append_from_chunk: int = 0
    ):
        """
        Append content from a new ebook to an existing audiobook.
        This keeps all existing audio and adds the new content as additional chapters.
        
        Args:
            audiobook_id: ID of the audiobook to update
            audiobooks_db: Database of audiobooks
            save_callback: Function to call to persist changes
            append_from_chunk: The chunk index where we should start appending
        """
        try:
            print(f"[APPEND] Starting append for audiobook {audiobook_id}")
            print(f"[APPEND] Will append new content after chunk {append_from_chunk}")
            
            audiobook = audiobooks_db[audiobook_id]
            audiobook.status = GenerationStatus.IN_PROGRESS
            audiobook.updated_at = datetime.now()
            
            # Create/ensure audiobook directory exists
            audiobook_dir = settings.AUDIOBOOKS_DIR / audiobook_id
            audiobook_dir.mkdir(parents=True, exist_ok=True)
            
            # Parse the NEW ebook
            print(f"[APPEND] Parsing new ebook: {audiobook.source_file}")
            ebook_path = Path(audiobook.source_file)
            if not ebook_path.exists():
                full_path = settings.EBOOKS_DIR / audiobook.source_file
                if not full_path.exists():
                    raise FileNotFoundError(f"Ebook not found: {audiobook.source_file}")
                ebook_path = full_path
            
            # Update ebook hash
            current_hash = self._compute_ebook_hash(ebook_path)
            audiobook.ebook_hash = current_hash
            
            # Parse new ebook content
            chunks_data = self.ebook_parser.parse_ebook(ebook_path)
            new_text_chunks = []
            new_chapter_info = []
            
            # Calculate where the new content starts (after existing content)
            existing_chunks_count = append_from_chunk
            
            for chapter_data in chunks_data:
                chapter_start = len(new_text_chunks)
                text_chunks = self.ebook_parser.chunk_text(
                    chapter_data['text'],
                    settings.CHUNK_SIZE
                )
                new_text_chunks.extend(text_chunks)
                new_chapter_info.append({
                    'name': chapter_data.get('chapter', 'Unknown Chapter'),
                    'start_chunk': existing_chunks_count + chapter_start,
                    'end_chunk': existing_chunks_count + len(new_text_chunks) - 1,
                    'chunk_count': len(text_chunks)
                })
            
            print(f"[APPEND] New content has {len(new_text_chunks)} chunks across {len(new_chapter_info)} chapters")
            
            # Update total chunks to include both old and new
            audiobook.total_chunks = existing_chunks_count + len(new_text_chunks)
            
            # Load existing LRC data
            lrc_lines = []
            if audiobook.lrc_file:
                lrc_path = Path(audiobook.lrc_file)
                if lrc_path.exists():
                    lrc_lines = self.lrc_generator.load_lrc(lrc_path)
                    print(f"[APPEND] Loaded {len(lrc_lines)} existing LRC lines")
            
            # Get cumulative time from existing content
            cumulative_time = audiobook.total_duration
            print(f"[APPEND] Starting append at cumulative time: {cumulative_time:.2f}s")
            
            # Get model config and create client
            model_config = self._get_model_config(audiobook.model)
            api_model = model_config.get('api_model', audiobook.model) if model_config else audiobook.model
            client = self._get_openai_client(model_config)
            
            # Calculate starting audio chunk index
            current_audio_chunk_index = len(audiobook.audio_chunks)
            current_audio_segments = []
            current_audio_chunk_start = existing_chunks_count
            
            # Generate audio for new content
            for i, text_chunk in enumerate(new_text_chunks):
                global_chunk_index = existing_chunks_count + i
                
                if audiobook.status == GenerationStatus.PAUSED:
                    print(f"[APPEND] Generation paused at chunk {global_chunk_index}")
                    break
                
                print(f"[APPEND] Processing chunk {i + 1}/{len(new_text_chunks)} (global: {global_chunk_index})")
                
                # Skip non-speakable chunks
                if not self._is_valid_text_chunk(text_chunk):
                    print(f"[APPEND] Skipping chunk {i + 1} - no speakable content")
                    lrc_lines.append({
                        'timestamp': cumulative_time,
                        'text': text_chunk
                    })
                    audiobook.completed_chunks = global_chunk_index + 1
                    audiobook.updated_at = datetime.now()
                    if save_callback:
                        save_callback()
                    continue
                
                # Generate audio
                audio_segment, duration = self._generate_audio_chunk(
                    client, api_model, audiobook.voice, text_chunk
                )
                
                current_audio_segments.append(audio_segment)
                
                # Add to LRC
                lrc_lines.append({
                    'timestamp': cumulative_time,
                    'text': text_chunk
                })
                
                cumulative_time += duration
                time.sleep(0.1)  # Small delay
                
                # Update progress
                audiobook.completed_chunks = global_chunk_index + 1
                audiobook.progress = audiobook.completed_chunks / audiobook.total_chunks
                audiobook.total_duration = cumulative_time
                audiobook.updated_at = datetime.now()
                
                # Save audio file chunk periodically
                is_chunk_boundary = (i + 1) % TEXT_CHUNKS_PER_AUDIO_FILE == 0
                is_last_chunk = (i + 1) == len(new_text_chunks)
                is_paused = audiobook.status == GenerationStatus.PAUSED
                
                if (is_chunk_boundary or is_last_chunk or is_paused) and current_audio_segments:
                    audio_chunk_index = current_audio_chunk_index + (i // TEXT_CHUNKS_PER_AUDIO_FILE)
                    audio_chunk_filename = f"chunk_{audio_chunk_index:04d}.mp3"
                    audio_chunk_path = audiobook_dir / audio_chunk_filename
                    
                    print(f"[APPEND] Saving audio chunk {audio_chunk_index}")
                    
                    combined_audio = sum(current_audio_segments)
                    temp_path = audiobook_dir / f"temp_{audio_chunk_filename}"
                    combined_audio.export(str(temp_path), format="mp3")
                    os.replace(str(temp_path), str(audio_chunk_path))
                    
                    audio_chunk_duration = len(combined_audio) / 1000.0
                    audio_chunk_start_time = cumulative_time - audio_chunk_duration
                    
                    audio_chunk_obj = AudioChunk(
                        index=audio_chunk_index,
                        filename=audio_chunk_filename,
                        start_text_chunk=current_audio_chunk_start,
                        end_text_chunk=global_chunk_index,
                        duration=audio_chunk_duration,
                        start_time=audio_chunk_start_time
                    )
                    audiobook.audio_chunks.append(audio_chunk_obj)
                    audiobook.audio_chunks.sort(key=lambda x: x.index)
                    
                    # Save LRC
                    uuid_only = audiobook_id.split('/')[-1]
                    lrc_filename = f"{uuid_only}.lrc"
                    lrc_path = settings.LRC_DIR / lrc_filename
                    self.lrc_generator.save_lrc(lrc_lines, lrc_path)
                    audiobook.lrc_file = str(lrc_path)
                    
                    if save_callback:
                        save_callback()
                    
                    current_audio_segments = []
                    current_audio_chunk_start = global_chunk_index + 1
            
            # Add new chapters to existing chapters
            for new_chap in new_chapter_info:
                # Find the timestamp for this chapter
                start_chunk = new_chap['start_chunk']
                timestamp = 0.0
                for lrc in lrc_lines:
                    if lrc_lines.index(lrc) == start_chunk:
                        timestamp = lrc['timestamp']
                        break
                
                audiobook.chapters.append(ChapterInfo(
                    name=f"[APPENDED] {new_chap['name'][:90]}",
                    start_chunk=new_chap['start_chunk'],
                    end_chunk=new_chap['end_chunk'],
                    timestamp=timestamp
                ))
            
            # Mark complete if all chunks done
            if audiobook.completed_chunks == audiobook.total_chunks:
                audiobook.status = GenerationStatus.COMPLETED
                audiobook.progress = 1.0
                print(f"[APPEND] Audiobook append completed!")
            
            # Delete cached combined download
            combined_download = audiobook_dir / "combined_download.mp3"
            if combined_download.exists():
                combined_download.unlink()
                print(f"[APPEND] Deleted cached combined download")
            
            audiobook.updated_at = datetime.now()
            if save_callback:
                save_callback()
                
        except Exception as e:
            print(f"[APPEND ERROR] Failed: {str(e)}")
            import traceback
            traceback.print_exc()
            
            audiobook.status = GenerationStatus.FAILED
            audiobook.error = str(e)
            audiobook.updated_at = datetime.now()
            
            if save_callback:
                save_callback()
