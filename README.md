# 🎧 Web Audio Book Reader (Audiobook Server)

A self-hosted audiobook server that converts e-books (**EPUB**, **PDF**) into streamed audio via AI TTS endpoints. Features a cache-first generation pipeline, real-time text synchronization, playback progress tracking, M4B chapter-embedded audiobook downloads with full metadata, and a themeable web UI with 18+ color themes.

---

## ✨ Features

### Core
- **Multi-format support**: EPUB (via `ebooklib` + BeautifulSoup4) and PDF (via PyPDF2) parsing
- **AI-powered TTS**: OpenAI-compatible API — works with any self-hosted or cloud TTS server
- **Cache-first generation**: Audio chunks are cached by content hash on disk; re-generating already-cached segments is a no-op
- **Streaming architecture**: Separates text streaming (SSE) from audio chunk delivery for real-time playback
- **Playback progress tracking**: Resume reading position, bookmark chapters across sessions

### TTS Integration
- **Model agnostic**: Configure any OpenAI-compatible endpoint in `models.json`
- **Pre-configured providers** included by default:
  - **Kokoro** (8 voices) — localhost:8880
  - **higgs_v2** (35+ voices incl. character voices) — localhost:8999
  - **Voxcmp**, **Chatterbox-Turbo**, **LuxTTS**, **OmniVoice** and more
- **Configurable**: Per-model base URLs, API keys, voice lists — all editable via the web UI

### Audio & Playback
- **Audio formats**: OPUS (default), MP3, or M4B output
  - **OPUS** (`opus`) — Default format; efficient concatenation with `-c copy` for fast combined files
  - **MP3** (`mp3`) — Re-encoded via libmp3lame; maximum player compatibility
  - **M4B** (`m4b`) — MP4 container with embedded chapter metadata extracted from the ebook's TOC, using `ffprobe` to measure per-chunk durations and FFMETADATA1 for accurate chapter timestamps with interpolation support
- **Streaming playback**: Chunk-by-chunk audio delivery for instant start
- **Text synchronization**: Highlighted text segments synced with audio playback in real time
- **Browser-side cache**: `stream-cache.js` caches streamed audio chunks locally

### Download & Export (Job-Based System)
- **Background conversion jobs**: OPUS concat, MP3 re-encode, and M4B chapter embedding all run as background threads with real-time progress polling via `/download-progress/<job_id>`
- **M4B metadata extraction**: Automatically reads ebook chapters from parsed TOC, probes each audio chunk's duration via `ffprobe`, builds FFMETADATA1 text with interpolated timestamps, embeds into MP4 container for iTunes/Apple Books compatibility
- **Skip-if-ready**: If a combined file already exists in the requested format, returns immediately without re-conversion
- **Format selection**: Choose between `opus` (fastest), `mp3`, or `m4b` at download time

### File Management
- **Upload EPUB/PDF** files directly through the web UI (drag-and-drop or file picker)
- **Duplicate detection**: Choose to replace, copy, ignore, or be prompted on duplicate uploads
- **File browser**: Navigate directory structure, create folders, move/delete/copy/rename files
- **Temp cleanup**: Automated garbage collection of temporary upload artifacts

### Cache Management
- **Audio cache (CAS)**: Per-chunk content-hash-based caching under `storage/audiobooks/_stream_cache_{safe_stem}_{hash}/{model_name}/{voice_name}/` — chunks are identified by their 16-char MD5 hash so re-generating already-cached segments is a no-op
- **Parse cache**: Cached parsed book text (with optional images) stored as JSON in `storage/stream_cache/` to avoid re-parsing large books
- **Cache status & control**: View, generate, pause/resume background generation, and clear caches per-book or globally

### Persistence — SQLite Backend
All state now lives in a single SQLite database (`storage/app.db`) with WAL mode:
- **profiles** table — audiobook generation profiles (ebook_path + model_name + voice → status, progress, timestamps)
- **chapters** table — chapter boundaries per profile for granular progress tracking
- **bookmarks** table — unified bookmarks across all contexts ('progress' | 'profile')
- **settings_kv** table — key-value store for user preferences (replaces separate JSON config files)

