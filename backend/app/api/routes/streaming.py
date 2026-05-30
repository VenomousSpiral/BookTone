"""
Backward-compatible module: re-exports from split route modules.

This file exists so any code that imports from
`app.api.routes.streaming` still works.
The actual route definitions live in:
    text_routes.py, audio_routes.py, progress_routes.py
"""
from app.api.routes import text_routes
from app.api.routes import audio_routes
from app.api.routes import progress_routes

# For backward compatibility: expose the routers
router = None  # No single router anymore — use the individual modules
