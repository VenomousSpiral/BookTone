from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pathlib import Path

from app.api.routes import files, audiobooks, openai_routes, streaming
from app.core.config import settings

app = FastAPI(
    title="Audiobook Server",
    description="Upload ebooks and convert them to audiobooks with LRC support",
    version="0.1.0"
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files
static_path = Path(__file__).parent.parent.parent / "frontend" / "static"
app.mount("/static", StaticFiles(directory=str(static_path)), name="static")

# Templates
templates_path = Path(__file__).parent.parent.parent / "frontend" / "templates"
templates = Jinja2Templates(directory=str(templates_path))

# Include routers
app.include_router(files.router, prefix="/api/files", tags=["files"])
app.include_router(audiobooks.router, prefix="/api/audiobooks", tags=["audiobooks"])
app.include_router(openai_routes.router, prefix="/api/openai", tags=["openai"])
app.include_router(streaming.router, prefix="/api/stream", tags=["streaming"])

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    """Serve the main web interface"""
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/stream", response_class=HTMLResponse)
async def stream_page(request: Request, ebook: str):
    """Serve the streaming player interface"""
    return templates.TemplateResponse("stream.html", {"request": request, "ebook_path": ebook})

@app.get("/health")
async def health_check():
    return {"status": "healthy"}
