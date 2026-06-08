"""
API routes for audiobook download/combine operations.

Endpoints (NEW job-based system):
    POST   /api/stream/download-start       Start a download conversion (returns job_id)
    GET    /api/stream/download-progress/<job_id>  Poll real-time progress updates  
    GET    /api/stream/download/<job_id>    Download the completed combined file by job ID

Endpoints (backward-compat shims):
    POST   /api/stream/prepare-download     → redirects to download-start with format=opus
    GET    /api/stream/download-status      → legacy status check for existing output files  
    GET    /api/stream/download             → legacy direct download of combined.opus file
    GET    /api/stream/download-source      → Download the source ebook file

Background conversion uses a dedicated thread per job with real-time progress updates.
"""
import json
import logging
import os
import subprocess
import threading
import tempfile as _tempfile_module
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from fastapi.responses import FileResponse

from app.services.job_manager import JobManager
from app.services.download_service import get_job_manager, _run_download_job
from app.core.config import settings
from app.utils.path_utils import resolve_combined_audio_path
from app.utils.validators import validate_ebook_path
from app.services import stream_service

logger = logging.getLogger(__name__)

router = APIRouter()


# ─── NEW: Job-Based Download Endpoints ──────────────────────────────────

@router.post("/download-start")
async def start_download(
    ebook_path: str = Query(...),
    model: str = Query(...),
    voice: str = Query(...),
    format_type: str = Query("opus", regex="^(opus|m4b|mp3)$"),
):
    """Start a download conversion for the selected format.

    Returns immediately with job_id. The actual ffmpeg conversion runs in a background thread
    and can be polled via /download-progress/<job_id>.
    
    If the combined file already exists (skip-if-ready), returns status=ready immediately 
    without starting a new conversion.
    """
    ebook_path = validate_ebook_path(ebook_path)
    
    try:
        job_mgr = get_job_manager()
        
        # Get or create job — skip-if-ready if output file already exists  
        job = job_mgr.get_or_create(ebook_path, model, voice, format_type)
        
        if job["status"] == "ready":
            return {
                "job_id": job["job_id"],
                "status": "ready",
                "format_type": format_type,
                "audio_files_count": job.get("audio_files_count", 0),
                "message": "Already prepared — download immediately.",
                "output_file": job.get("output_file"),
            }

        # Create a fresh pending job (in case it was created in failed state)  
        if job["status"] == "failed" or job["status"] == "pending":
            job = job_mgr.create_job(ebook_path, model, voice, format_type)

        # Start background conversion thread  
        t = threading.Thread(
            target=_run_download_job,
            args=(job["job_id"], ebook_path, model, voice, format_type, 
                  settings.AUDIOBOOKS_DIR, job_mgr),
            daemon=True,
        )
        t.start()

        return {
            "job_id": job["job_id"],
            "status": job["status"] or "pending",
            "format_type": format_type,
            "audio_files_count": job.get("audio_files_count", 0),
            "message": f"Download conversion started ({format_type.upper()})",
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error("[DOWNLOAD-START] Error starting download for %s/%s/%s (%s): %s",
                     ebook_path, model, voice, format_type, e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/download-progress/{job_id}")
async def get_download_progress(job_id: str):
    """Poll for real-time download conversion progress.

    Returns job state including status (pending | converting | ready | failed), 
    progress percentage (0-100), and a human-readable message with phase info.
    
    During active conversion, this endpoint should be polled every 500ms–2s for smooth UI updates.
    """
    try:
        job_mgr = get_job_manager()
        job = job_mgr.get_job(job_id)

        if not job:
            raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

        return {
            "job_id": job["job_id"],
            "status": job["status"],
            "progress_pct": job.get("progress_pct", 0),
            "message": job.get("message", ""),
            "format_type": job.get("format_type"),
            "audio_files_count": job.get("audio_files_count", 0),
            "output_file": job.get("output_file"),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error("[DOWNLOAD-PROGRESS] Error getting progress for %s: %s", job_id, e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/download/{job_id}")
async def download_by_job(job_id: str):
    """Download the completed combined file by job ID.

    Resolves to the actual output path stored in the job record when conversion finished.
    Falls back to checking common output locations if the job is missing its output_file field.
    """
    try:
        job_mgr = get_job_manager()
        job = job_mgr.get_job(job_id)

        if not job:
            raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

        # If conversion failed or still in progress  
        if job["status"] == "failed":
            error_msg = job.get("error_message", "Conversion failed with unknown error")
            raise HTTPException(
                status_code=400, 
                detail=f"Download unavailable: {job['status']}. Error: {error_msg}"
            )

        if job["status"] != "ready":
            raise HTTPException(
                status_code=409, 
                detail=f"Conversion still in progress (status={job['status']}). Check /download-progress/{job_id} for updates."
            )

        # Resolve the output file path  
        output_path_str = job.get("output_file") or ""
        
        if not os.path.exists(output_path_str):
            raise HTTPException(
                status_code=404, 
                detail=f"Output file missing: {output_path_str}. Job may have been cleaned up."
            )

        # Determine format from job record  
        fmt = job.get("format_type", "opus")
        
        safe_title = "".join(c if c.isalnum() or c in " -_" else "_" for c in Path(job["ebook_path"]).stem)
        filename = f"{safe_title}.{fmt}"

        return FileResponse(
            path=output_path_str,
            filename=filename,
            media_type=f"audio/{fmt}",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("[DOWNLOAD-BY-JOB] Error for job %s: %s", job_id, e)
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(status_code=500, detail=str(e))


# ─── BACKWARD-COMPAT SHIMS (legacy endpoints kept for existing clients) ─

def _combine_stream_cache(
    cache_dir: str, combined_path: str, temp_output: str, audio_format: str,
):
    """Background task to combine stream cache audio files into a single OPUS/MP3 file.

    Kept only for backward compatibility with legacy /prepare-download flow.
    New code should use the job-based download-start endpoint instead.
    """
    try:
        concat_file = _tempfile_module.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
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
            logger.error("[DOWNLOAD] ffmpeg failed for legacy combine: %s", 
                        stderr.decode("utf-8", errors="ignore")[:500])
            return

        os.rename(temp_output, combined_path)
        logger.info(
            "[DOWNLOAD] Legacy combined %s -> %s", cache_dir, combined_path
        )

    except Exception as e:
        logger.error("[DOWNLOAD] Failed to combine stream cache (legacy): %s", e)
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
    """Legacy endpoint — redirects to the new job-based system.

    For backward compatibility only. New clients should use POST /download-start with format_type parameter.
    This always defaults to OPUS format and returns a simplified status response.
    """
    ebook_path = validate_ebook_path(ebook_path)
    
    # Try redirecting to the new job-based system first  
    try:
        from app.services.download_service import get_job_manager as _get_mgr
        
        job_mgr = _get_mgr()
        
        # Check if already ready (skip-if-ready check for legacy path)
        combined_path_legacy = resolve_combined_audio_path(
            settings.AUDIOBOOKS_DIR, ebook_path, model, voice,
            audio_format=settings.AUDIO_FORMAT or "opus",
            compute_hash_fn=stream_service._compute_ebook_hash,
        )

        if not (settings.AUDIOBOOKS_DIR / "_stream_cache_" ).exists():
            # No cache dir at all — fall back to legacy behavior  
            pass  # Will proceed with legacy path below
    except Exception:
        pass  # Fall through to legacy implementation
    
    try:
        full_path = stream_service._resolve_ebook_path(ebook_path)
        file_hash = stream_service._compute_ebook_hash(full_path)[:12]
        ebook_stem = Path(ebook_path).stem
        safe_stem = "".join(
            c if c.isalnum() or c in "-_" else "_" for c in ebook_stem
        )[:50]
        
        cache_dir_name = f"_stream_cache_{safe_stem}_{file_hash}"
        audio_format = settings.AUDIO_FORMAT or "opus"
        cache_dir = (settings.AUDIOBOOKS_DIR / cache_dir_name) / model / voice
        
        combined_path_legacy = resolve_combined_audio_path(
            settings.AUDIOBOOKS_DIR, ebook_path, model, voice,
            audio_format=audio_format,
            compute_hash_fn=stream_service._compute_ebook_hash,
        )

        if not cache_dir.exists():
            raise HTTPException(status_code=404, detail="No cached audio found")

        # Check skip-if-ready for legacy path  
        if combined_path_legacy.exists() and combined_path_legacy.stat().st_size > 100:
            file_size = combined_path_legacy.stat().st_size
            return {
                "status": "ready",
                "message": "Already prepared (legacy)",
                "file_size": file_size,
                "file_size_mb": round(file_size / (1024 * 1024), 1),
            }

        audio_files = sorted(
            cache_dir.glob(f"audio_*.{audio_format}"),
            key=lambda p: int(p.stem.split("_")[1]),
        )

        if not audio_files:
            raise HTTPException(status_code=400, detail="No audio files to combine")

        temp_output = str(combined_path_legacy) + ".tmp"
        
        # Use legacy BackgroundTasks approach (no progress tracking — kept for compat only)  
        background_tasks.add_task(
            _combine_stream_cache, str(cache_dir), str(combined_path_legacy),
            temp_output, audio_format,
        )

        return {
            "status": "in_progress",
            "message": "Preparing download in background... (legacy)",
            "audio_files": len(audio_files),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error("[PREPARE-DOWNLOAD-LEGACY] Error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/download-status")
async def get_download_status_legacy(
    ebook_path: str = Query(...),
    model: str = Query(...),
    voice: str = Query(...),
):
    """Legacy status check — checks if combined.opus exists on disk.

    Kept for backward compatibility with existing clients that poll this endpoint directly.
    New code should use the job-based /download-progress/{job_id} instead.
    """
    ebook_path = validate_ebook_path(ebook_path)
    
    try:
        audio_format = settings.AUDIO_FORMAT or "opus"
        
        combined_legacy = resolve_combined_audio_path(
            settings.AUDIOBOOKS_DIR, ebook_path, model, voice,
            audio_format=audio_format,
            compute_hash_fn=stream_service._compute_ebook_hash,
        )

        if combined_legacy.exists():
            file_size = combined_legacy.stat().st_size
            return {
                "status": "ready",
                "progress": 100,
                "message": "Ready to download (legacy)",
                "file_size": file_size,
                "file_size_mb": round(file_size / (1024 * 1024), 1),
            }

        # Also check for new job-based output  
        try:
            from app.services.download_service import get_job_manager as _get_mgr
            jm = _get_mgr()
            
            safe_stem = "".join(c if c.isalnum() or c in "-_" else "_" for c in Path(ebook_path).stem)[:50]
            file_hash = stream_service._compute_ebook_hash(
                stream_service._resolve_ebook_path(ebook_path)
            )[:12]

            # Try to find a matching job by scanning jobs directory  
            import os as _os_module
            jobs_dir = Path(__file__).resolve().parent.parent / "storage" / "download_jobs"
            
            if jobs_dir.exists():
                for jf in sorted(jobs_dir.glob("*.json")):
                    with open(jf) as fj:
                        try:
                            jd = json.load(fj)  # type: ignore[name-defined]
                            if (jd.get("ebook_path") == ebook_path and 
                                jd.get("model_name") == model and 
                                jd.get("voice") == voice):
                                
                                return {
                                    "job_id": jd["job_id"],
                                    "status": jd["status"],
                                    "progress_pct": jd.get("progress_pct", 0),
                                    "message": jd.get("message", ""),
                                    "format_type": jd.get("format_type"),
                                }
                        except (json.JSONDecodeError, KeyError):
                            continue
        except Exception:
            pass

        return {
            "status": "not_started",
            "progress": 0,
            "message": "Not prepared yet (legacy)",
        }
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Ebook not found")
    except Exception as e:
        logger.error("[DOWNLOAD-STATUS-LEGACY] Error: %s", e)
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/download")
async def download_combined_legacy(
    ebook_path: str = Query(...),
    model: str = Query(...),
    voice: str = Query(...),
):
    """Legacy direct download — serves the combined.opus file directly.

    Kept for backward compatibility. New code should use /download/{job_id} after starting a job.
    """
    ebook_path = validate_ebook_path(ebook_path)
    
    try:
        audio_format = settings.AUDIO_FORMAT or "opus"
        
        combined_legacy = resolve_combined_audio_path(
            settings.AUDIOBOOKS_DIR, ebook_path, model, voice,
            audio_format=audio_format,
            compute_hash_fn=stream_service._compute_ebook_hash,
        )

        if not combined_legacy.exists():
            raise HTTPException(
                status_code=404,
                detail="Combined file not found. Use /prepare-download first.",
            )

        safe_title = "".join(c if c.isalnum() or c in " -_" else "_" for c in ebook_path)
        filename = f"{safe_title}.{audio_format}"

        return FileResponse(
            path=str(combined_legacy),
            filename=filename,
            media_type=f"audio/{audio_format}",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Ebook not found")
    except HTTPException:
        raise
    except Exception as e:
        logger.error("[DOWNLOAD-LEGACY] Error: %s", e)
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/download-source")  
async def download_source(ebook_path: str = Query(...)):
    """Download the source ebook file (unchanged from original)."""
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
        raise HTTPException(status_code=404, detail="Source file not found")
    except HTTPException:
        raise
    except Exception as e:
        logger.error("[DOWNLOAD-SOURCE] Error: %s", e)
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(status_code=500, detail=str(e))