### UI / UX
- **18+ themes**: amber, catppuccin, cyberpunk, dracula, emerald, forest, gruvbox, light, midnight, nord, ocean, sakura, secrets, synthwave, tokyo-night, vhs, vscode-dark — plus default and dark variants
- **Theme switching** at runtime via JSON theme definitions (no page reload)
- **Responsive design**: Works on desktop and mobile browsers
- **Mobile web app ready**: `apple-mobile-web-app-capable` meta tags for full-screen PWA-like experience

### User Preferences
- Per-user preference profiles: default model, voice, audio format, chunk size
- Theme selection persisted per user session (in SQLite)
- Upload duplicate behavior configurable globally or per-profile

---

## 📁 Project Structure

```
Web-Audio-Book-Reader/
├── backend/app/                         # FastAPI application package
│   ├── main.py                          # App entry: routers, middleware, template mounts
│   ├── api/routes/                      # API route modules (10 files)
│   │   ├── audio_routes.py              # Audio streaming & generation settings
│   │   ├── cache_routes.py              # Stream / parse cache management
│   │   ├── download_routes.py           # Download conversion jobs + progress polling
│   │   ├── files.py                     # E-book file management (upload, list, delete...)
│   │   ├── openai_routes.py             # TTS model / OpenAI-compatible endpoint config
│   │   ├── preferences.py               # User preference profiles + theme listing
│   │   ├── progress_routes.py           # Playback progress tracking & bookmarks
│   │   ├── streaming.py                 # Streaming text/audio endpoints (compat shims)
│   │   └── text_routes.py               # Text display, highlighting & synchronization
│   ├── core/
│   │   └── config.py                    # Settings via pydantic-settings (.env)
│   ├── models/
│   │   ├── streaming_models.py          # Pydantic request/response schemas (streaming)
│   │   ├── openai_config.py             # OpenAI model configuration schema
│   │   └── streaming.py                 # Streaming-specific models
│   ├── services/                        # Business logic layer
│   │   ├── cache_service.py             # Audio chunk caching logic & parse cache management
│   │   ├── database.py                  # SQLite connection + schema (profiles, bookmarks...)
│   │   ├── download_service.py          # Combined audio creation: opus/m4b/mp3 via ffmpeg
│   │   ├── ebook_parser.py              # EPUB/PDF parsing with ebooklib + BeautifulSoup4
│   │   ├── file_manager.py              # File system operations for ebooks/audiobooks
│   │   ├── generation_queue.py          # Background task queue for async generation
│   │   ├── job_manager.py               # Job lifecycle management (start, poll, complete)
│   │   ├── migrate_from_json.py         # Migration from legacy JSON storage to SQLite
│   │   ├── profile_manager.py           # Voice/profile management (per-user)
│   │   ├── settings_service.py          # App-level settings persistence (SQLite-backed)
│   │   ├── stream_audiobook_service.py  # Audiobook generation orchestrator + progress
│   │   └── stream_service.py            # Streaming TTS client (OpenAI-compatible)
│   └── utils/                           # Shared utilities
│       ├── path_utils.py                # Path sanitization & cache directory resolution
│       └── validators.py                # Input validation helpers
├── frontend/                            # Web UI
│   ├── static/js/                       # Vanilla JS modules (8 files, ES module pattern)
│   │   ├── app.js                       # Main entry point + tab navigation
│   │   ├── file-manager.js              # E-book upload/listing/drag-drop UI logic (45KB)
│   │   ├── stream-audio.js              # Audio player controls & playback
│   │   ├── stream-text.js               # Text display, highlighting & synchronization
│   │   ├── stream-state.js              # Shared streaming state store (reactive pattern)
│   │   ├── stream-cache.js              # Browser-side cache for streamed audio chunks
│   │   ├── theme-manager.js             # Theme switching from JSON definitions
│   │   └── stream.js                    # Streaming orchestration helpers
│   ├── static/themes/                   # 18 color themes as JSON files
│   ├── templates/index.html             # Main page template (tabbed interface)
│   └── templates/stream.html            # Streaming player page template
├── docker/                              # Docker Compose setup + backend Dockerfile
├── storage/                             # Runtime data (auto-created on startup)
│   ├── app.db                           # SQLite database: profiles, bookmarks, settings_kv
│   ├── audiobooks/                      # Audiobook cache directories (CAS layout below):
│   │                                  # _stream_cache_{safe_stem}_{md5_hash}/{model_name}/{voice_name}/
│   │                                  # Each {model}/{voice} dir contains individual chunk audio files
│   │                                  # named by their 16-char content hash + format extension (.opus/.m4a)
│   ├── ebooks/                          # Uploaded e-book files (.epub, .pdf, or .txt)
│   └── stream_cache/
├── models.json                          # TTS model configurations (pre-populated with providers)
├── backend/tests/                       # Backend unit/integration tests
│   ├── test_routes.py                   # API route testing (pytest + httpx)
│   ├── test_services.py                 # Service layer testing
│   ├── test_validators.py               # Validation logic tests
│   └── test_path_utils.py              # Path traversal protection tests
├── .env.example                         # Environment variable template
├── start.sh                             # Quick-start script (venv + pip install)
├── docker-start.sh                      # Docker quick-start alternative
├── up-server.sh                         # Server launcher helper
└── backend/requirements.txt             # Python dependencies (see below)
```

