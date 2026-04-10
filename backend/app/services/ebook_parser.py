from pathlib import Path
from typing import List, Dict, Optional, Tuple
import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup
import re
from PyPDF2 import PdfReader
import base64
import hashlib

class EbookParser:
    """Parse ebooks and extract text content"""
    
    SUPPORTED_FORMATS = ['.epub', '.txt', '.html', '.pdf']
    
    def __init__(self):
        self._image_cache = {}  # Cache for extracted images {ebook_path: {image_id: base64_data}}
    
    def parse_ebook(self, file_path: Path) -> List[Dict[str, str]]:
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
    
    def parse_ebook_with_images(self, file_path: Path) -> Tuple[List[Dict[str, str]], Dict[str, str]]:
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
    
    def _parse_epub(self, file_path: Path) -> List[Dict[str, str]]:
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
    
    def _parse_epub_with_images(self, file_path: Path) -> Tuple[List[Dict[str, str]], Dict[str, str]]:
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
    
    def _parse_txt(self, file_path: Path) -> List[Dict[str, str]]:
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
    
    def _parse_html(self, file_path: Path) -> List[Dict[str, str]]:
        """Parse HTML file"""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                html_content = f.read()
            
            soup = BeautifulSoup(html_content, 'html.parser')
            text = soup.get_text(separator=' ', strip=True)
            
            return [{'text': text, 'chapter': 'Full Text'}]
        except Exception as e:
            raise ValueError(f"Error parsing HTML: {str(e)}")
    
    def _parse_pdf(self, file_path: Path) -> List[Dict[str, str]]:
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
    
    def _parse_pdf_with_images(self, file_path: Path) -> Tuple[List[Dict[str, str]], Dict[str, str]]:
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
                                    print(f"[DEBUG] Could not extract image from PDF page {i}: {img_err}")
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
    
    def _split_oversized_chunk(self, text: str, max_chars: int = 500) -> List[str]:
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
    
    def chunk_text(self, text: str, chunk_size: int = 4096, max_chunk_chars: int = 500) -> List[str]:
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
