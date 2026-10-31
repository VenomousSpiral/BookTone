#!/bin/bash
# Pre-flight fix: ensure stream-cache directories are owned by the current user.
# If the server was previously started as sudo/root, those cache dirs get root ownership,
# causing "Permission denied" on every audio chunk write for non-root processes.


source venv/bin/activate
python3 backend/run.py
