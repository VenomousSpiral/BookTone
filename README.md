# Audiobook Server

A complete self-hosted audiobook server that converts ebooks (EPUB, TXT, HTML) into audiobooks using TTS (Text-to-Speech) with synchronized lyrics (LRC format), bookmarks, and position tracking. It does not include a TTS engine, you will need an open ai api key or to self host your own. 


## ⚠️ Project Status

This is a personal project in active development and may contain bugs. It is intended for users who are comfortable with self-hosting and independent troubleshooting. Not recommended for production environments.
It has been mostly vibe coded


## Known Working TTS SERVERS
https://github.com/remsky/Kokoro-FastAPI

Other recomended Models:
 - VoxCPM
 - OmniVoice
 - Chatterbox Turbo

## 🌟 Features

### Core Functionality
- **Ebook to Audiobook Conversion**: Convert EPUB, TXT, and HTML files to MP3 audiobooks
- **Self-Hosted TTS**: Works with OpenAI-compatible TTS servers (local or cloud)
- **LRC Synchronization**: Sentence-by-sentence synchronized text display
- **Multi-Model Support**: Configure multiple TTS models with different voices
- **Background Generation**: Process audiobooks asynchronously with progress tracking
- **File Management**: Upload, organize, move, and delete ebooks in folders

### User Interface
- **Dark Mode**: Modern dark-themed interface optimized for reading
- **Full-Screen Player**: Distraction-free audiobook playback
- **Smart Scrolling**: Auto-follows current text, with manual scroll override
- **Responsive Design**: Works on desktop and mobile devices

### Playback Features
- **Bookmarking**: Swipe left on any line to bookmark (persists across sessions)
- **Position Saving**: Auto-saves playback position every 3 seconds
- **Jump to Current**: Always-visible button to return to currently playing line
- **Click to Seek**: Tap any line to jump to that position in audio
- **Progress Tracking**: Visual progress indicator and time display

### Themes
- **Dark Mode**: Modern dark-themed interface optimized for reading
- **Multiple Themes**: Built-in themes (Dark, VS Code Dark, Monokai Secrets)
- **Change Themes**: Select from the Settings modal in the player (⚙️ button)
- **Persisted**: Theme choice saves to server and syncs across devices
- **Easy to Add**: Drop a JSON file into `frontend/static/themes/` — see below

### Data Persistence
- All bookmarks saved to disk
- Playback positions persist across server restarts
- Works across multiple devices
- Automatic database backup

## 📋 Requirements

- Python 3.12+
- FFmpeg (for audio processing)
- Self-hosted TTS server (OpenAI-compatible API)
- 2GB+ RAM recommended
- Storage space for audiobooks

## 🚀 Quick Start

### Option 1: Docker (Recommended)

```bash
./docker-start.sh
```

Then open http://localhost:8000

### Option 2: Manual Setup

```bash
# Install dependencies
python3 -m venv venv
source venv/bin/activate
pip install -r backend/requirements.txt

# Install FFmpeg
sudo apt-get install ffmpeg  # Ubuntu/Debian
brew install ffmpeg           # macOS

# Start server
./up-server.sh
```

Then open http://localhost:8000

## 📖 Complete Setup Guide

### 1. Installation

**Prerequisites:**
- Python 3.12+
- pip package manager
- FFmpeg

**Install FFmpeg:**

Ubuntu/Debian:
```bash
sudo apt-get update
sudo apt-get install ffmpeg
```

macOS:
```bash
brew install ffmpeg
```

Windows: Download from https://ffmpeg.org/download.html

**Setup Project:**
```bash
cd /home/eli/AI-projects/auido_book_server
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
cd backend
pip install -r requirements.txt
cd ..
```

### 2. Configure TTS Server

Edit `models.json` in the project root:

```json
{
  "local-tts-server": {
    "name": "My Local TTS Server",
    "api_model": "tts-1",
    "voices": ["am_onyx", "voice2"],
    "base_url": "http://localhost:8880/v1",
    "api_key": null
  },
  "another-server": {
    "name": "Another TTS Server",
    "api_model": "tts-1",
    "voices": ["alloy", "echo"],
    "base_url": "http://localhost:8881/v1",
    "api_key": null
  }
}
```

