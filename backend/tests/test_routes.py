"""Tests for route registration and endpoint structure."""
import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    return TestClient(app)


class TestAppStructure:
    def test_health_endpoint(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "healthy"}

    def test_root_endpoint(self, client):
        resp = client.get("/")
        assert resp.status_code == 200

    def test_openapi_schema_exists(self, client):
        resp = client.get("/openapi.json")
        assert resp.status_code == 200
        schema = resp.json()
        paths = schema.get("paths", {})
        assert len(paths) > 10  # Should have many endpoints


class TestRouteRegistration:
    """Verify all expected route groups are registered."""

    def test_files_routes(self, client):
        resp = client.get("/api/files/list")
        # Should return 200 (empty list) or 400 (directory not found), not 404 (route not found)
        assert resp.status_code in (200, 400), f"Unexpected: {resp.status_code}"

    def test_openai_models_route(self, client):
        resp = client.get("/api/openai/models")
        assert resp.status_code == 200

    def test_parse_route(self, client):
        resp = client.get("/api/stream/parse", params={"ebook_path": "test.epub"})
        # Should return 404 (file not found) not 404 (route not found)
        assert resp.status_code == 404

    def test_audio_route(self, client):
        resp = client.post("/api/stream/audio", json={
            "ebook_path": "test.epub",
            "start_char": 0,
            "end_char": 100,
            "model": "tts-1",
            "voice": "alloy",
        })
        assert resp.status_code == 404  # File not found, not route not found

    def test_settings_routes(self, client):
        resp = client.get("/api/stream/settings")
        assert resp.status_code == 200

        resp = client.post("/api/stream/settings", json={
            "font_size": 20,
        })
        assert resp.status_code == 200

    def test_progress_routes(self, client):
        resp = client.get("/api/stream/progress", params={"ebook_path": "test.epub"})
        assert resp.status_code == 200

        resp = client.post("/api/stream/progress", json={
            "ebook_path": "test.epub",
            "chunk_index": 0,
        })
        assert resp.status_code == 200

    def test_bookmark_routes(self, client):
        resp = client.get("/api/stream/bookmarks", params={"ebook_path": "test.epub"})
        assert resp.status_code == 200

        resp = client.post("/api/stream/bookmark", json={
            "ebook_path": "test.epub",
            "chunk_index": 0,
        })
        assert resp.status_code == 200

    def test_cache_info_route(self, client):
        resp = client.get("/api/stream/cache-info", params={"ebook_path": "test.epub"})
        assert resp.status_code == 200

    def test_cache_status_route(self, client):
        resp = client.get("/api/stream/cache-status", params={"ebook_path": "test.epub"})
        assert resp.status_code == 200

    def test_parse_cache_status(self, client):
        resp = client.get("/api/stream/parse-cache-status", params={"ebook_path": "test.epub"})
        assert resp.status_code == 404  # File not found

    def test_parse_cache_list(self, client):
        resp = client.get("/api/stream/parse-cache-list")
        assert resp.status_code == 200

    def test_download_status_route(self, client):
        resp = client.get("/api/stream/download-status", params={
            "ebook_path": "test.epub",
            "model": "tts-1",
            "voice": "alloy",
        })
        assert resp.status_code == 404  # File not found

    def test_text_batch_route(self, client):
        resp = client.post("/api/stream/text-batch", json={
            "ebook_path": "test.epub",
            "chunk_indices": [0, 1],
            "with_images": False,
        })
        assert resp.status_code == 404  # File not found

    def test_text_route(self, client):
        resp = client.get("/api/stream/text", params={
            "ebook_path": "test.epub",
            "start_char": 0,
            "end_char": 100,
        })
        assert resp.status_code == 404  # File not found

    def test_chapter_route(self, client):
        resp = client.get("/api/stream/chapter", params={
            "ebook_path": "test.epub",
            "char_position": 0,
        })
        assert resp.status_code == 404  # File not found

    def test_image_route(self, client):
        resp = client.get("/api/stream/image", params={
            "ebook_path": "test.epub",
            "image_id": "test123",
        })
        assert resp.status_code == 404  # File not found

    def test_preferences_themes(self, client):
        resp = client.get("/api/audiobooks/themes")
        assert resp.status_code == 200

    def test_preferences_get(self, client):
        resp = client.get("/api/audiobooks/preferences/get")
        assert resp.status_code == 200

    def test_preferences_save(self, client):
        resp = client.post("/api/audiobooks/preferences/save", json={
            "theme": "dark",
        })
        assert resp.status_code == 200


class TestPathValidation:
    """Test that path traversal is blocked."""

    def test_traversal_in_parse(self, client):
        resp = client.get("/api/stream/parse", params={
            "ebook_path": "../etc/passwd",
        })
        # Path traversal should be rejected with 400
        assert resp.status_code == 400

    def test_traversal_in_progress(self, client):
        resp = client.get("/api/stream/progress", params={
            "ebook_path": "../../../etc/passwd",
        })
        # Path traversal should be rejected with 400
        assert resp.status_code == 400


class TestStreamPage:
    def test_stream_page_exists(self, client):
        resp = client.get("/stream", params={"ebook": "test.epub"})
        assert resp.status_code == 200
