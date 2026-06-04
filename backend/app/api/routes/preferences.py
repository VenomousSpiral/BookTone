"""
API routes for user preferences and theme listing.

Endpoints:
    GET  /api/audiobooks/themes          - List available themes
    GET  /api/audiobooks/preferences/get - Get user preferences (SQLite)
    POST /api/audiobooks/preferences/save - Save user preferences (SQLite)
"""
from pathlib import Path
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.services.settings_service import get_preferences, save_preferences

router = APIRouter()

# Paths to static directories - use settings for reliable path resolution
THEMES_DIR = settings.BASE_DIR / "frontend" / "static" / "themes"


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
async def get_preferences_route():
    """Get user preferences from SQLite key-value store."""
    try:
        prefs = get_preferences()
        return JSONResponse(content=prefs)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read preferences: {e}")


@router.post("/preferences/save")
async def save_preferences_route(request: dict):
    """Save user preferences to SQLite key-value store."""
    try:
        save_preferences(request)
        return JSONResponse(content={"status": "ok"})
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save preferences: {e}")