**Configuration Options:**
- `name`: Display name shown in UI (must be unique)
- `api_model`: Actual model name sent to TTS API (e.g., "tts-1")
- `voices`: Comma-separated list of available voices
- `base_url`: Your TTS server URL (format: `http://host:port/v1`)
- `api_key`: Optional API key (null for self-hosted servers)

**Multiple Models with Same API:**
You can now have multiple models that all use the same API model name (e.g., two different servers both using "tts-1"). The display `name` is the unique identifier, while `api_model` is what gets sent to the TTS API.

### 3. Start the Server

**Option A: Using the run script**
```bash
cd backend
python run.py
```

**Option B: Using uvicorn directly**
```bash
cd backend
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

The server will be available at http://localhost:8000

## 📱 Usage Guide

### First-Time Setup

1. **Add Your TTS Server** (if not in models.json):
   - Open http://localhost:8000
   - Click the **Models** tab
   - Click **Add Model**
   - Fill in:
     - Display Name: `My Local TTS Server` (shown in UI)
     - API Model Name: `tts-1` (sent to TTS API)
     - Voices: `voice1, voice2, voice3` (comma-separated)
     - Base URL: `http://localhost:8880/v1`
     - API Key: Leave empty for self-hosted
   - Click **Add Model**
   
   **Note:** You can add multiple models that use the same API model name. For example, two different TTS servers both using "tts-1" won't conflict because they have different display names.

2. **Upload an Ebook**:
   - Click the **Files** tab
   - Click **📤 Upload Ebook** button
   - Select EPUB, TXT, or HTML file
   - File appears in the list

3. **Generate Audiobook**:
   - Find your uploaded ebook
   - Click **🎵 Generate Audio**
   - Select model and voice
   - (Optional) Add instructions like "Speak cheerfully"
   - Click **Generate**

4. **Monitor Progress**:
   - Switch to **Audiobooks** tab
   - Watch real-time progress bar
   - Generation may take several minutes

5. **Play Audiobook**:
   - Once status shows **completed**
   - Click **▶️ Play** button
   - Full-screen player opens

### Player Features

**Navigation:**
- Click any text line to jump to that position in audio
- Use standard audio controls (play/pause/seek/volume)
- Click **⬇ Jump to Current** button to return to playing line
- Free scroll while playing - auto-scroll resumes when current line is visible

**Bookmarking:**
- **Mobile**: Swipe left on any line to bookmark
- **Desktop**: Click and drag left on a line
- Bookmarked lines show gold star (★) and gold left border
- Click **★ View Bookmarks** to see all bookmarks
- Click on bookmark to jump to it
- Swipe left again to remove bookmark

**Position Saving:**
- Position auto-saves every 3 seconds while playing
- Saves when pausing or closing player
- Automatically resumes from last position on next play
- Works across devices and server restarts

### File Management

**Create Folders:**
- Click **📁 New Folder**
- Enter folder name
- Click Create

**Move Files:**
- Click **↔️ Move** on any file/folder
- Enter new path
- Click Move

**Delete:**
- Click **🗑️ Delete** on any file/folder
- Confirm deletion

### Managing Models

**Add Model:**
- Models tab → Add Model
- Configure name, voices, URL, API key

**Delete Model:**
- Click **🗑️ Delete** next to model
- Confirm deletion

## 🐳 Docker Deployment

Complete Docker setup with backend and TTS server.

### Quick Start

```bash
# Navigate to docker directory
cd docker

# Copy environment template
cp .env.example .env

# Start all services
docker-compose up -d

# View logs
docker-compose logs -f

# Stop services
docker-compose down
```

### What's Included

- **audiobook-server**: FastAPI backend with Python 3.12
- **tts-server**: Kokoro TTS or compatible (OpenAI-compatible API)
- **Persistent volumes**: Storage for ebooks, audiobooks, LRC files
- **Network isolation**: Services communicate on private network
- **Health checks**: Automatic service monitoring

### Configuration

