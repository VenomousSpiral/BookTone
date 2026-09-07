#!/bin/bash
# Pre-flight fix: ensure stream-cache directories are owned by the current user.
# If the server was previously started as sudo/root, those cache dirs get root ownership,
# causing "Permission denied" on every audio chunk write for non-root processes.

cd "$HOME/AI-projects/BookTone"

AUDIODOCS_DIR="storage/audiobooks"
mkdir -p "$AUDIODOCS_DIR"

CURRENT_USER=$(whoami)
echo "[FIX] Checking stream-cache ownership for user: $CURRENT_USER"
stale=0
for d in "$AUDIODOCS_DIR"/_stream_cache_*; do
    [ -d "$d" ] || continue
    dir_owner=$(stat -c '%U' "$d" 2>/dev/null || echo "unknown")
    if [ "$dir_owner" != "$CURRENT_USER" ]; then
        stale=$((stale + 1))
        # Try sudo chown; prompts for password interactively (-S reads from stdin)
        if command -v sudo &> /dev/null; then
            echo "[FIX] $d owned by '$dir_owner' → fixing to '$CURRENT_USER:$CURRENT_USER'"
            echo "" | sudo -S chown -R "$CURRENT_USER:$CURRENT_USER" "$d" 2>/dev/null || \
                echo "      (sudo failed — run manually: cd storage/audiobooks && sudo chown -R $CURRENT_USER $(basename $d))"
        fi
    fi
done
[ "$stale" -gt 0 ] && echo "[FIX] Fixed $stale stale cache dir(s)." || true

source venv/bin/activate
python3 backend/run.py
