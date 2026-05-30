"""
API routes for user preferences and theme listing.

Endpoints:
    GET  /api/audiobooks/themes          - List available themes
    GET  /api/audiobooks/preferences/get - Get user preferences
    POST /api/audiobooks/preferences/save - Save user preferences
"""
import json
from pathlib import Path
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from app.core.config import settings

router = APIRouter()

# Paths to static directories - use settings for reliable path resolution
THEMES_DIR = settings.BASE_DIR / "frontend" / "static" / "themes"
STORAGE_DIR = settings.STORAGE_DIR
PREFERENCES_FILE = STORAGE_DIR / "user_preferences.json"


@router.get("/themes")
async def list_themes():
    """List available theme files from the themes directory."""
    if not THEMES_DIR.exists():
        return JSONResponse(content=[])

    themes = []
    for theme_file in sorted(THEMES_DIR.glob("*.json")):
        theme_id = theme_file.stem
        themes.append({"id": theme_id, "name": theme_id})

    return JSONResponse(content=themes)


@router.get("/preferences/get")
async def get_preferences():
    """Get user preferences from storage."""
    if not PREFERENCES_FILE.exists():
        return JSONResponse(content={})

    try:
        with open(PREFERENCES_FILE, "r") as f:
            prefs = json.load(f)
        return JSONResponse(content=prefs)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read preferences: {e}")


@router.post("/preferences/save")
async def save_preferences(request: dict):
    """Save user preferences to storage."""
    try:
        STORAGE_DIR.mkdir(parents=True, exist_ok=True)
        existing = {}
        if PREFERENCES_FILE.exists():
            try:
                with open(PREFERENCES_FILE, "r") as f:
                    existing = json.load(f)
            except Exception:
                existing = {}

        existing.update(request)

        with open(PREFERENCES_FILE, "w") as f:
            json.dump(existing, f, indent=2)

        return JSONResponse(content={"status": "ok"})
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save preferences: {e}")
