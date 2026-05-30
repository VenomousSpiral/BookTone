"""
Shared service instances — a single StreamService is created once
and reused across all route modules so that in-memory state
(bookmarks, progress) is consistent.
"""
from app.services.stream_service import StreamService

# Module-level singleton — created on first import, reused everywhere
stream_service = StreamService()

__all__ = ["stream_service"]
