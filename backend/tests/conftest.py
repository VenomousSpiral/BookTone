"""Shared pytest fixtures for backend tests."""


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
        # Between files — clean up stale SQLite sidecar files.
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

    _last_file = filepath
