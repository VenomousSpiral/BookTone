"""
API routes for audiobook download/combine operations.

Endpoints:
    POST   /api/stream/prepare-download
    GET    /api/stream/download-status
    GET    /api/stream/download
    GET    /api/stream/download-source
"""
import os
import subprocess
import tempfile
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from fastapi.responses import FileResponse

from app.services import stream_service
from app.core.config import settings
from app.utils.path_utils import resolve_combined_audio_path
from app.utils.validators import validate_ebook_path

router = APIRouter()



def _combine_stream_cache(
    cache_dir: str, combined_path: str, temp_output: str, audio_format: str
):
    """
    Background task: combine stream cache audio files into a single OPUS/MP3 file.
    """
    try:
        concat_file = tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False
        )
        audio_path = Path(cache_dir)
        for af in sorted(
            audio_path.glob(f"audio_*.{audio_format}"),
            key=lambda p: int(p.stem.split("_")[1]),
        ):
            escaped = str(af).replace("'", "'\\''")
            concat_file.write(f"file '{escaped}'\n")
        concat_file.close()

        ffmpeg_format = "opus" if audio_format == "opus" else "mp3"

        cmd = [
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", concat_file.name,
            "-c", "copy",
            "-f", ffmpeg_format,
            "-loglevel", "error",
            temp_output,
        ]

        process = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        stdout, stderr = process.communicate()

        os.unlink(concat_file.name)

        if not os.path.exists(temp_output) or os.path.getsize(temp_output) == 0:
            import logging
            logging.getLogger(__name__).error(
                "[DOWNLOAD] ffmpeg failed: %s",
                stderr.decode("utf-8", errors="ignore")[:500],
            )
            return

        os.rename(temp_output, combined_path)
        logging.getLogger(__name__).info(
            "[DOWNLOAD] Combined %s -> %s", cache_dir, combined_path
        )

    except Exception as e:
        logging.getLogger(__name__).error(
            "[DOWNLOAD] Failed to combine stream cache: %s", e
        )
        try:
            if os.path.exists(temp_output):
                os.unlink(temp_output)
        except OSError:
            pass


@router.post("/prepare-download")
async def prepare_download(
    ebook_path: str = Query(...),
    model: str = Query(...),
    voice: str = Query(...),
    background_tasks: BackgroundTasks = None,
):
    """Combine all stream cache chunks into a single OPUS file."""
    ebook_path = validate_ebook_path(ebook_path)
    try:
        full_path = stream_service._resolve_ebook_path(ebook_path)
        file_hash = stream_service._compute_ebook_hash(full_path)[:12]
        ebook_stem = Path(ebook_path).stem
        safe_stem = "".join(
            c if c.isalnum() or c in "-_" else "_" for c in ebook_stem
        )[:50]
        cache_dir = (
            settings.AUDIOBOOKS_DIR
            / f"_stream_cache_{safe_stem}_{file_hash}"
            / model
            / voice
        )
        combined_path = resolve_combined_audio_path(
            settings.AUDIOBOOKS_DIR, ebook_path, model, voice,
            audio_format=settings.AUDIO_FORMAT,
            compute_hash_fn=stream_service._compute_ebook_hash,
        )

        if not cache_dir.exists():
            raise HTTPException(status_code=404, detail="No cached audio found")

        if combined_path.exists():
            file_size = combined_path.stat().st_size
            return {
                "status": "ready",
                "message": "Already prepared",
                "file_size": file_size,
                "file_size_mb": round(file_size / (1024 * 1024), 1),
            }

        audio_files = sorted(
            cache_dir.glob(f"audio_*.{settings.AUDIO_FORMAT}"),
            key=lambda p: int(p.stem.split("_")[1]),
        )

        if not audio_files:
            raise HTTPException(status_code=400, detail="No audio files to combine")

        temp_output = str(combined_path) + ".tmp"
        background_tasks.add_task(
            _combine_stream_cache, str(cache_dir), str(combined_path),
            temp_output, settings.AUDIO_FORMAT,
        )

        return {
            "status": "in_progress",
            "message": "Preparing download in background...",
            "audio_files": len(audio_files),
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/download-status")
async def get_download_status(
    ebook_path: str = Query(...),
    model: str = Query(...),
    voice: str = Query(...),
):
    """Check if combined download is ready"""
    ebook_path = validate_ebook_path(ebook_path)
    try:
        combined_path = resolve_combined_audio_path(
            settings.AUDIOBOOKS_DIR, ebook_path, model, voice,
            audio_format=settings.AUDIO_FORMAT,
            compute_hash_fn=stream_service._compute_ebook_hash,
        )

        if combined_path.exists():
            file_size = combined_path.stat().st_size
            return {
                "status": "ready",
                "progress": 100,
                "message": "Ready to download",
                "file_size": file_size,
                "file_size_mb": round(file_size / (1024 * 1024), 1),
            }

        return {
            "status": "not_started",
            "progress": 0,
            "message": "Not prepared yet",
        }
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Ebook not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/download")
async def download_combined(
    ebook_path: str = Query(...),
    model: str = Query(...),
    voice: str = Query(...),
):
    """Download the combined OPUS file"""
    ebook_path = validate_ebook_path(ebook_path)
    try:
        combined_path = resolve_combined_audio_path(
            settings.AUDIOBOOKS_DIR, ebook_path, model, voice,
            audio_format=settings.AUDIO_FORMAT,
            compute_hash_fn=stream_service._compute_ebook_hash,
        )

        if not combined_path.exists():
            raise HTTPException(
                status_code=404,
                detail="Combined file not found. Use /prepare-download first.",
            )

        safe_title = "".join(
            c if c.isalnum() or c in " -_" else "_" for c in ebook_path
        )
        filename = f"{safe_title}.{settings.AUDIO_FORMAT}"

        return FileResponse(
            path=str(combined_path),
            filename=filename,
            media_type=f"audio/{settings.AUDIO_FORMAT}",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Ebook not found")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/download-source")
async def download_source(ebook_path: str = Query(...)):
    """Download the source ebook file"""
    ebook_path = validate_ebook_path(ebook_path)
    try:
        full_path = stream_service._resolve_ebook_path(ebook_path)
        if not full_path.exists():
            raise HTTPException(status_code=404, detail="Source file not found")

        return FileResponse(
            path=str(full_path),
            filename=full_path.name,
            headers={"Content-Disposition": f'attachment; filename="{full_path.name}"'},
        )
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Ebook not found")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
