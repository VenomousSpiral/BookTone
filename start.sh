#!/bin/bash

echo "🚀 Audiobook Server - Quick Start"
echo "=================================="
echo ""

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    echo "📦 Creating virtual environment..."
    python3 -m venv venv
fi

# Activate virtual environment
echo "🔧 Activating virtual environment..."
source venv/bin/activate

# Install dependencies
echo "📥 Installing dependencies..."
cd backend
pip install -r requirements.txt

# Check for .env file
cd ..
if [ ! -f ".env" ]; then
    echo "⚙️  Creating .env file from example..."
    cp .env.example .env
    echo "✅ .env file created (API key optional for self-hosted servers)"
    echo ""
fi

# Create storage directories if they don't exist
mkdir -p storage/ebooks
mkdir -p storage/audiobooks
mkdir -p storage/lrc

# Pre-flight fix: ensure all storage dirs are owned by current user (fixes stale root-owned dirs from prior sudo/docker runs)
CURRENT_USER=$(whoami)
echo "[FIX] Checking storage ownership for user: $CURRENT_USER"
stale=0

# Fix top-level storage subdirs that may be root-owned
for d in "$PWD/storage/stream_cache" "$PWD/storage/download_jobs"; do
    [ -d "$d" ] || continue
    dir_owner=$(stat -c '%U' "$d" 2>/dev/null || echo "unknown")
    if [ "$dir_owner" != "$CURRENT_USER" ]; then
        stale=$((stale + 1))
        if command -v sudo &> /dev/null; then
            echo "[FIX] $d owned by '$dir_owner' → fixing to '$CURRENT_USER:$CURRENT_USER'"
            echo "" | sudo -S chown -R "$CURRENT_USER:$CURRENT_USER" "$d" 2>/dev/null || \
                echo "      (sudo failed — run manually: cd storage && sudo chown -R $CURRENT_USER $(basename $d))"
        fi
    fi
done

# Fix all _stream_cache_* dirs and their nested subdirs (e.g. OmniVoice/Narrator-UK/)
cache_dirs=$(find "$PWD/storage" -type d -name '_stream_cache_*' 2>/dev/null || true)
if [ -n "$cache_dirs" ]; then
    for d in $cache_dirs; do
        dir_owner=$(stat -c '%U' "$d" 2>/dev/null || echo "unknown")
        if [ "$dir_owner" != "$CURRENT_USER" ]; then
            stale=$((stale + 1))
            if command -v sudo &> /dev/null; then
                echo "[FIX] $d owned by '$dir_owner' → fixing to '$CURRENT_USER:$CURRENT_USER'"
                echo "" | sudo -S chown -R "$CURRENT_USER:$CURRENT_USER" "$d" 2>/dev/null || \
                    echo "      (sudo failed — run manually: cd storage && sudo find . -name '_stream_cache_*' -exec chown -R $CURRENT_USER {} +)"
            fi
        else
            # Dir itself is fine but check for nested subdirs that might be root-owned
            while IFS= read -r subdir; do
                [ -z "$subdir" ] && continue
                subdir_owner=$(stat -c '%U' "$subdir" 2>/dev/null || echo "unknown")
                if [ "$subdir_owner" != "$CURRENT_USER" ]; then
                    stale=$((stale + 1))
                    if command -v sudo &> /dev/null; then
                        echo "[FIX]   $subdir owned by '$subdir_owner' → fixing"
                        echo "" | sudo -S chown -R "$CURRENT_USER:$CURRENT_USER" "$subdir" 2>/dev/null || \
                            echo "      (sudo failed)"
                    fi
                fi
            done < <(find "$d" -type d ! -path "$d" 2>/dev/null)
        fi
    done
fi
[ "$stale" -gt 0 ] && echo "[FIX] Fixed $stale stale dir(s)." || true

echo ""
echo "✅ Setup complete!"
echo ""
echo "To start the server:"
echo "  1. Run: cd backend && python run.py"
echo "  2. Open: http://localhost:8000"
echo "  3. Go to Models tab to add your self-hosted TTS servers"
echo ""
echo "Or run directly:"
echo "  cd backend && uvicorn app.main:app --reload --host 0.0.0.0 --port 8000"
echo ""