Edit `docker/.env`:
```env
HOST=0.0.0.0
PORT=8000
OPENAI_BASE_URL=http://tts-server:8880/v1
TZ=America/New_York
```

Edit `docker/docker-compose.yml` for advanced configuration:
- GPU support (uncomment nvidia-docker sections)
- External TTS server
- Resource limits
- Custom networks

### Access

- Web Interface: http://localhost:8000
- TTS API: http://localhost:8880/v1

### Data Persistence

All data in `storage/` directory persists:
- `storage/ebooks/` - Uploaded ebooks
- `storage/audiobooks/` - Generated MP3 files
- `storage/lrc/` - Synchronized lyrics
- `storage/audiobooks_db.json` - Metadata, bookmarks, positions

### Backup

```bash
# Backup storage directory
tar -czf audiobook-backup-$(date +%Y%m%d).tar.gz storage/

# Restore
tar -xzf audiobook-backup-YYYYMMDD.tar.gz
```

### Using External TTS Server

If you have a TTS server outside Docker:

1. Edit `docker-compose.yml`:
```yaml
environment:
  - OPENAI_BASE_URL=http://host.docker.internal:8880/v1
# Remove depends_on: tts-server
```

2. Comment out or remove the `tts-server` service

3. Restart: `docker-compose up -d`

### GPU Support

For NVIDIA GPUs:

1. Install nvidia-docker
2. Uncomment in `docker-compose.yml`:
```yaml
tts-server:
  deploy:
    resources:
      reservations:
        devices:
          - driver: nvidia
            count: 1
            capabilities: [gpu]
```
3. Restart: `docker-compose down && docker-compose up -d`

See `docker/README.md` for complete Docker documentation.

## 🔧 Configuration

### Directory Structure

```
audiobook_server/
├── backend/
│   ├── app/
│   │   ├── api/routes/          # API endpoints
│   │   ├── core/                # Configuration
│   │   ├── models/              # Data models
│   │   └── services/            # Business logic
│   ├── requirements.txt
│   └── run.py
├── frontend/
│   ├── static/
│   │   ├── css/styles.css       # Dark mode theme
│   │   └── js/                  # Player, file manager
│   └── templates/index.html
├── storage/
│   ├── ebooks/                  # Uploaded ebooks
│   ├── audiobooks/              # Generated MP3s
│   ├── lrc/                     # Synchronized lyrics
│   └── audiobooks_db.json       # Metadata & bookmarks
├── docker/                      # Docker setup
│   ├── docker-compose.yml
│   ├── Dockerfile.backend
│   └── README.md
├── models.json                  # TTS model configs
├── docker-start.sh              # Quick Docker start
└── docker-stop.sh               # Quick Docker stop
```

### Environment Variables (.env)

```env
# Server settings
HOST=0.0.0.0
PORT=8000              # Change to use different port (e.g., 8001)

# TTS configuration (optional - configure models in UI Models tab)
OPENAI_BASE_URL=http://localhost:8880/v1
OPENAI_API_KEY=not-needed-for-self-hosted

# Optional
TZ=America/New_York
DEBUG=True
```

**Port Configuration:**
- Set `PORT=8001` (or any available port) in `.env` to change the server port
- Works for both manual and Docker deployments
- Docker users: Edit `docker/.env` for containerized setup

### App Settings (backend/app/core/config.py)

- Storage paths
- Default model/voice
- Chunk size (default: sentence-based, 5 words minimum, 21 chars minimum)
- API timeouts
- TTS delay (0.5s between chunks to prevent CUDA errors)

### Adding Themes

Themes are defined as simple JSON files in `frontend/static/themes/`. Each theme maps CSS custom properties to color values.

**Step 1:** Create a new JSON file, e.g. `frontend/static/themes/ocean.json`:

