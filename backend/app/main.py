"""
FastAPI application entry point.
"""
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Query
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse, FileResponse
from pathlib import Path
import inspect
import logging

from app.api.routes import (
    files,
    openai_routes,
    text_routes,
    audio_routes,
    progress_routes,
    cache_routes,
    download_routes,
    preferences,
)
from app.services.database import init_db
from app.services.migrate_from_json import migrate_if_needed

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
#  Lifespan: DB init + migration on startup                                   #
# --------------------------------------------------------------------------- #


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize schema (idempotent).
    init_db()

    # Run one-time migration if needed.
    migrate_if_needed()

    yield

    # Cleanup on shutdown — stop service-owned workers.
    try:
        from app.services.stream_audiobook_service import stream_audiobook_service
        stream_audiobook_service.close()
    except Exception:
        logger.exception("Failed to shut down stream audiobook workers")


app = FastAPI(
    title="Audio Book Reader",
    description="Self-hosted audiobook server with streaming and cache-first generation",
    version="0.1.0",
    lifespan=lifespan,
)

# Middleware
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files
_project_root = Path(__file__).resolve().parent.parent.parent
static_path = _project_root / "frontend" / "static"
app.mount("/static", StaticFiles(directory=str(static_path)), name="static")

# Templates
templates_path = _project_root / "frontend" / "templates"
templates = Jinja2Templates(directory=str(templates_path))

def _template_response(request: Request, name: str, context: dict):
    """Render a template across supported Starlette TemplateResponse APIs."""
    parameters = inspect.signature(templates.TemplateResponse).parameters
    if "request" in parameters:
        return templates.TemplateResponse(
            request=request,
            name=name,
            context=context,
        )
    return templates.TemplateResponse(
        name,
        {"request": request, **context},
    )


# ---- Route registration ----

app.include_router(files.router, prefix="/api/files", tags=["files"])
app.include_router(openai_routes.router, prefix="/api/openai", tags=["openai"])
app.include_router(text_routes.router, prefix="/api/stream", tags=["text"])
app.include_router(audio_routes.router, prefix="/api/stream", tags=["audio"])
app.include_router(progress_routes.router, prefix="/api/stream", tags=["progress"])
app.include_router(cache_routes.router, prefix="/api/stream", tags=["stream-cache"])
app.include_router(download_routes.router, prefix="/api/stream", tags=["stream-download"])
app.include_router(preferences.router, prefix="/api/audiobooks", tags=["preferences"])


# ---- Page routes ----

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    """Serve the main web interface"""
    return _template_response(request, "index.html", {})


@app.get("/stream", response_class=HTMLResponse)
async def stream_page(request: Request, ebook: str = Query(...)):
    """Serve the streaming player interface"""
    return _template_response(request, "stream.html", {"ebook_path": ebook})


@app.get("/test-performance", response_class=HTMLResponse)
async def test_performance_page(request: Request):
    """Serve the performance test page"""
    test_file = _project_root / "frontend" / "test-performance" / "index.html"
    return FileResponse(test_file)


@app.get("/health")
async def health_check():
    return {"status": "healthy"}
