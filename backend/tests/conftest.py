"""Shared pytest fixtures for backend tests."""


def _reset_stream_progress_schema_flag():
    """Reset StreamService._progress_schema_created between test files.

    This is needed because the flag is a class variable that persists across
    all TestClient instances and threads, but each test file may use different
    DB paths (temp dirs vs default storage/app.db).
    """
    try:
        from app.services.stream_service import StreamService as SS
        SS._progress_schema_created = False
    except Exception:
        pass  # Module may not be imported yet.


_seen_files = set()

# Track last test file to only cleanup between files.
_last_file = None


def pytest_runtest_protocol(item, nextitem):
    """Called before each test item runs."""
    global _last_file

    try:
        filepath = str(getattr(item, "fspath", None)) or ""
    except Exception:
        return

    if filepath != _last_file and _last_file is not None:
        # Between files — clean up stale DB state.
        try:
            import os as _os
            from app.core.config import settings as cfg
            db_base = str(cfg.STORAGE_DIR / "app.db")
            for ext in [".db-wal", ".db-shm"]:
                p = db_base + ext
                if _os.path.exists(p):
                    try:
                        _os.unlink(p)
                    except Exception:
                        pass
        except Exception:
            pass

    # Always reset the schema flag.
    _reset_stream_progress_schema_flag()
    _last_file = filepath