```json
{
    "name": "Ocean",
    "description": "Deep blue ocean theme",
    "variables": {
        "--bg-primary": "#0a192f",
        "--bg-secondary": "#112240",
        "--bg-tertiary": "#233554",
        "--text-primary": "#ccd6f6",
        "--text-secondary": "#8892b0",
        "--accent": "#64ffda",
        "--accent-hover": "#4fd1b5",
        "--border": "#233554",
        "--success": "#64ffda",
        "--error": "#ff6b6b",
        "--warning": "#ffd166",
        "--primary-color": "#64ffda",
        "--primary-color-dark": "#4fd1b5",
        "--primary-color-light": "rgba(100, 255, 218, 0.1)",
        "--modal-overlay": "rgba(0, 0, 0, 0.8)",
        "--image-overlay": "rgba(0, 0, 0, 0.9)",
        "--toast-bg": "rgba(0, 0, 0, 0.8)",
        "--toast-text": "#ccd6f6",
        "--scrollbar-track": "#233554",
        "--scrollbar-thumb": "#495670",
        "--hover-bg": "#1d3353",
        "--shadow-color": "rgba(0, 0, 0, 0.4)",
        "--info-toast-bg": "rgba(204, 214, 246, 0.95)",
        "--info-toast-text": "#0a192f",
        "--success-toast-bg": "rgba(100, 255, 218, 0.95)",
        "--error-toast-bg": "rgba(255, 107, 107, 0.95)"
    }
}
```

**Step 2:** Register the theme in `frontend/static/js/theme-manager.js`. Add the filename to the `themeNames` array in `init()`:

```js
const themeNames = ['default', 'vscode-dark', 'secrets', 'ocean'];
```

That's it — the theme will appear in the Settings dropdown on both the main page and streaming page.

**Available CSS Variables:**

| Variable | Purpose |
|---|---|
| `--bg-primary` | Body / main background |
| `--bg-secondary` | Header, panel backgrounds |
| `--bg-tertiary` | Cards, inputs, nav backgrounds |
| `--text-primary` | Main text color |
| `--text-secondary` | Muted / descriptive text |
| `--accent` | Primary interactive color (buttons, active tabs) |
| `--accent-hover` | Darker accent for hover states |
| `--border` | Card / button borders |
| `--success` | Completed / positive status |
| `--error` | Danger / failed status |
| `--warning` | Pending / warning status |
| `--primary-color` | Streaming player accent (alias of accent) |
| `--primary-color-dark` | Streaming player hover accent |
| `--primary-color-light` | Subtle highlight background |
| `--modal-overlay` | Modal backdrop opacity |
| `--image-overlay` | Fullscreen image modal backdrop |
| `--toast-bg` | Toast notification background |
| `--toast-text` | Toast notification text |
| `--scrollbar-track` | Scrollbar track |
| `--scrollbar-thumb` | Scrollbar thumb |
| `--hover-bg` | Card hover background |
| `--shadow-color` | Drop shadow color |
| `--info-toast-bg` | Info toast background |
| `--info-toast-text` | Info toast text |
| `--success-toast-bg` | Success toast background |
| `--error-toast-bg` | Error toast background |

## 🛠️ Troubleshooting

### Server Won't Start

```bash
# Check if port 8000 is in use
lsof -i :8000

# Try different port
cd backend
uvicorn app.main:app --reload --host 0.0.0.0 --port 8080
```

### Import Errors

```bash
# Ensure virtual environment is activated
source venv/bin/activate

# Reinstall dependencies
cd backend
pip install --upgrade -r requirements.txt
```

### FFmpeg Not Found

```bash
# Verify installation
ffmpeg -version

# Install if missing
sudo apt-get install ffmpeg  # Ubuntu/Debian
brew install ffmpeg           # macOS
```

### Audiobook Generation Fails

**Check TTS Server:**
```bash
curl -X POST http://localhost:8880/v1/audio/speech \
  -H "Content-Type: application/json" \
  -d '{"model":"tts-1","voice":"am_onyx","input":"Test"}' \
  --output test.mp3
```

If test.mp3 is created with audio, TTS server is working.

**Common Issues:**
- **GPU out of memory**: Delay between chunks already set to 0.5s (prevents CUDA errors)
- **Model not found**: Check model name in models.json
- **Connection refused**: Verify TTS server is running and URL is correct
- **Wrong format**: TTS server must return MP3

### CUDA Errors (GPU)

If TTS server fails with CUDA errors:

