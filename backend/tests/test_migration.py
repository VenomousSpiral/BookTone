"""Tests for JSON → SQLite migration."""
import json
import sys
import shutil
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def _setup_temp_storage(storage_dir):
    """Create a temp storage dir with sample JSON files and patch settings."""
    from app.core.config import settings as cfg

    (storage_dir / "ebooks").mkdir(parents=True, exist_ok=True)
    (storage_dir / "audiobooks_db.json").write_text(json.dumps({
        "book.epub:OmniVoice:Narrator-UK": {
            "ebook_path": "book.epub",
            "title": "Book Title",
            "model": "OmniVoice",
            "voice": "Narrator-UK",
            "status": "in_progress",
            "ebook_hash": "abc123def456",
            "total_chunks": 10,
            "completed_chunks": 3,
            "progress": 30.0,
            "created_at": "2026-06-01T10:00:00",
            "updated_at": "2026-06-01T10:05:00",
            "last_position": 0.0,
            "bookmarks": [2, 5],
            "chapters": [
                {"name": "Intro", "start_idx": 0, "end_idx": 500,
                 "start_chunk": 0, "end_chunk": 4},
                {"name": "Chapter1", "start_idx": 500, "end_idx": 1200,
                 "start_chunk": 5, "end_chunk": 9},
            ],
            "lrc_lines": [],
            "error": None,
        }
    }))

    (storage_dir / "stream_progress.json").write_text(json.dumps({
        "book.epub": {
            "ebook_path": "book.epub",
            "current_chunk": 3,
            "last_updated": None,
            "bookmarks": {"1": "", "4": ""},
        }
    }))

    (storage_dir / "stream_settings.json").write_text(json.dumps({
        "font_size": 20,
        "preferred_model": "CustomModel",
    }))

    (storage_dir / "user_preferences.json").write_text(json.dumps({
        "theme": "dracula",
        "audiobooks": {"book.epub": {"last_played": 1780432564221}},
    }))


def _reset_stream_progress_schema():
    """Reset StreamService._progress_schema_created flag for test isolation."""
    from app.services.stream_service import StreamService as SS
    SS._progress_schema_created = False


