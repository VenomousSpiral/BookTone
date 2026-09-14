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

    @staticmethod
    def _extract_chapter_name(soup: BeautifulSoup, item) -> str:
        """Extract a human-readable chapter name from an EPUB document's HTML.

        Priority: <h2> / <h1> headings → <title> tag → epublib EpubHtml.title →
                  get_name() (filename) → 'Chapter N'.

        Note: We prefer h2 over h1 because most well-formed EPUBs use h2 for actual
        chapter headings (often with class="heading"), while Calibre-generated EPUBs
        often put page-break markers in h1 tags that may contain incorrect/offset
        numbers. This prevents issues like "Chapter 7" followed by "Chapter 6"
        when consecutive split files use different heading elements.
        """
        # 1. Look for h2 headings first (most reliable chapter indicators),
        #    then fall back to h1 if no h2 found
        el = soup.find('h2')
        if not el:
            el = soup.find('h1')
        
        if el:
            text = el.get_text(strip=True)
            if text and len(text) < 300:   # guard against giant headings
                return text

        # 2. Fall back to the document's <title> element (inside <head>)
        title_tag = soup.find('title')
        if title_tag:
            text = title_tag.get_text(strip=True)
            if text and len(text) < 300:
                return text

        # 3. epublib EpubHtml.title attribute (often set to "Chapter N")
        if hasattr(item, 'title') and item.title:
            t = str(item.title).strip()
            if t and t != '':
                return t

        # 4. Fall back to the internal filename
        name = ''
        if item is not None:
            try:
                name = item.get_name() or ''
            except (AttributeError, TypeError):
                pass
        if name:
            return Path(name).stem   # strip extension for readability

        return "Chapter"
    
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
            
            # Collect all document items with their content
            doc_items = []
            for item in book.get_items():
                if item.get_type() == ebooklib.ITEM_DOCUMENT:
                    soup = BeautifulSoup(item.get_content(), 'html.parser')
                    
                    # Remove images but preserve spacing - replace with space to prevent text merging
                    for img in soup.find_all('img'):
                        img.replace_with(' ')
                    for svg in soup.find_all('svg'):
                        svg.replace_with(' ')
                    
                    name = item.get_name()
                    # Extract body text (skip title/metadata at very top of file)
                    all_text = soup.get_text(separator=' ', strip=True)
                    all_text = re.sub(r' +', ' ', all_text)
                    
                    doc_items.append({
                        'name': name,
                        'soup': soup,
                        'text': all_text
                    })
            
            # Sort by filename to ensure consecutive split files are adjacent
            doc_items.sort(key=lambda x: x['name'])
            
            chunks = self._merge_split_pairs(doc_items)
            return chunks
        except Exception as e:
            raise ValueError(f"Error parsing EPUB: {str(e)}")
    
    def _get_body_text(self, soup: BeautifulSoup) -> str:
        """Extract meaningful body text from a document, skipping title/metadata at top.
        
        Calibre split files often have metadata (title page info) before the actual
        chapter content. This method tries to find where real story text begins by
        looking for paragraphs that aren't part of the book's front matter.
        """
        # Get all paragraph text with their positions in original document order
        paragraphs = []
        for p in soup.find_all(['p', 'span']):
            text = ''.join(p.stripped_strings).strip()
            if text:
                paragraphs.append(text)
        
        if not paragraphs:
            return ''
        
        # Skip initial metadata lines (title, author, etc.) - usually short and contain
        # keywords like "Through", "the Heart", book title patterns, or special chars  
        start_idx = 0
        for i, para in enumerate(paragraphs):
            # Look for actual story content: longer paragraphs with regular prose
            if len(para) > 100 and not any(kw in para.lower() for kw in [
                'through the heart',
                '無職転生', 
                '異世界行ったら本気だす',
                '理不尽な孫の手',
                '| mushoku'
            ]):
                # Check if this looks like actual story text (has dialogue or narrative)
                if any(marker in para for marker in ['"', "'", '.', ',', '!']):
                    start_idx = i
                    break
        
        body_text = ' '.join(paragraphs[start_idx:])
        return re.sub(r' +', ' ', body_text).strip()
    
    def _texts_overlap(self, text1: str, text2: str) -> float:
        """Calculate overlap ratio between two texts using word-level Jaccard similarity.
        Returns 0.0 to 1.0."""
        words1 = set(text1.lower().split())
        words2 = set(text2.lower().split())
        
        if not words1 or not words2:
            return 0.0
        
        intersection = len(words1 & words2)
        union = len(words1 | words2)
        return intersection / union if union > 0 else 0.0
    
    def _merge_split_pairs(self, doc_items: list[dict]) -> list[dict[str, str]]:
        """Merge Calibre's split file pairs into single chunks.
        
        Some Calibre-exported EPUBs create TWO items per logical chapter:
        - An even-numbered TOC page with correct <h2 class="heading"> numbering
          (often contains only metadata/chapter notes, minimal story text)
        - An odd-numbered content page with the full chapter text
          (may have wrong/offset h1 numbers like "Chapter 6" when it should be "Chapter 7")
        
        We detect pairs by:
        1. Consecutive numbered filenames (_split_NNN.xhtml pattern)
        2. Similar or matching chapter names between the two files
           (Calibre duplicates have same/near-identical headings; separate chapters differ)
        
        For non-paired files, they pass through unchanged.
        """
        if not doc_items:
            return []
        
        import re as regex
        def _is_consecutive_split(name_a: str, name_b: str) -> bool:
            """Check if two filenames are consecutive numbered split files."""
            m1 = regex.search(r'_split_(\d{3})\.xhtml$', name_a)
            m2 = regex.search(r'_split_(\d{3})\.xhtml$', name_b)
            if not (m1 and m2):
                return False
            n1, n2 = int(m1.group(1)), int(m2.group(1))
            return abs(n1 - n2) == 1
        
        def _chapters_match(name_a: str, name_b: str) -> bool:
            """Check if two files likely represent the same chapter (Calibre duplicate pair).
            
            Returns True if both have similar/non-conflicting chapter names,
            indicating they're Calibre's split-pair duplicates.
            Returns False if chapters clearly differ, meaning separate content.
            """
            h1_a = name_a.split('/')[-1]
            h2_b = name_b.split('/')[-1]  # simplified - we'll use actual soup
            
        chunks = []
        i = 0
        
        while i < len(doc_items):
            item = doc_items[i]
            chapter_name_i = self._extract_chapter_name(item['soup'], None)
            
            # Check if next file is a Calibre split pair
            should_merge = False
            j = i + 1
            
            if j < len(doc_items):
                next_item = doc_items[j]
                chapter_name_j = self._extract_chapter_name(next_item['soup'], None)
                
                # Check filename pattern for consecutive numbered splits
                is_consecutive = _is_consecutive_split(item['name'], next_item['name'])
                
                if is_consecutive:
                    # Only merge if chapter names are similar (Calibre duplicate pair)
                    # or one has no meaningful heading. If chapters clearly differ,
                    # they're separate content that happens to be consecutive.
                    name_i_clean = self._normalize_chapter_name(chapter_name_i)
                    name_j_clean = self._normalize_chapter_name(chapter_name_j)
                    
                    # Only merge if normalized names match exactly.
                    # This ensures Calibre dual-split pairs (same heading in both files) are merged,
                    # while separate consecutive chapters (different headings) remain as-is.
                    is_same_chapter = (name_i_clean == name_j_clean)
                    
                    if is_same_chapter:
                        should_merge = True
            
            if should_merge:
                next_item = doc_items[j]
                combined_text = item['text'] + ' ' + next_item['text']
                combined_text = re.sub(r' +', ' ', combined_text).strip()
                best_chapter_name = self._select_better_chapter(item, next_item)
                
                chunks.append({
                    'text': combined_text,
                    'chapter': best_chapter_name
                })
                i += 2
            else:
                # Keep the file if it has any text content OR a meaningful chapter heading.
                # This avoids filtering out manga-style EPUBs where images ARE the story,
                # since after replacing <img> with ' ', _has_real_content would return False
                # even though these files have valid chapter structure (h2 headings).
                has_heading = bool(chapter_name_i)
                if item['text'] and (len(item['text'].strip()) > 10 or has_heading):
                    chunks.append({
                        'text': item['text'],
                        'chapter': chapter_name_i
                    })
                i += 1
        
        return chunks
    
    def _select_better_chapter(self, item_a: dict, item_b: dict) -> str:
        """Select the better chapter name between two split pair items.
        
        Prefers h2-derived names (more reliable in Calibre EPUBs where even-numbered
        TOC pages use <h2 class="heading"> with correct numbers).
        Falls back to whichever has more descriptive content.
        """
        name_a = self._extract_chapter_name(item_a['soup'], None)
        name_b = self._extract_chapter_name(item_b['soup'], None)
        
        # If both names are the same, return it
        if name_a == name_b:
            return name_a
        
        # Check which one has an h2 tag (more reliable source)
        a_has_h2 = item_a['soup'].find('h2') is not None
        b_has_h2 = item_b['soup'].find('h2') is not None
        
        if a_has_h2 and not b_has_h2:
            return name_a
        elif b_has_h2 and not a_has_h2:
            return name_b
        else:
            # Both have h2 or both don't - prefer the one with more descriptive content  
            # (longer names are usually chapter titles, short ones like "Chapter N" may be placeholders)
            if len(name_a) > len(name_b):
                return name_a
            elif len(name_b) > len(name_a):
                return name_b
            else:
                # Names same length - prefer alphabetically first (usually the correct TOC order)
                return min(name_a, name_b)
    
    def _normalize_chapter_name(self, name: str) -> str:
        """Normalize chapter names for comparison.
        
        Strips leading 'Chapter N:' prefix and normalizes whitespace
        to allow fuzzy matching between slightly different representations.
        """
        import re as regex
        # Remove 'Chapter X: ' or 'Chapter X - ' prefixes
        normalized = regex.sub(r'^[Cc]hapter\s+\d+[.:\-]\s*', '', name.strip())
        return normalized.lower()
    
    def _has_real_content(self, soup: BeautifulSoup) -> bool:
        """Check if a document has actual meaningful content beyond metadata/title page.
        
        Calibre's even-numbered split files often contain only chapter notes or
        minimal metadata. We want to skip those when they're not part of a pair merge.
        For non-Calibre-split EPUBs, we keep all documents that have any text content.
        """
        # Get total text length (excluding heading elements)
        soup_copy = BeautifulSoup(str(soup), 'html.parser')
        for tag in ['h1', 'h2']:
            for el in soup_copy.find_all(tag):
                el.decompose()
        
        body_text = ''.join(soup_copy.stripped_strings).strip()
        
        # If there's any substantial text beyond just a title/metadata line, keep it
        if len(body_text) > 20:
            return True
        
        # Otherwise check paragraph count for small documents
        para_count = sum(1 for p in soup.find_all(['p', 'span'])
                        if len(''.join(p.stripped_strings).strip()) > 30)
        return para_count >= 2
    
    def _merge_split_pairs_with_images(self, doc_items: list[dict]) -> list[dict[str, str]]:
        """Merge Calibre's split file pairs for image-aware parsing.
        
        Key fix: When merging two files, we must rename markers from the second
        file so they don't collide with indices from the first file. Each original
        EPUB file has its own marker_index starting at 0, but after concatenation
        all markers share one namespace - duplicate "<<<IMAGE_0>>>" strings would
        break downstream position tracking.
        """
        import re as regex
        
        def _is_consecutive_split(name_a: str, name_b: str) -> bool:
            m1 = regex.search(r'_split_(\d{3})\.xhtml$', name_a)
            m2 = regex.search(r'_split_(\d{3})\.xhtml$', name_b)
            if not (m1 and m2): return False
            n1, n2 = int(m1.group(1)), int(m2.group(1))
            return abs(n1 - n2) == 1
        
        def _renumber_markers(markers: list[dict], offset: int) -> list[dict]:
            """Renumber markers by adding an offset to their indices."""
            if not markers:
                return []
            result = []
            for m in markers:
                old_marker = m['marker']  # e.g., '<<<IMAGE_3>>>'
                new_idx = int(regex.search(r'\d+', old_marker).group()) + offset
                new_marker = f'<<<IMAGE_{new_idx}>>>'
                result.append({**m, 'marker': new_marker})
            return result
        
        chunks = []
        i = 0
        
        while i < len(doc_items):
            item = doc_items[i]
            chapter_name_i = self._extract_chapter_name(item['soup'], None)
            should_merge = False
            j = i + 1
            
            if j < len(doc_items):
                next_item = doc_items[j]
                chapter_name_j = self._extract_chapter_name(next_item['soup'], None)
                is_consecutive = _is_consecutive_split(item['name'], next_item['name'])
                
                if is_consecutive:
                    name_i_clean = self._normalize_chapter_name(chapter_name_i)
                    name_j_clean = self._normalize_chapter_name(chapter_name_j)
                    
                    # Only merge if names match. For non-Chapter items like Preface/Title,
                    # require exact normalized equality since they could be unrelated pages.
                    is_same_chapter = (name_i_clean == name_j_clean)
                    
                    if is_same_chapter:
                        should_merge = True
            
            if should_merge:
                next_item = doc_items[j]
                # Combine text, removing any extra space between files
                combined_text = item['text'] + ' ' + next_item['text']
                combined_text = re.sub(r' +', ' ', combined_text).strip()
                best_chapter_name = self._select_better_chapter(item, next_item)
                
                # Get markers from both files
                first_markers = item.get('image_markers', [])
                second_markers = next_item.get('image_markers', [])
                
                if not first_markers and not second_markers:
                    all_markers = []
                elif not second_markers:
                    # Only first file has markers - no renumbering needed
                    all_markers = list(first_markers)
                else:
                    # Renumber second file's markers to avoid index collision
                    offset = len(first_markers)  # Start numbering after first file's last marker
                    renamed_second = _renumber_markers(second_markers, offset)
                    
                    # Also update the text of the second item by replacing old markers with new ones
                    for orig_m in second_markers:
                        old_marker_str = orig_m['marker']
                        new_idx_val = int(regex.search(r'\d+', old_marker_str).group()) + offset
                        new_marker_str = f'<<<IMAGE_{new_idx_val}>>>'
                        combined_text = combined_text.replace(old_marker_str, new_marker_str)
                    
                    all_markers = first_markers + renamed_second
                
                chunks.append({
                    'text': combined_text,
                    'chapter': best_chapter_name,
                    'image_markers': all_markers
                })
                i += 2
            else:
                if item['text'] or item.get('image_markers'):
                    chapter_name = self._extract_chapter_name(item['soup'], None)
                    chunks.append({
                        'text': item['text'],
                        'chapter': chapter_name,
                        'image_markers': item.get('image_markers', [])
                    })
                i += 1
        
        return chunks
    
    def _parse_epub_with_images(self, file_path: Path) -> tuple[list[dict[str, str]], dict[str, str]]:
        """Parse EPUB file and extract images"""
        try:
            book = epub.read_epub(str(file_path))
            
            # First, extract all images from the EPUB
            images = {}
            image_items = {}
            for item in book.get_items():
                if item.get_type() == ebooklib.ITEM_IMAGE:
                    img_name = item.get_name()
                    img_data = item.get_content()
                    img_ext = Path(img_name).suffix.lower()
                    mime_map = {
                        '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
                        '.png': 'image/png', '.gif': 'image/gif',
                        '.svg': 'image/svg+xml', '.webp': 'image/webp'
                    }
                    mime_type = mime_map.get(img_ext, 'image/png')
                    img_id = hashlib.md5(img_name.encode()).hexdigest()[:12]
                    img_base64 = base64.b64encode(img_data).decode('utf-8')
                    images[img_id] = f"data:{mime_type};base64,{img_base64}"
                    image_items[img_name] = img_id
                    image_items[Path(img_name).name] = img_id
            
            # Collect all document items with processed content (images → markers)
            doc_items = []
            for item in book.get_items():
                if item.get_type() == ebooklib.ITEM_DOCUMENT:
                    soup = BeautifulSoup(item.get_content(), 'html.parser')
                    image_markers = []
                    marker_index = 0
                    
                    # Replace images with placeholders
                    for img in soup.find_all('img'):
                        src = img.get('src', '')
                        img_path = src.split('?')[0].split('#')[0]
                        img_filename = img_path.split('/')[-1] if '/' in img_path else img_path
                        img_path_normalized = img_path.lstrip('./')
                        
                        found_id = None
                        for name, iid in image_items.items():
                            nfn = Path(name).name
                            if img_filename == nfn:
                                found_id = iid
                                break
                            if name.endswith(img_path_normalized) or img_path_normalized.endswith(name):
                                found_id = iid
                                break
                            if img_filename and img_filename in name:
                                found_id = iid
                                break
                        
                        marker = f" <<<IMAGE_{marker_index}>>> "
                        img.replace_with(marker)
                        if found_id:
                            image_markers.append({'marker': marker, 'id': found_id})
                        marker_index += 1
                    
                    # Handle SVG elements
                    for svg in soup.find_all('svg'):
                        svg_str = str(svg)
                        svg_id = hashlib.md5(svg_str.encode()).hexdigest()[:12]
                        svg_base64 = base64.b64encode(svg_str.encode()).decode('utf-8')
                        images[svg_id] = f"data:image/svg+xml;base64,{svg_base64}"
                        marker = f" <<<IMAGE_{marker_index}>>> "
                        svg.replace_with(marker)
                        image_markers.append({'marker': marker, 'id': svg_id})
                        marker_index += 1
                    
                    text_with_markers = soup.get_text(separator=' ', strip=True)
                    text_with_markers = re.sub(r' +', ' ', text_with_markers)
                    text_with_markers = re.sub(r'<<<\s*IMAGE_(\d+)\s*>>>', r'<<<IMAGE_\1>>>', text_with_markers)
                    
                    doc_items.append({
                        'name': item.get_name(),
                        'soup': soup,
                        'text': text_with_markers,
                        'image_markers': image_markers
                    })
            
            # Sort by filename to ensure consecutive split files are adjacent
            doc_items.sort(key=lambda x: x['name'])
            
            chunks = self._merge_split_pairs_with_images(doc_items)
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

            # Calculate normalized positions by iterating through RAW text.
            # Key fix: only count a space as separator (not leading whitespace)
            # when the next non-space content after this position leads to actual
            # text, not directly into an IMAGE marker. This prevents off-by-one
            # errors where trailing spaces before markers are counted but then 
            # stripped by .strip() from clean_chapter_text.
            image_positions = []
            ncp = 0
            marked_pos = 0
            last_was_space = False

            while marked_pos < len(chapter_text_with_markers):
                remaining = chapter_text_with_markers[marked_pos:]
                is_marker_start = remaining.startswith('<<<IMAGE_')

                if not is_marker_start:
                    char = remaining[0] if remaining else ''
                    is_space = char == ' '

                    if not is_space:
                        ncp += 1
                        last_was_space = False
                    elif not last_was_space and ncp > 0:
                        # Check: does the next non-space content after this space 
                        # lead to actual text (not directly into an IMAGE marker)?
                        rest_after_spaces = remaining.lstrip()
                        if rest_after_spaces.startswith('<<<IMAGE_'):
                            # Trailing space before a marker - don't count as separator
                            pass
                        else:
                            ncp += 1  # Count as content separator
                    last_was_space = is_space
                
                if is_marker_start:
                    marker_match = marker_pattern.match(remaining)
                    matched_marker_text = marker_match.group()  # e.g., '<<<IMAGE_0>>>' (no spaces)
                    for marker_info in image_markers:
                        stored = marker_info['marker']
                        # Stored markers may have surrounding whitespace; strip before comparing
                        if stored.strip() == matched_marker_text or stored == matched_marker_text:
                            image_positions.append((ncp, marker_info))
                            break
                    marked_pos += len(marker_match.group())
                else:
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