### Audio Cache Directory Layout

Audio chunks are stored using **Content-Addressable Storage (CAS)**:

```
storage/audiobooks/_stream_cache_{safe_stem}_{md5_hash}/
├── {model_name}/                          # e.g., "OminiVoice"
│   └── {voice_name}/                      # e.g., "Narrator-UK"
│       ├── a3f2b8c9d4e5f6a7.opus          # Audio chunk (16-char MD5 content hash)
│       ├── b1c2d3e4f5a6b7c8.opus          # Another audio chunk
│       └── ...                            # More chunks...
```

Each audio file is named after the **MD5 prefix** of its text content, so identical passages across chapters or books share a single cached file. The base cache directory name includes a URL-safe stem of the ebook title plus an MD5 hash of the actual file bytes for stability.

---

## 🚀 Quick Start

### Prerequisites
- **Python 3.10+** with pip
- A self-hosted or cloud TTS server compatible with the OpenAI API format
- **ffmpeg + ffprobe** — required for OPUS concat, MP3 re-encode, and M4B chapter metadata embedding

### Option 1: Native Install (Recommended)

```bash
# 1. Clone / navigate to project root
cd Web-Audio-Book-Reader

# 2. Run the quick-start script (creates venv, installs deps, creates .env)
bash start.sh

# 3. Start the server
cd backend && python run.py
# Or manually: uvicorn app.main:app --reload --host 0.0.0.0 --port 8984
```

### Option 2: Manual Install

```bash
cd backend
python -m venv venv
source venv/bin/activate     # On Windows: venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8984
```

### Option 3: Docker Compose (Recommended for production)

```bash
# Ensure .env exists in the docker/ directory, then:
docker compose -f docker/docker-compose.yml up --build -d
```

The server will be available at **http://localhost:8000** (Docker) or **http://localhost:8984** (native).

---

## ⚙️ Configuration

### Environment Variables (.env)

| Variable | Default | Description |
|----------|---------|-------------|
| `APP_NAME` | `"Audiobook Server"` | Display name for the application |
| `HOST` | `"0.0.0.0"` | Bind address |
| `PORT` | `8984` | HTTP server port (Docker overrides to 8000) |
| `OPENAI_API_KEY` | *(optional)* | Default API key for cloud TTS providers |
| `OPENAI_BASE_URL` | *(optional)* | Base URL for OpenAI-compatible endpoints |