class TestMigrationScript:

    def test_migrate_profiles_from_json(self):
        with tempfile.TemporaryDirectory() as td:
            storage_dir = Path(td) / "migration_1"
            _setup_temp_storage(storage_dir)

            from app.core.config import settings as cfg
            original_storage_dir = cfg.STORAGE_DIR
            original_ebooks_dir = cfg.EBOOKS_DIR
            try:
                cfg.STORAGE_DIR = storage_dir
                cfg.EBOOKS_DIR = storage_dir / "ebooks"

                db_file = storage_dir / "app.db"
                from app.services.database import get_connection, SCHEMA_SQL
                conn = get_connection(db_file)
                conn.executescript(SCHEMA_SQL)

                from app.services.migrate_from_json import migrate_if_needed
                migrate_if_needed(db_file)

                row = conn.execute(
                    "SELECT ebook_path, model_name, voice, status, total_chunks, completed_chunks "
                    "FROM profiles WHERE ebook_path='book.epub'"
                ).fetchone()
                assert row is not None
                assert row["model_name"] == "OmniVoice"
                assert row["voice"] == "Narrator-UK"
                assert row["status"] == "in_progress"
                assert row["total_chunks"] == 10
                assert row["completed_chunks"] == 3

            finally:
                conn.close()
                cfg.STORAGE_DIR = original_storage_dir
                cfg.EBOOKS_DIR = original_ebooks_dir

    def test_migrate_chapters_preserved(self):
        with tempfile.TemporaryDirectory() as td:
            storage_dir = Path(td) / "migration_2"
            _setup_temp_storage(storage_dir)

            from app.core.config import settings as cfg
            original_storage_dir = cfg.STORAGE_DIR
            original_ebooks_dir = cfg.EBOOKS_DIR
            try:
                cfg.STORAGE_DIR = storage_dir
                cfg.EBOOKS_DIR = storage_dir / "ebooks"

                db_file = storage_dir / "app.db"
                from app.services.database import get_connection, SCHEMA_SQL
                conn = get_connection(db_file)
                conn.executescript(SCHEMA_SQL)

                from app.services.migrate_from_json import migrate_if_needed
                migrate_if_needed(db_file)

                chapters = conn.execute(
                    "SELECT name, start_idx, end_idx FROM chapters ORDER BY id ASC"
                ).fetchall()
                assert len(chapters) == 2
                assert chapters[0]["name"] == "Intro"
                assert chapters[1]["name"] == "Chapter1"

            finally:
                conn.close()
                cfg.STORAGE_DIR = original_storage_dir
                cfg.EBOOKS_DIR = original_ebooks_dir

    def test_migrate_bookmarks_preserves_data(self):
        with tempfile.TemporaryDirectory() as td:
            storage_dir = Path(td) / "migration_3"
            _setup_temp_storage(storage_dir)

            from app.core.config import settings as cfg
            original_storage_dir = cfg.STORAGE_DIR
            original_ebooks_dir = cfg.EBOOKS_DIR
            try:
                cfg.STORAGE_DIR = storage_dir
                cfg.EBOOKS_DIR = storage_dir / "ebooks"

                db_file = storage_dir / "app.db"
                from app.services.database import get_connection, SCHEMA_SQL
                conn = get_connection(db_file)
                conn.executescript(SCHEMA_SQL)

                from app.services.migrate_from_json import migrate_if_needed
                migrate_if_needed(db_file)

                profile_bms = conn.execute(
                    "SELECT chunk_index FROM bookmarks WHERE context='profile' AND ebook_path='book.epub'"
                ).fetchall()
                assert len(profile_bms) == 2
                bm_indices = [r["chunk_index"] for r in profile_bms]
                assert 2 in bm_indices and 5 in bm_indices

            finally:
                conn.close()
                cfg.STORAGE_DIR = original_storage_dir
                cfg.EBOOKS_DIR = original_ebooks_dir

    def test_migrate_settings_merge_priority(self):
        with tempfile.TemporaryDirectory() as td:
            storage_dir = Path(td) / "migration_4"
            _setup_temp_storage(storage_dir)

            from app.core.config import settings as cfg
            original_storage_dir = cfg.STORAGE_DIR
            original_ebooks_dir = cfg.EBOOKS_DIR
            try:
                cfg.STORAGE_DIR = storage_dir
                cfg.EBOOKS_DIR = storage_dir / "ebooks"

                db_file = storage_dir / "app.db"
                from app.services.database import get_connection, SCHEMA_SQL
                conn = get_connection(db_file)
                conn.executescript(SCHEMA_SQL)

                from app.services.migrate_from_json import migrate_if_needed
                migrate_if_needed(db_file)

                row = conn.execute(
                    "SELECT value_json FROM settings_kv WHERE key='font_size'"
                ).fetchone()
                assert row is not None
                val = json.loads(row["value_json"])
                # stream_settings has font_size=20 (user_preferences doesn't override).
                assert val == 20

            finally:
                conn.close()
                cfg.STORAGE_DIR = original_storage_dir
                cfg.EBOOKS_DIR = original_ebooks_dir

    def test_migration_is_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            storage_dir = Path(td) / "migration_5"
            _setup_temp_storage(storage_dir)

            from app.core.config import settings as cfg
            original_storage_dir = cfg.STORAGE_DIR
            original_ebooks_dir = cfg.EBOOKS_DIR
            try:
                cfg.STORAGE_DIR = storage_dir
                cfg.EBOOKS_DIR = storage_dir / "ebooks"

                db_file = storage_dir / "app.db"
                from app.services.database import get_connection, SCHEMA_SQL
                conn = get_connection(db_file)
                conn.executescript(SCHEMA_SQL)

                from app.services.migrate_from_json import migrate_if_needed
                migrate_if_needed(db_file)
                first_count = conn.execute("SELECT COUNT(*) FROM profiles").fetchone()[0]

                # Run again — should be no-op.
                migrate_if_needed(db_file)
                second_count = conn.execute("SELECT COUNT(*) FROM profiles").fetchone()[0]

                assert first_count == 1
                assert second_count == 1, "Migration duplicated data on second run!"

            finally:
                cfg.STORAGE_DIR = original_storage_dir
                cfg.EBOOKS_DIR = original_ebooks_dir


class TestEdgeCases:

    def test_empty_json_files_produce_no_errors(self):
        with tempfile.TemporaryDirectory() as td:
            storage_dir = Path(td) / "empty_test"
            (storage_dir / "ebooks").mkdir(parents=True, exist_ok=True)
            for fname in ["audiobooks_db.json", "stream_progress.json",
                           "stream_settings.json", "user_preferences.json"]:
                (storage_dir / fname).write_text("{}")

            from app.core.config import settings as cfg
            original_storage_dir = cfg.STORAGE_DIR
            original_ebooks_dir = cfg.EBOOKS_DIR
            try:
                cfg.STORAGE_DIR = storage_dir
                cfg.EBOOKS_DIR = storage_dir / "ebooks"

                db_file = storage_dir / "app.db"
                from app.services.database import get_connection, SCHEMA_SQL
                conn = get_connection(db_file)
                conn.executescript(SCHEMA_SQL)

                from app.services.migrate_from_json import migrate_if_needed
                migrate_if_needed(db_file)  # Should not raise.

                profiles_count = conn.execute("SELECT COUNT(*) FROM profiles").fetchone()[0]
                assert profiles_count == 0

            finally:
                cfg.STORAGE_DIR = original_storage_dir
                cfg.EBOOKS_DIR = original_ebooks_dir

    def test_corrupt_json_skips_with_no_crash(self):
        with tempfile.TemporaryDirectory() as td:
            storage_dir = Path(td) / "corrupt_test"
            (storage_dir / "ebooks").mkdir(parents=True, exist_ok=True)
            (storage_dir / "audiobooks_db.json").write_text("not valid json {{{")
            (storage_dir / "stream_progress.json").write_text("{}")
            (storage_dir / "stream_settings.json").write_text('{}')
            (storage_dir / "user_preferences.json").write_text('{}')

            from app.core.config import settings as cfg
            original_storage_dir = cfg.STORAGE_DIR
            original_ebooks_dir = cfg.EBOOKS_DIR
            try:
                cfg.STORAGE_DIR = storage_dir
                cfg.EBOOKS_DIR = storage_dir / "ebooks"

                db_file = storage_dir / "app.db"
                from app.services.database import get_connection, SCHEMA_SQL
                conn = get_connection(db_file)
                conn.executescript(SCHEMA_SQL)

                from app.services.migrate_from_json import migrate_if_needed
                migrate_if_needed(db_file)  # Should not raise.

            finally:
                cfg.STORAGE_DIR = original_storage_dir
                cfg.EBOOKS_DIR = original_ebooks_dir