```bash
# Restart TTS server
docker restart tts-server

# Or if running directly
kill <pid>
# Then restart your TTS server

# Clear GPU memory
sudo rmmod nvidia_uvm
sudo modprobe nvidia_uvm
```

The audiobook server already has 0.5-second delay between chunks to prevent GPU overload.

### Position Not Saving

**Check Browser Console (F12):**
- Look for "Saving position:" logs
- Check for API errors

**Verify Database:**
```bash
cat storage/audiobooks_db.json | jq '.'
```

Should show last_position and bookmarks for each audiobook.

### Bookmarks Not Working

**Check Browser Console (F12):**
- Look for "Touch start/end" logs
- Look for "Swipe left detected!" message
- Swipe must be > 30px horizontal, < 50px vertical, < 500ms

**Test on Desktop:**
- Click and drag left on a line
- Should see console logs

### File Upload Fails

```bash
# Check storage permissions
ls -la storage/

# Fix if needed
chmod -R 755 storage/
```

## 📊 API Reference

### Files Endpoints

- `GET /api/files/list?path=` - List files in directory
- `POST /api/files/upload` - Upload ebook (multipart/form-data)
- `POST /api/files/create-directory` - Create folder
- `POST /api/files/move` - Move file/folder (JSON: {source, destination})
- `DELETE /api/files/delete?file_path=` - Delete file/folder
- `GET /api/files/download?file_path=` - Download file

### Audiobooks Endpoints

- `GET /api/audiobooks/list` - List all audiobooks with status
- `GET /api/audiobooks/{id}` - Get audiobook metadata
- `POST /api/audiobooks/generate` - Generate audiobook (JSON: {file_path, model, voice, instructions})
- `GET /api/audiobooks/{id}/audio` - Stream/download audio file
- `GET /api/audiobooks/{id}/lrc` - Get LRC synchronized lyrics
- `POST /api/audiobooks/{id}/position` - Update position (JSON: {position})
- `POST /api/audiobooks/{id}/bookmark` - Toggle bookmark (JSON: {chunk_index})
- `GET /api/audiobooks/{id}/bookmarks` - Get all bookmarks
- `POST /api/audiobooks/{id}/pause` - Pause generation
- `POST /api/audiobooks/{id}/resume` - Resume generation
- `DELETE /api/audiobooks/{id}` - Delete audiobook

### Models Endpoints

- `GET /api/openai/models` - List configured TTS models
- `POST /api/openai/models` - Add/update model (JSON: {name, voices, base_url, api_key})
- `DELETE /api/openai/models/{name}` - Delete model
- `GET /api/openai/models/{name}/voices` - Get model voices

### Health Check

- `GET /health` - Server health status

### Interactive API Docs

Visit http://localhost:8000/docs for Swagger UI with all endpoints documented.

## 🚦 Performance

### Resource Usage

- **CPU**: Low when idle, moderate during generation
- **RAM**: ~200MB base + ~50MB per concurrent generation
- **Storage**: ~1MB per minute of audio (MP3 format)
- **Network**: Minimal (local TTS server recommended)

### Optimization Tips

1. **Chunk Size**: Sentence-based chunking works well (5 words min, 21 chars min)
2. **TTS Delay**: 0.5s between chunks prevents GPU overload
3. **Position Save**: 3 seconds balances responsiveness and I/O
4. **Audio Format**: MP3 provides good compression/quality

### Testing API Performance

```bash
# API health check
curl http://localhost:8000/health

# List files
curl http://localhost:8000/api/files/list

# List audiobooks
curl http://localhost:8000/api/audiobooks/list

# TTS server test
curl -X POST http://localhost:8880/v1/audio/speech \
  -H "Content-Type: application/json" \
  -d '{"model":"tts-1","voice":"am_onyx","input":"Performance test"}' \
  --output perf-test.mp3
```

## 🔐 Security Notes

⚠️ **For Development/Personal Use:**
- Runs on 0.0.0.0:8000 (accessible on local network)
- No authentication/authorization
- File uploads unrestricted
- API keys stored in plaintext

**For Production:**
- Add reverse proxy with authentication (nginx/Traefik)
- Enable HTTPS
- Implement rate limiting
- Validate file types and sizes
- Sanitize file paths
- Use environment variables for secrets
- Add CSRF protection
- Restrict network access with firewall

