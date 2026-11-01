#!/bin/bash
# Pre-flight: ensure all storage directories are owned by the current user.
# If the server was previously started as sudo/root, those cache/files get root ownership,
# causing "Permission denied" on every audio chunk write for non-root processes.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CURRENT_USER=$(whoami)

echo "[PERM FIX] Ensuring storage dirs are owned by $CURRENT_USER..."

# Fix ownership recursively on ALL writable directories (relative to script/project root).
# These are all the directories that write files at runtime:
for dir in \
    "$SCRIPT_DIR/storage" \
    "$SCRIPT_DIR/storage/audiobooks" \
    "$SCRIPT_DIR/storage/ebooks" \
    "$SCRIPT_DIR/storage/lrc" \
    "$SCRIPT_DIR/storage/download_jobs" \
    "$SCRIPT_DIR/storage/stream_cache"; do

    if [ -d "$dir" ]; then
        echo "[PERM FIX]  chown $CURRENT_USER:$(id -gn) '$dir' (recursive)"
        sudo chown -R "${CURRENT_USER}":"$(id -gn)" "$dir"
    fi
done

# Also fix any subdirectories that may have been created under storage/ by root.
if [ -d "$SCRIPT_DIR/storage" ]; then
    echo "[PERM FIX]  Scanning for any other owned-by-root files under 'storage/'..."
    sudo find "$SCRIPT_DIR/storage" ! -user "$CURRENT_USER" -exec chown "${CURRENT_USER}":"$(id -gn)" {} + 2>/dev/null || true
fi

echo "[PERM FIX] Done."

# Activate venv and start the server.
source "$SCRIPT_DIR/venv/bin/activate"
python3 "$SCRIPT_DIR/backend/run.py"
