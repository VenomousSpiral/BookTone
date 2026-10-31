"""Background parsing helpers used after ebook uploads."""

import logging
import threading
from pathlib import Path

from app.services.ebook_parser import EbookParser

logger = logging.getLogger(__name__)


def preparse_ebook(file_path: Path) -> None:
    """Build the parse caches needed for fast streaming after upload."""
    parser = EbookParser()
    parser.parse_and_cache(file_path, with_images=False)
    if file_path.suffix.lower() in {".epub", ".pdf"}:
        parser.parse_and_cache(file_path, with_images=True)


def start_preparse(file_path: Path) -> threading.Thread:
    """Start upload pre-parsing in a managed daemon thread."""
    def worker() -> None:
        try:
            logger.debug("[UPLOAD] Pre-parse started: %s", file_path)
            preparse_ebook(file_path)
            logger.debug("[UPLOAD] Pre-parse completed: %s", file_path)
        except Exception:
            logger.exception("[UPLOAD] Pre-parse failed: %s", file_path)

    thread = threading.Thread(
        target=worker,
        name=f"preparse-{file_path.name}",
        daemon=True,
    )
    thread.start()
    return thread
