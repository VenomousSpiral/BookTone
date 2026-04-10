"""Helper utility functions"""
from datetime import datetime
from pathlib import Path
import hashlib

def generate_id(text: str) -> str:
    """Generate a unique ID from text"""
    return hashlib.md5(text.encode()).hexdigest()[:8]

def format_file_size(bytes: int) -> str:
    """Format bytes to human-readable size"""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if bytes < 1024:
            return f"{bytes:.1f} {unit}"
        bytes /= 1024
    return f"{bytes:.1f} TB"

def ensure_dir(path: Path):
    """Ensure directory exists"""
    path.mkdir(parents=True, exist_ok=True)

def format_timestamp(dt: datetime) -> str:
    """Format datetime to string"""
    return dt.strftime("%Y-%m-%d %H:%M:%S")