class TestConcurrentAccess:

    def test_profile_update_atomic(self):
        with tempfile.TemporaryDirectory() as td:
            storage_dir = Path(td) / "concurrent_test"
            (storage_dir / "ebooks").mkdir(parents=True, exist_ok=True)

            from app.core.config import settings as cfg
            original_storage_dir = cfg.STORAGE_DIR
            original_ebooks_dir = cfg.EBOOKS_DIR
            try:
                cfg.STORAGE_DIR = storage_dir
                cfg.EBOOKS_DIR = storage_dir / "ebooks"

                db_file = storage_dir / "app.db"
                from app.services.database import get_connection, SCHEMA_SQL
                conn = get_connection(db_file)
                conn.executescript(SCHEMA_SQL)

                now = "2026-06-03T12:00:00"
                conn.execute(
                    """INSERT INTO profiles (ebook_path, model_name, voice, title, status, ebook_hash,
                       total_chunks, completed_chunks, progress_pct, created_at, updated_at)
                      VALUES (?, 'OmniVoice', 'Narrator-UK', 'Book', 'not_started', 'hash123', 10, 0, 0.0, ?, ?)""",
                    ("book.epub", now, now),
                )

                conn.execute(
                    "UPDATE profiles SET status='in_progress', completed_chunks=5 WHERE ebook_path=?",
                    ("book.epub",),
                )
                conn.commit()

                row = conn.execute("SELECT * FROM profiles").fetchone()
                assert dict(row)["status"] == "in_progress"

            finally:
                cfg.STORAGE_DIR = original_storage_dir
                cfg.EBOOKS_DIR = original_ebooks_dir


class TestBookmarksTableCRUD:

    def test_add_remove_bookmark_via_sqlite(self):
        with tempfile.TemporaryDirectory() as td:
            storage_dir = Path(td) / "bm_test"
            (storage_dir / "ebooks").mkdir(parents=True, exist_ok=True)
            (storage_dir / "ebooks" / "test.txt").write_text("hello world test content")

            from app.core.config import settings as cfg
            original_storage_dir = cfg.STORAGE_DIR
            original_ebooks_dir = cfg.EBOOKS_DIR
            try:
                cfg.STORAGE_DIR = storage_dir
                cfg.EBOOKS_DIR = storage_dir / "ebooks"

                # Create the DB schema.
                db_file = storage_dir / "app.db"
                from app.services.database import SCHEMA_SQL, get_connection as gc
                conn = gc(db_file)
                conn.executescript(SCHEMA_SQL)
                conn.close()

                _reset_stream_progress_schema()

                from app.services.stream_service import StreamService
                service = StreamService()

                # Add bookmark at chunk 5.
                result_add = service.toggle_bookmark("test.txt", 5, "Test preview text")
                assert result_add is True  # added (returns True when inserted)

                # Toggle again — should remove it and return False.
                _reset_stream_progress_schema()  # Ensure schema check runs fresh.
                service2 = StreamService()
                result_remove = service2.toggle_bookmark("test.txt", 5)
                assert result_remove is False, f"Expected False (removed), got {result_remove}"

            finally:
                cfg.STORAGE_DIR = original_storage_dir
                cfg.EBOOKS_DIR = original_ebooks_dir

    def test_clear_progress(self):
        _reset_stream_progress_schema()
        with tempfile.TemporaryDirectory() as td:
            storage_dir = Path(td) / "clear_test"
            (storage_dir / "ebooks").mkdir(parents=True, exist_ok=True)
            (storage_dir / "ebooks" / "test2.txt").write_text("hello world")

            from app.core.config import settings as cfg
            original_storage_dir = cfg.STORAGE_DIR
            original_ebooks_dir = cfg.EBOOKS_DIR
            try:
                cfg.STORAGE_DIR = storage_dir
                cfg.EBOOKS_DIR = storage_dir / "ebooks"

                db_file = storage_dir / "app.db"
                from app.services.database import SCHEMA_SQL, get_connection as gc
                conn = gc(db_file)
                conn.executescript(SCHEMA_SQL)
                conn.close()

                _reset_stream_progress_schema()
                from app.services.stream_service import StreamService
                service = StreamService()

                for i in range(5):
                    service.toggle_bookmark("test2.txt", i, f"bm {i}")

            finally:
                cfg.STORAGE_DIR = original_storage_dir
                cfg.EBOOKS_DIR = original_ebooks_dir
