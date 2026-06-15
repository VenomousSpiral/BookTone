from pathlib import Path

import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup
import re
import json
import hashlib
from datetime import datetime
from PyPDF2 import PdfReader
import base64
import logging

from app.core.config import settings

# ------------------------------------------------------------------ #
#  DEBUG LOGGING: Remove this block entirely to disable all debug    #
#  output from the ebook parser.                                     #
#  To enable: set level to logging.DEBUG (default is WARNING).       #
# ------------------------------------------------------------------ #
_EBOOK_PARSER_DEBUG = True  # <-- Set to False to disable all debug logs
if _EBOOK_PARSER_DEBUG:
    _logger = logging.getLogger("ebook_parser")
    _logger.setLevel(logging.DEBUG)
    if not _logger.handlers:
        _handler = logging.StreamHandler()
        _handler.setLevel(logging.DEBUG)
        _logger.addHandler(_handler)
else:
    _logger = logging.getLogger("ebook_parser")
    _logger.setLevel(logging.WARNING)

class EbookParser:
    """Parse ebooks and extract text content"""
    
    SUPPORTED_FORMATS = ['.epub', '.txt', '.html', '.pdf']
    
    def __init__(self):
        self._image_cache = {}  # Cache for extracted images {ebook_path: {image_id: base64_data}}
        self._parse_cache = {}  # In-memory cache {cache_key: {mtime, data}}
    
    def parse_ebook(self, file_path: Path) -> list[dict[str, str]]:
        """
        Parse an ebook and return structured text chunks
        
        Returns:
            List of dicts with 'text' and 'chapter' keys
        """
        suffix = file_path.suffix.lower()
        
        if suffix == '.epub':
            return self._parse_epub(file_path)
        elif suffix == '.txt':
            return self._parse_txt(file_path)
        elif suffix in ['.html', '.htm']:
            return self._parse_html(file_path)
        elif suffix == '.pdf':
            return self._parse_pdf(file_path)
        else:
            raise ValueError(f"Unsupported format: {suffix}")
    
    def parse_ebook_with_images(self, file_path: Path) -> tuple[list[dict[str, str]], dict[str, str]]:
        """
        Parse an ebook and return structured text chunks with image references
        
        Returns:
            Tuple of (chunks_list, images_dict)
            - chunks_list: List of dicts with 'text', 'chapter', and 'images' keys
            - images_dict: Dict mapping image_id to base64-encoded image data
        """
        suffix = file_path.suffix.lower()
        
        if suffix == '.epub':
            return self._parse_epub_with_images(file_path)
        elif suffix == '.pdf':
            return self._parse_pdf_with_images(file_path)
        else:
            # Other formats don't have embedded images
            chunks = self.parse_ebook(file_path)
            for chunk in chunks:
                chunk['images'] = []
            return chunks, {}
    
    def _parse_epub(self, file_path: Path) -> list[dict[str, str]]:
        """Parse EPUB file"""
        try:
            book = epub.read_epub(str(file_path))
            chunks = []
            
            for item in book.get_items():
                if item.get_type() == ebooklib.ITEM_DOCUMENT:
                    # Extract text from HTML
                    soup = BeautifulSoup(item.get_content(), 'html.parser')
                    
                    # Remove images but preserve spacing - replace with space to prevent text merging
                    for img in soup.find_all('img'):
                        img.replace_with(' ')
                    for svg in soup.find_all('svg'):
                        svg.replace_with(' ')
                    
                    text = soup.get_text(separator=' ', strip=True)
                    # Clean up multiple spaces
                    text = re.sub(r' +', ' ', text)
                    
                    if text:
                        # Try to get chapter name from title
                        chapter_name = item.get_name() or "Chapter"
                        chunks.append({
                            'text': text,
                            'chapter': chapter_name
                        })
            
            return chunks
        except Exception as e:
            raise ValueError(f"Error parsing EPUB: {str(e)}")
    
    def _parse_epub_with_images(self, file_path: Path) -> tuple[list[dict[str, str]], dict[str, str]]:
        """Parse EPUB file and extract images"""
        try:
            book = epub.read_epub(str(file_path))
            chunks = []
            images = {}
            
            # First, extract all images from the EPUB
            image_items = {}
            for item in book.get_items():
                if item.get_type() == ebooklib.ITEM_IMAGE:
                    # Generate a unique ID for this image
                    img_name = item.get_name()
                    img_data = item.get_content()
                    
                    # Determine image type from name or content
                    img_ext = Path(img_name).suffix.lower()
                    if img_ext in ['.jpg', '.jpeg']:
                        mime_type = 'image/jpeg'
                    elif img_ext == '.png':
                        mime_type = 'image/png'
                    elif img_ext == '.gif':
                        mime_type = 'image/gif'
                    elif img_ext == '.svg':
                        mime_type = 'image/svg+xml'
                    elif img_ext == '.webp':
                        mime_type = 'image/webp'
                    else:
                        mime_type = 'image/png'  # Default
                    
                    # Create base64 data URL
                    img_id = hashlib.md5(img_name.encode()).hexdigest()[:12]
                    img_base64 = base64.b64encode(img_data).decode('utf-8')
                    images[img_id] = f"data:{mime_type};base64,{img_base64}"
                    
                    # Map original name to our ID
                    image_items[img_name] = img_id
                    # Also map just the filename
                    image_items[Path(img_name).name] = img_id
            
            # Now parse documents and find image references with positions
            for item in book.get_items():
                if item.get_type() == ebooklib.ITEM_DOCUMENT:
                    soup = BeautifulSoup(item.get_content(), 'html.parser')
                    
                    # Replace images with placeholders to track position in text
                    image_markers = []
                    marker_index = 0
                    
                    for img in soup.find_all('img'):
                        src = img.get('src', '')
                        # Normalize the image path - handle various path formats
                        # Strip query strings and fragments
                        img_path = src.split('?')[0].split('#')[0]
                        # Get just the filename
                        img_filename = img_path.split('/')[-1] if '/' in img_path else img_path
                        # Also try without the leading path components
                        img_path_normalized = img_path.lstrip('./')
                        
                        # Find the image ID - try multiple matching strategies
                        found_id = None
                        for name, img_id in image_items.items():
                            name_filename = Path(name).name
                            # Match by exact filename
                            if img_filename == name_filename:
                                found_id = img_id
                                break
                            # Match by path ending
                            if name.endswith(img_path_normalized) or img_path_normalized.endswith(name):
                                found_id = img_id
                                break
                            # Match by filename contained in path
                            if img_filename and img_filename in name:
                                found_id = img_id
                                break
                        
                        # Always replace the img tag (even if not found) to prevent text merging issues
                        # Use spaces around marker to ensure proper word separation
                        marker = f" <<<IMAGE_{marker_index}>>> "
                        img.replace_with(marker)
                        if found_id:
                            image_markers.append({'marker': f"<<<IMAGE_{marker_index}>>>", 'id': found_id})
                        marker_index += 1
                    
                    # Handle SVG elements
                    for svg in soup.find_all('svg'):
                        svg_str = str(svg)
                        svg_id = hashlib.md5(svg_str.encode()).hexdigest()[:12]
                        svg_base64 = base64.b64encode(svg_str.encode()).decode('utf-8')
                        images[svg_id] = f"data:image/svg+xml;base64,{svg_base64}"
                        
                        # Use spaces around marker to ensure proper word separation
                        marker = f" <<<IMAGE_{marker_index}>>> "
                        svg.replace_with(marker)
                        image_markers.append({'marker': f"<<<IMAGE_{marker_index}>>>", 'id': svg_id})
                        marker_index += 1
                    
                    # Extract text with markers
                    text_with_markers = soup.get_text(separator=' ', strip=True)
                    
                    # Clean up any multiple spaces that may have been introduced
                    text_with_markers = re.sub(r' +', ' ', text_with_markers)
                    # Fix markers that may have gotten space inside due to strip
                    text_with_markers = re.sub(r'<<<\s*IMAGE_(\d+)\s*>>>', r'<<<IMAGE_\1>>>', text_with_markers)
                    
                    if text_with_markers or image_markers:
                        chapter_name = item.get_name() or "Chapter"
                        chunks.append({
                            'text': text_with_markers,
                            'chapter': chapter_name,
                            'image_markers': image_markers  # List of {marker, id}
                        })
            
            return chunks, images
        except Exception as e:
            raise ValueError(f"Error parsing EPUB with images: {str(e)}")
    
    def _parse_txt(self, file_path: Path) -> list[dict[str, str]]:
        """Parse plain text file"""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                text = f.read()
            
            # Split by common chapter markers
            chapter_pattern = r'(Chapter\s+\d+|CHAPTER\s+\d+|Chapter\s+[IVXLCDM]+)'
            chapters = re.split(chapter_pattern, text)
            
            chunks = []
            current_chapter = "Introduction"
            
            for i, part in enumerate(chapters):
                if re.match(chapter_pattern, part):
                    current_chapter = part
                elif part.strip():
                    chunks.append({
                        'text': part.strip(),
                        'chapter': current_chapter
                    })
            
            return chunks if chunks else [{'text': text, 'chapter': 'Full Text'}]
        except Exception as e:
            raise ValueError(f"Error parsing TXT: {str(e)}")
    
    def _parse_html(self, file_path: Path) -> list[dict[str, str]]:
        """Parse HTML file"""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                html_content = f.read()
            
            soup = BeautifulSoup(html_content, 'html.parser')
            text = soup.get_text(separator=' ', strip=True)
            
            return [{'text': text, 'chapter': 'Full Text'}]
        except Exception as e:
            raise ValueError(f"Error parsing HTML: {str(e)}")
    
    def _parse_pdf(self, file_path: Path) -> list[dict[str, str]]:
        """Parse PDF file"""
        try:
            reader = PdfReader(str(file_path))
            chunks = []
            
            # Try to extract text by chapter/section if outline exists
            if reader.outline:
                # PDF has bookmarks/outline
                current_chapter = "Introduction"
                chapter_texts = {}
                
                # Extract all text first
                all_text = []
                for page in reader.pages:
                    text = page.extract_text()
                    if text:
                        all_text.append(text)
                
                full_text = '\n'.join(all_text)
                
                # For simplicity, treat entire PDF as one chunk
                # In future, could parse outline to split into chapters
                chunks.append({
                    'text': full_text,
                    'chapter': 'PDF Document'
                })
            else:
                # No outline, combine pages into logical chunks
                current_text = []
                page_count = 0
                pages_per_chunk = 10  # Group pages into chunks
                
                for i, page in enumerate(reader.pages):
                    text = page.extract_text()
                    if text:
                        current_text.append(text)
                        page_count += 1
                        
                        # Create chunk every N pages
                        if page_count >= pages_per_chunk:
                            chunks.append({
                                'text': '\n'.join(current_text),
                                'chapter': f'Pages {i - page_count + 2}-{i + 1}'
                            })
                            current_text = []
                            page_count = 0
                
                # Add remaining text
                if current_text:
                    chunks.append({
                        'text': '\n'.join(current_text),
                        'chapter': f'Pages {len(reader.pages) - page_count + 1}-{len(reader.pages)}'
                    })
            
            if not chunks:
                raise ValueError("No text extracted from PDF")
            
            return chunks
        except Exception as e:
            raise ValueError(f"Error parsing PDF: {str(e)}")
    
    def _parse_pdf_with_images(self, file_path: Path) -> tuple[list[dict[str, str]], dict[str, str]]:
        """Parse PDF file and extract images"""
        try:
            reader = PdfReader(str(file_path))
            chunks = []
            images = {}
            
            # Extract text and images from each page
            current_text = []
            current_images = []
            page_count = 0
            pages_per_chunk = 10
            
            for i, page in enumerate(reader.pages):
                # Extract text
                text = page.extract_text()
                if text:
                    current_text.append(text)
                
                # Try to extract images from page
                try:
                    if '/XObject' in page['/Resources']:
                        xObject = page['/Resources']['/XObject'].get_object()
                        for obj in xObject:
                            if xObject[obj]['/Subtype'] == '/Image':
                                try:
                                    img_obj = xObject[obj]
                                    
                                    # Get image data
                                    if '/Filter' in img_obj:
                                        filter_type = img_obj['/Filter']
                                        
                                        # Handle DCTDecode (JPEG)
                                        if filter_type == '/DCTDecode':
                                            img_data = img_obj._data
                                            img_id = hashlib.md5(img_data[:100]).hexdigest()[:12]
                                            img_base64 = base64.b64encode(img_data).decode('utf-8')
                                            images[img_id] = f"data:image/jpeg;base64,{img_base64}"
                                            current_images.append(img_id)
                                        
                                        # Handle FlateDecode (PNG-like)
                                        elif filter_type == '/FlateDecode':
                                            # This is more complex and may not always work
                                            # For now, skip FlateDecode images as they need reconstruction
                                            pass
                                except Exception as img_err:
                                    _logger.debug("[DEBUG] Could not extract image from PDF page %d: %s", i, img_err)
                except Exception as page_err:
                    # Page doesn't have images or couldn't be processed
                    pass
                
                page_count += 1
                
                # Create chunk every N pages
                if page_count >= pages_per_chunk:
                    if current_text or current_images:
                        chunks.append({
                            'text': '\n'.join(current_text),
                            'chapter': f'Pages {i - page_count + 2}-{i + 1}',
                            'images': current_images.copy()
                        })
                    current_text = []
                    current_images = []
                    page_count = 0
            
            # Add remaining content
            if current_text or current_images:
                chunks.append({
                    'text': '\n'.join(current_text),
                    'chapter': f'Pages {len(reader.pages) - page_count + 1}-{len(reader.pages)}',
                    'images': current_images.copy()
                })
            
            if not chunks:
                raise ValueError("No content extracted from PDF")
            
            return chunks, images
        except Exception as e:
            raise ValueError(f"Error parsing PDF with images: {str(e)}")
    
    def _is_valid_text_chunk(self, text: str) -> bool:
        """
        Check if a text chunk has enough actual words/letters to generate audio.
        Filters out chunks that are only punctuation, whitespace, or symbols.
        """
        if not text:
            return False
        
        # Remove all punctuation, whitespace, and common symbols
        # Keep only actual letters and numbers
        letters_only = re.sub(r'[^a-zA-Z0-9\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff]', '', text)
        
        # Must have at least 2 actual characters to be speakable
        return len(letters_only) >= 2
    
    def _split_oversized_chunk(self, text: str, max_chars: int = 500) -> list[str]:
        """
        Split an oversized chunk at the nearest space after max_chars.
        This preserves existing chunk boundaries while preventing huge chunks.
        """
        if len(text) <= max_chars:
            return [text]
        
        result = []
        remaining = text
        
        while len(remaining) > max_chars:
            # Find a space near the max_chars limit to split at
            split_point = max_chars
            
            # Look for a space after the limit (prefer not cutting words)
            space_after = remaining.find(' ', max_chars)
            # Also check for a space before the limit as fallback
            space_before = remaining.rfind(' ', 0, max_chars)
            
            if space_after != -1 and space_after < max_chars + 100:
                # Found a space within 100 chars after limit, use it
                split_point = space_after
            elif space_before > max_chars // 2:
                # Use space before limit if it's not too early
                split_point = space_before
            elif space_after != -1:
                # Use any space after limit
                split_point = space_after
            # else: force split at max_chars (no good space found)
            
            chunk = remaining[:split_point].strip()
            if chunk and self._is_valid_text_chunk(chunk):
                result.append(chunk)
            remaining = remaining[split_point:].strip()
        
        # Add the last piece
        if remaining and self._is_valid_text_chunk(remaining):
            result.append(remaining)
        
        return result
    
    def chunk_text(self, text: str, chunk_size: int = 4096, max_chunk_chars: int = 500) -> list[str]:
        """
        Split text into chunks of approximately chunk_size characters,
        breaking at sentence boundaries. Also enforces a maximum character
        limit per chunk to prevent oversized chunks from dialogue-heavy text.
        """
        # Split into sentences
        sentences = re.split(r'(?<=[.!?])\s+', text)
        
        # Combine sentences to meet minimum (5 words or 21 characters)
        chunks = []
        current_chunk = ""
        
        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue
                
            # Add to current chunk
            if current_chunk:
                current_chunk += " " + sentence
            else:
                current_chunk = sentence
            
            # Check if chunk meets minimum (5 words or 21 characters)
            word_count = len(current_chunk.split())
            if word_count >= 5 or len(current_chunk) >= 21:
                # Only add if it has actual speakable content
                if self._is_valid_text_chunk(current_chunk):
                    # Split if oversized, otherwise add as-is
                    split_chunks = self._split_oversized_chunk(current_chunk, max_chunk_chars)
                    chunks.extend(split_chunks)
                current_chunk = ""
        
        # Add any remaining text if it's valid
        if current_chunk and self._is_valid_text_chunk(current_chunk):
            split_chunks = self._split_oversized_chunk(current_chunk, max_chunk_chars)
            chunks.extend(split_chunks)

        return chunks

    # ------------------------------------------------------------------ #
    #  Disk-backed caching for parsed ebooks (B-3, B-5, B-11)           #
    # ------------------------------------------------------------------ #

    def parse_and_cache(self, file_path: Path, with_images: bool = False) -> dict:
        """
        Parse an ebook and cache the result on disk.
        Returns the parsed data structure.

        The cached result is stored in:
            storage/stream_cache/{safe_stem}_{hash}{suffix}.json

        On subsequent calls, if the file hasn't changed (same mtime),
        the cached result is returned instantly.
        """
        if not file_path.exists():
            file_path = settings.STORAGE_DIR / "ebooks" / file_path

        _logger.debug("[PARSE] parse_and_cache START: file=%s with_images=%s", file_path, with_images)

        file_hash = self._compute_file_hash(file_path)
        cache_key = f"{file_path}:{file_hash}:{with_images}"

        # Check in-memory cache first
        if cache_key in self._parse_cache:
            cached = self._parse_cache[cache_key]
            if cached['mtime'] == file_path.stat().st_mtime:
                _logger.debug("[PARSE] IN-MEMORY CACHE HIT: file=%s with_images=%s", file_path, with_images)
                return cached['data']
            else:
                _logger.debug("[PARSE] IN-MEMORY CACHE STALE (mtime changed): file=%s with_images=%s", file_path, with_images)

        # Check disk cache
        cache_file = self._get_cache_file(file_path, with_images)
        if cache_file.exists():
            try:
                with open(cache_file, 'r') as f:
                    cached_data = json.load(f)
                if cached_data.get('_file_mtime') == file_path.stat().st_mtime:
                    _logger.debug("[PARSE] DISK CACHE HIT: file=%s with_images=%s cache=%s", file_path, with_images, cache_file)
                    self._parse_cache[cache_key] = {
                        'mtime': file_path.stat().st_mtime,
                        'data': cached_data
                    }
                    return cached_data
                else:
                    _logger.debug("[PARSE] DISK CACHE STALE (mtime changed): file=%s with_images=%s", file_path, with_images)
            except (json.JSONDecodeError, KeyError) as e:
                _logger.debug("[PARSE] DISK CACHE CORRUPT (will re-parse): file=%s with_images=%s error=%s", file_path, with_images, e)
        else:
            _logger.debug("[PARSE] NO DISK CACHE: file=%s with_images=%s", file_path, with_images)

        # Parse the ebook (this is the slow part)
        _logger.debug("[PARSE] ACTUAL PARSING: file=%s with_images=%s", file_path, with_images)
        import time
        t0 = time.time()

        if with_images:
            data = self._parse_for_streaming_with_images(file_path)
        else:
            data = self._parse_for_streaming(file_path)

        elapsed = time.time() - t0
        _logger.debug("[PARSE] PARSING DONE: file=%s with_images=%s took=%.2fs chunks=%d", file_path, with_images, elapsed, len(data.get('chunks', [])))

        data['_file_mtime'] = file_path.stat().st_mtime
        data['_file_hash'] = file_hash
        data['_with_images'] = with_images
        data['_cached_at'] = datetime.now().isoformat()

        cache_file.parent.mkdir(parents=True, exist_ok=True)
        with open(cache_file, 'w') as f:
            json.dump(data, f)

        _logger.debug("[PARSE] CACHE SAVED TO DISK: file=%s with_images=%s cache=%s", file_path, with_images, cache_file)

        self._parse_cache[cache_key] = {
            'mtime': file_path.stat().st_mtime,
            'data': data
        }

        return data

    def _parse_for_streaming(self, file_path: Path) -> dict:
        """Parse ebook and build streaming data structure (cached version)."""
        chapters_data = self.parse_ebook(file_path)

        all_text_chunks = []
        chapters = []
        chunk_index = 0
        current_char_pos = 0

        for chapter_idx, chapter_data in enumerate(chapters_data):
            chapter_start_chunk = chunk_index
            chapter_start_char = current_char_pos
            text_chunks = self.chunk_text(chapter_data['text'], 4096)

            for text_chunk in text_chunks:
                chunk_start_char = current_char_pos
                chunk_end_char = current_char_pos + len(text_chunk)
                all_text_chunks.append({
                    "index": chunk_index,
                    "start_idx": chunk_start_char,
                    "end_idx": chunk_end_char,
                    "_content_hash": hashlib.md5(text_chunk.encode()).hexdigest()[:16],
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

        # B-11: Pre-compute binary-searchable time index
        chunk_time_index = [
            {"start_time": c["start_idx"], "chunk_index": c["index"]}
            for c in all_text_chunks
        ]

        return {
            "title": file_path.stem,
            "chapters": chapters,
            "chunks": all_text_chunks,
            "total_chars": current_char_pos,
            "total_chunks": len(all_text_chunks),
            "chunk_time_index": chunk_time_index
        }

    def _parse_for_streaming_with_images(self, file_path: Path) -> dict:
        """Parse ebook with images and build streaming data structure (cached version)."""
        chapters_data, all_images = self.parse_ebook_with_images(file_path)

        marker_pattern = re.compile(r'<<<IMAGE_\d+>>>')
        all_text_chunks = []
        chapters = []
        chunk_index = 0
        current_char_pos = 0

        for chapter_idx, chapter_data in enumerate(chapters_data):
            chapter_start_chunk = chunk_index
            chapter_start_char = current_char_pos
            chapter_text_with_markers = chapter_data.get('text', '')
            image_markers = chapter_data.get('image_markers', [])

            clean_chapter_text_raw = marker_pattern.sub('', chapter_text_with_markers)
            clean_chapter_text = re.sub(r' +', ' ', clean_chapter_text_raw).strip()

            image_positions = []
            normalized_clean_pos = 0
            marked_pos = 0
            last_was_space = False

            while marked_pos < len(chapter_text_with_markers):
                marker_match = marker_pattern.match(chapter_text_with_markers[marked_pos:])
                if marker_match:
                    for marker_info in image_markers:
                        if marker_info['marker'] == marker_match.group():
                            image_positions.append((normalized_clean_pos, marker_info))
                            break
                    marked_pos += len(marker_match.group())
                else:
                    char = chapter_text_with_markers[marked_pos]
                    is_space = char == ' '
                    if is_space:
                        if not last_was_space and normalized_clean_pos > 0:
                            normalized_clean_pos += 1
                        last_was_space = True
                    else:
                        normalized_clean_pos += 1
                        last_was_space = False
                    marked_pos += 1

            text_chunks = self.chunk_text(clean_chapter_text, 4096)
            chapter_clean_pos = 0

            for i, clean_text_chunk in enumerate(text_chunks):
                chunk_start_char = current_char_pos
                chunk_end_char = current_char_pos + len(clean_text_chunk)
                chunk_start_in_chapter = clean_chapter_text.find(clean_text_chunk, chapter_clean_pos)
                if chunk_start_in_chapter == -1:
                    chunk_start_in_chapter = chapter_clean_pos
                chunk_end_in_chapter = chunk_start_in_chapter + len(clean_text_chunk)

                chunk_image_data = []
                for img_pos, marker_info in image_positions:
                    if chunk_start_in_chapter <= img_pos <= chunk_end_in_chapter:
                        chunk_image_data.append({
                            'id': marker_info['id'],
                            'marker': marker_info['marker'],
                            'position': img_pos - chunk_start_in_chapter
                        })

                display_text = clean_text_chunk
                for img_data in sorted(chunk_image_data, key=lambda x: x['position'], reverse=True):
                    display_text = display_text[:img_data['position']] + img_data['marker'] + display_text[img_data['position']:]

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
                    "start_idx": chunk_start_char,
                    "end_idx": chunk_end_char,
                    "_content_hash": hashlib.md5(clean_text_chunk.encode()).hexdigest()[:16],
                    "text": clean_text_chunk,
                    "display_text": display_text,
                    "length": len(clean_text_chunk),
                    "chapter_index": chapter_idx,
                    "image_data": final_image_data
                })
                chapter_clean_pos = chunk_end_in_chapter
                current_char_pos = chunk_end_char
                chunk_index += 1

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

        # B-11: Pre-compute binary-searchable time index
        chunk_time_index = [
            {"start_time": c["start_idx"], "chunk_index": c["index"]}
            for c in all_text_chunks
        ]

        return {
            "title": file_path.stem,
            "chapters": chapters,
            "chunks": all_text_chunks,
            "images": all_images,
            "total_chars": current_char_pos,
            "total_chunks": len(all_text_chunks),
            "chunk_time_index": chunk_time_index
        }

    def _get_cache_file(self, file_path: Path, with_images: bool) -> Path:
        """Get the cache file path for a parsed ebook."""
        file_hash = self._compute_file_hash(file_path)[:12]
        safe_stem = "".join(c if c.isalnum() or c in '-_' else '_' for c in file_path.stem)[:50]
        suffix = "_with_images" if with_images else ""
        cache_dir = settings.STORAGE_DIR / "stream_cache"
        return cache_dir / f"{safe_stem}_{file_hash}{suffix}.json"

    def _compute_file_hash(self, file_path: Path) -> str:
        """Compute MD5 hash of file."""
        hash_md5 = hashlib.md5()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_md5.update(chunk)
        return hash_md5.hexdigest()

    def clear_cache(self, file_path: Path, with_images: bool = False):
        """Clear cache for a specific ebook."""
        cache_file = self._get_cache_file(file_path, with_images)
        if cache_file.exists():
            cache_file.unlink()
        try:
            file_hash = self._compute_file_hash(file_path)
        except Exception:
            file_hash = ""
        cache_key = f"{file_path}:{file_hash}:{with_images}"
        self._parse_cache.pop(cache_key, None)