### models.json — TTS Provider Configuration

The root-level `models.json` file defines all available TTS providers. Each entry specifies:

```json
{
  "OminiVoice": {
    "name": "OminiVoice",
    "api_model": "omnivore",
    "voices": ["Narrator-UK", "fiftyshades_anna", "Jessica", "Michael"],
    "base_url": "http://localhost:8869/v1"
  }
}
```

Providers can be added, edited, or removed via the web UI's **Models** tab — changes persist to `models.json`.

### Runtime Settings (via Web UI / Config)

| Setting | Default | Description |
|---------|---------|-------------|
| Audio Format | `opus` | Output format: `"opus"` (default), `"mp3"`, or `"m4b"` |
| Chunk Size | 500 chars | Characters per audio segment (smaller = better quality, more requests) |
| Upload Duplicate Behavior | `popup` | Action on duplicate upload: `"popup"`, `"replace"`, `"copy"`, `"ignore"` |

---

## 📡 API Reference

All endpoints are prefixed with `/api/`. The full API is documented via Swagger UI at **http://localhost:8984/docs** (or the equivalent Docker port).

### File Management (`/api/files`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/files/list?path=` | List files/folders in a directory |
| `POST` | `/api/files/upload` | Upload an EPUB/PDF/TXT file (multipart) |
| `POST` | `/api/files/upload-with-replace-cache` | Upload + replace existing book's cache |
| `POST` | `/api/files/upload-with-copy-cache` | Upload + copy cache from existing book |
| `POST` | `/api/files/upload-ignore-cache` | Upload without any cache interaction |
| `GET` | `/api/files/upload-check?name=&path=` | Check for duplicate filenames |
| `DELETE` | `/api/files/delete?file_path=` | Delete a file or folder |
| `POST` | `/api/files/move` | Move/rename files and folders |
| `POST` | `/api/files/create-directory` | Create a new directory |
| `POST` | `/api/files/create-file` | Create a new file with optional content |
| `DELETE` | `/api/files/cleanup-temp` | Clean up temporary upload artifacts |
| `GET` | `/api/files/download?file_path=` | Download a file from storage |

### TTS Models (`/api/openai`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/openai/models` | List all configured models |
| `POST` | `/api/openai/models` | Add a new model provider |
| `DELETE` | `/api/openai/models/{name}` | Remove a model |
| `GET` | `/api/openai/models/{name}/voices` | Get available voices for a model |

### Text Streaming (`/api/stream`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/stream/parse?ebook_path=&chapters=` | Parse an e-book into text segments |
| `GET` | `/api/stream/text?path=&start_char=&end_char=` | Get a specific text segment |
| `POST` | `/api/stream/text-batch` | Batch-request multiple text segments |
| `GET` | `/api/stream/image?path=&image_id=` | Extract embedded image from e-book |
| `GET` | `/api/stream/chapter?path=&pos=` | Find chapter at a given character position |

### Audio Streaming (`/api/stream`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/stream/audio` | Generate audio for a text segment (returns raw bytes) |
| `GET` | `/api/stream/settings` | Get current stream settings |
| `POST` | `/api/stream/settings` | Update default model/voice/format preferences |

### Download Jobs (`/api/stream`) — New Job-Based System

Background conversion with real-time progress polling. Supports **opus**, **mp3**, and **m4b** formats (M4B includes chapter metadata embedding).

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/stream/download-start?ebook_path=&model=&voice=&format_type=` | Start conversion job (`opus`, `mp3`, or `m4b`). Returns `job_id`. Skip-if-ready if output exists. |
| `GET` | `/api/stream/download-progress/<job_id>` | Poll real-time progress (percentage, status message) |
| `GET` | `/api/stream/download/<job_id>` | Download the completed combined file by job ID |

### Legacy Download Endpoints (backward-compatible shims)

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/stream/prepare-download?ebook_path=&model=&voice=` | Redirects to download-start with format=opus |
| `GET` | `/api/stream/download-status?id=` | Legacy status check for existing output files |
| `GET` | `/api/stream/download?id=&format=` | Stream the combined audiobook file (legacy) |
| `GET` | `/api/stream/download-source?ebook_path=` | Download original ebook source |