## 📱 Mobile Support

### Gestures
- **Swipe Left**: Bookmark line (> 30px horizontal, < 50px vertical, < 500ms)
- **Tap Line**: Jump to position
- **Scroll**: Free scrolling (auto-scroll resumes when current line visible)

### Tips
- Use landscape mode for better reading
- Bookmarks and positions sync across all devices
- Access from mobile: `http://YOUR_SERVER_IP:8000`

### Finding Server IP
```bash
# Linux/macOS
ip addr show | grep inet

# Or check server logs when starting
# Shows: Uvicorn running on http://0.0.0.0:8000
```

## 🤝 Contributing

This is a personal project, but suggestions welcome!

### Development

```bash
# Backend (with hot reload)
cd backend
source ../venv/bin/activate
python run.py

# Frontend
# Edit files in frontend/static and frontend/templates
# FastAPI serves them with hot reload
```

### Project Dependencies

**Backend:**
- FastAPI 0.115.0 - Web framework
- Uvicorn - ASGI server
- Pydantic - Data validation
- OpenAI 1.51.0 - TTS API client
- ebooklib 0.18 - EPUB parsing
- BeautifulSoup4 - HTML parsing
- pydub - Audio processing
- FFmpeg - Audio encoding
- Jinja2 - Templates
- python-multipart - File uploads

**Frontend:**
- Vanilla JavaScript (no frameworks)
- CSS3 with dark theme
- Touch gesture detection

## 🎯 Known Limitations

- Single user (no authentication)
- No audio streaming (full file loads)
- No playlist/queue feature
- No full-text search in ebooks
- No adjustable playback speed in UI (use browser controls)
- LRC timestamps are approximated (not word-level)

## 💡 Future Enhancement Ideas

- Multiple user support with authentication
- Playlist management
- Adjustable chunk size in UI
- Different voice per character (dialogue detection)
- Playback speed control in player
- Chapter detection and navigation
- Export bookmarks feature
- Share positions/bookmarks between users
- Progressive audio loading
- Download audiobook as file
- Sleep timer
- Text search in ebooks
- PDF support
- Batch processing
- Voice cloning integration

## 📜 License

Personal project - use as you wish!

## 🙏 Acknowledgments

- FastAPI for the excellent web framework
- Pydantic for data validation
- OpenAI for the TTS API standard
- FFmpeg for audio processing
- All open-source TTS projects (Kokoro, Coqui, etc.)

## 📞 Support

For issues or questions:

1. **Check logs**:
   - Browser console (F12) for frontend errors
   - Terminal output for backend errors
   - TTS server logs for API issues

2. **Verify configuration**:
   - models.json has correct TTS server URL
   - TTS server is running and accessible
   - Storage directories have correct permissions

3. **Test independently**:
   - Test TTS server with curl
   - Check API docs at http://localhost:8000/docs
   - Verify FFmpeg installation

4. **Common solutions**:
   - Restart server: `./up-server.sh`
   - Restart Docker: `./docker-start.sh`
   - Clear browser cache
   - Check network connectivity

## 🎉 Quick Reference

### One-Line Commands

```bash
# Start manually
./up-server.sh

# Start with Docker
./docker-start.sh

# Stop Docker
./docker-stop.sh

# Test TTS server
curl -X POST http://localhost:8880/v1/audio/speech -H "Content-Type: application/json" -d '{"model":"tts-1","voice":"am_onyx","input":"Test"}' --output test.mp3

# Check health
curl http://localhost:8000/health

# View logs (Docker)
docker-compose -f docker/docker-compose.yml logs -f

# Backup data
tar -czf backup-$(date +%Y%m%d).tar.gz storage/
```

### File Locations

- **Configuration**: `models.json`
- **Environment**: `.env` or `docker/.env`
- **Database**: `storage/audiobooks_db.json`
- **Uploads**: `storage/ebooks/`
- **Generated**: `storage/audiobooks/` and `storage/lrc/`
- **Logs**: Terminal output or Docker logs

---

**Enjoy your audiobook server!** 📚🎧
