"""
Models for streaming mode progress and bookmarks
"""
from pydantic import BaseModel
from typing import List, Dict, Union
from datetime import datetime


class StreamProgress(BaseModel):
    """Track streaming progress for an ebook"""
    ebook_path: str
    current_chunk: int = 0
    last_updated: datetime = None
    # Bookmarks: Dict mapping chunk_index (as string key) -> text preview
    # For backwards compatibility, also accepts List[int]
    bookmarks: Union[Dict[str, str], List[int]] = {}
    
    def __init__(self, **data):
        if 'last_updated' not in data or data['last_updated'] is None:
            data['last_updated'] = datetime.now()
        
        # Migrate old bookmarks format (List[int]) to new format (Dict[str, str])
        if 'bookmarks' in data and isinstance(data['bookmarks'], list):
            # Convert list of ints to dict with empty text previews
            data['bookmarks'] = {str(idx): "" for idx in data['bookmarks']}
        
        super().__init__(**data)
    
    @property
    def bookmark_indices(self) -> List[int]:
        """Get list of bookmark chunk indices (sorted)"""
        if isinstance(self.bookmarks, dict):
            return sorted([int(k) for k in self.bookmarks.keys()])
        return sorted(self.bookmarks)
    
    def has_bookmark(self, chunk_index: int) -> bool:
        """Check if a chunk is bookmarked"""
        if isinstance(self.bookmarks, dict):
            return str(chunk_index) in self.bookmarks
        return chunk_index in self.bookmarks
    
    def get_bookmark_text(self, chunk_index: int) -> str:
        """Get bookmark text preview for a chunk"""
        if isinstance(self.bookmarks, dict):
            return self.bookmarks.get(str(chunk_index), "")
        return ""
    
    def add_bookmark(self, chunk_index: int, text_preview: str = ""):
        """Add a bookmark with text preview"""
        if isinstance(self.bookmarks, dict):
            self.bookmarks[str(chunk_index)] = text_preview
        else:
            # Migrate to dict format
            self.bookmarks = {str(idx): "" for idx in self.bookmarks}
            self.bookmarks[str(chunk_index)] = text_preview
    
    def remove_bookmark(self, chunk_index: int):
        """Remove a bookmark"""
        if isinstance(self.bookmarks, dict):
            self.bookmarks.pop(str(chunk_index), None)
        elif chunk_index in self.bookmarks:
            self.bookmarks.remove(chunk_index)