### Progress & Bookmarks (`/api/stream`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/stream/progress?ebook_path=` | Get playback progress for a book |
| `POST` | `/api/stream/progress` | Update reading position |
| `POST` | `/api/stream/bookmark` | Toggle a bookmark at current position |
| `GET` | `/api/stream/bookmarks?ebook_path=` | List all bookmarks for a book |
| `DELETE` | `/api/stream/progress?ebook_path=` | Clear progress data |

### Cache Management (`/api/stream`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/stream/cache-info?ebook_path=` | Get cache stats for a book (audio + parse) |
| `POST` | `/api/stream/generate-cache` | Start background audio generation |
| `POST` | `/api/stream/cache-pause` | Pause background generation |
| `POST` | `/api/stream/cache-resume` | Resume paused background generation |
| `GET` | `/api/stream/cache-status?ebook_path=` | Get current cache status per model/voice |
| `DELETE` | `/api/stream/clear-cache?ebook_path=` | Clear audio cache for a book (all models) |

### Parse Cache (`/api/stream`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/stream/parse-cache-status?ebook_path=` | Check parse cache status for a book |
| `GET` | `/api/stream/parse-cache-list` | List all books with parsed text cached |
| `DELETE` | `/api/stream/parse-cache?ebook_path=` | Clear parse cache for one book |
| `DELETE` | `/api/stream/parse-cache-all` | Clear all parse caches |

### Preferences (`/api/audiobooks`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/audiobooks/themes` | List available themes (JSON files) |
| `GET` | `/api/audiobooks/preferences/get` | Get current user preferences |
| `POST` | `/api/audiobooks/preferences/save` | Save/update user preferences |

---

## 🎨 Themes

The UI ships with **18 color themes**, swappable at runtime via the theme selector in the settings panel:

| Theme | Style |
|-------|-------|
| default | Light, standard |
| dark / vscode-dark | Dark mode variants |
| dracula | Popular dark purple/pink palette |
| nord | Cool arctic blues/grays |
| gruvbox | Warm retro terminal |
| catppuccin | Soft pastels (mocha/macchiato/frappe) |
| synthwave / cyberpunk / vhs | Neon-retro aesthetics |
| tokyo-night | VS Code-inspired night theme |
| emerald / forest | Green nature palettes |
| sakura | Pink cherry blossom |
| ocean | Deep blue-teal |
| midnight | Very dark blue-black |
| amber / light | Warm single-tone variants |

Themes are defined as JSON files in `frontend/static/themes/`. Add a new theme by creating a file like `mytheme.json` and it will appear automatically.

---

## 📥 Download Formats Explained

### OPUS (default)
Fastest conversion — uses ffmpeg's `-c copy` to concatenate opus chunks without re-encoding. Minimal CPU, excellent quality-to-size ratio. Best for local/server-side use.

```bash
# Under the hood:
ffmpeg -f concat -i filelist.txt -c copy combined.opus
```

### MP3
Re-encodes all chunks through libmp3lame for maximum player compatibility (VLC, Windows Media Player, older car stereos). Slower and larger files than OPUS.

```bash
# Under the hood:
ffmpeg -i input.opus -codec:a libmp3lame -qscale:a 2 combined.mp3
```

### M4B (Apple Books / iTunes compatible)
Creates an MP4 container with embedded chapter metadata extracted from the ebook's table of contents. Uses `ffprobe` to probe each audio chunk's duration, then builds FFMETADATA1 text with interpolated timestamps for accurate chapter navigation. Compatible with Apple Books, VLC, and most modern audiobook players.

```bash
# Under the hood:
# 1. Concat opus chunks → intermediate.mp4 (copy mode)
ffmpeg -f concat -i filelist.txt -c copy intermediate.mp4
# 2. Probe durations for chapter timestamps via ffprobe
ffprobe -show_entries format=duration <chunk>
# 3. Build FFMETADATA1 with interpolated start/end times per chapter
# 4. Re-encode with metadata embedded:
ffmpeg -i intermediate.mp4 -i metadata.txt -map_metadata 1 -c copy combined.m4b
```

---

## 🐳 Docker Deployment

### Quick Start (Docker Compose)

```bash
cd docker/
cp .env.example .env   # Optional: customize settings
docker compose up --build -d
```

The Docker setup:
- Builds from `docker/Dockerfile.backend` (Python 3.12 slim, installs system deps + Python packages)
- Uses `network_mode: host` so the container can reach TTS servers on localhost or local network
- Exposes port **8000** on the host
- Mounts three volumes for persistent data:
  - `storage/` — all uploaded ebooks, cached audiobooks, SQLite database (app.db) survive container restarts
  - `models.json` — TTS provider configuration persists across deploys

### Docker Environment Variables

Override defaults by setting env vars in your `.env` file or directly on the service. The default port for Docker is **8000**.

---

## 🔒 Security Notes

- **Path sanitization**: All user-supplied paths are validated against directory traversal attacks via `utils/path_utils.py`
- **CORS**: Broad defaults (`allow_origins=["*"]`) — restrict in production by setting the appropriate CORS headers or reverse proxy configuration
- **GZip compression** enabled for responses ≥1 KB

For production use, consider:
- Adding authentication (reverse proxy with basic auth / JWT)
- Restricting CORS origins to your domain
- Using HTTPS via a reverse proxy (nginx, Caddy)

---

## 🧪 Testing

```bash
# Run all backend tests
cd backend && pytest -v

# Run specific test file
pytest tests/test_routes.py -v
```

Test files:
- `backend/tests/test_routes.py` — API endpoint testing with pytest + httpx
- `backend/tests/test_services.py` — Service layer unit tests
- `backend/tests/test_validators.py` — Input validation coverage
- `backend/tests/test_path_utils.py` — Path traversal protection tests

---

## 📦 Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| fastapi | 0.115.0 | Web framework |
| uvicorn[standard] | 0.30.0 | ASGI server |
| pydantic | 2.9.0 | Data validation |
| pydantic-settings | 2.5.0 | Environment / .env config |
| openai | 1.51.0 | OpenAI-compatible TTS client |
| ebooklib | 0.18 | EPUB parsing |
| beautifulsoup4 | 4.12.3 | HTML/XML content extraction |
| PyPDF2 | 3.0.1 | PDF text extraction |
| pydub | 0.25.1 | Audio format handling (MP3/OPUS) |

**System dependencies**: `ffmpeg` + `ffprobe` — required for audio concat, MP3 re-encode, and M4B chapter metadata embedding. Install via your package manager (`apt install ffmpeg`, `brew install ffmpeg`).

---

## 🔧 Development

### Running with hot-reload

```bash
cd backend && uvicorn app.main:app --reload --host 0.0.0.0 --port 8984
```

Frontend JS changes are picked up automatically (plain static files, no build step). Backend changes require the `--reload` flag.

### Adding a new TTS provider

1. Add an entry to `models.json`:
   ```json
   "MyProvider": {
     "name": "My Provider",
     "api_model": "tts-1",
     "voices": ["voice_a"],
     "base_url": "http://localhost:9000/v1"
   }
   ```
2. Or use the web UI **Models** tab to add/edit/remove providers dynamically.

### Adding a new theme

Create `frontend/static/themes/mytheme.json`:
```json
{
  "name": "My Theme",
  "background": "#0d1117",
  "foreground": "#c9d1d9"
}
```

---

## 📝 License

This project is open source. See the repository for license details.
