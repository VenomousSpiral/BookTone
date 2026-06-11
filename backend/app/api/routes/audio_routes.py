"""
API routes for audio generation and streaming settings.

Endpoints:
    POST   /api/stream/audio
    GET    /api/stream/settings
    POST   /api/stream/settings
"""
import time
import logging
import traceback
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from app.services import stream_service
from app.models.streaming_models import (
    StreamAudioRequest,
    UpdateStreamSettingsRequest,
)
from app.core.config import settings
from app.utils.validators import validate_ebook_path

router = APIRouter()


_logger = logging.getLogger("ebook_parser")
_stream_logger = logging.getLogger(__name__)


@router.post("/audio")
async def generate_audio(request: StreamAudioRequest):
    """Generate audio for a text segment on-demand."""
    request.ebook_path = validate_ebook_path(request.ebook_path)
    request_start = time.time()
    _stream_logger.debug(
        "[AUDIO] Request received: chars %d-%d, model=%s, voice=%s",
        request.start_char, request.end_char, request.model, request.voice,
    )

    try:
        text_start = time.time()
        text = stream_service.get_text_segment(
            request.ebook_path, request.start_char, request.end_char
        )
        text_time = time.time() - text_start
        _stream_logger.debug(
            "[AUDIO] Text extraction took %dms, length=%d chars",
            text_time * 1000, len(text),
        )

        if not text.strip():
            raise HTTPException(status_code=400, detail="Text segment is empty")

        gen_start = time.time()
        # Respect user's save_stream_audio setting for streaming requests
        stream_settings = stream_service.load_settings()
        save_to_disk = bool(stream_settings.get("save_stream_audio", True))
        audio_data = stream_service.generate_audio_for_text(
            text,
            request.model,
            request.voice,
            ebook_path=request.ebook_path,
            start_char=request.start_char,
            end_char=request.end_char,
            save_to_disk=save_to_disk,
        )
        gen_time = time.time() - gen_start
        total_time = time.time() - request_start
        _stream_logger.debug(
            "[AUDIO] TTS generation took %dms, audio size=%d bytes",
            gen_time * 1000, len(audio_data),
        )
        _stream_logger.debug("[AUDIO] Total request time: %dms", total_time * 1000)

        media_types = {"opus": "audio/opus", "mp3": "audio/mpeg"}
        return Response(
            content=audio_data,
            media_type=media_types.get(settings.AUDIO_FORMAT, "audio/mpeg"),
            headers={
                "Content-Disposition": "inline",
                "Cache-Control": "public, max-age=3600",
                "Accept-Ranges": "bytes",
            },
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        _stream_logger.error("[ERROR] Audio generation failed: %s", e)
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/settings")
async def get_stream_settings():
    """Get streaming settings (model/voice preferences, display settings)."""
    try:
        settings_data = stream_service.load_settings()
        return settings_data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/settings")
async def update_stream_settings(request: UpdateStreamSettingsRequest):
    """Update streaming settings."""
    try:
        current_settings = stream_service.load_settings()

        if request.preferred_model is not None:
            current_settings["preferred_model"] = request.preferred_model
        if request.preferred_voice is not None:
            current_settings["preferred_voice"] = request.preferred_voice
        if request.font_size is not None:
            current_settings["font_size"] = request.font_size
        if request.font_family is not None:
            current_settings["font_family"] = request.font_family
        if request.progress_mode is not None:
            current_settings["progress_mode"] = request.progress_mode
        if request.time_mode is not None:
            current_settings["time_mode"] = request.time_mode
        if request.show_title is not None:
            current_settings["show_title"] = request.show_title
        if request.show_progress_bar is not None:
            current_settings["show_progress_bar"] = request.show_progress_bar
        if request.show_images is not None:
            current_settings["show_images"] = request.show_images
        if request.save_stream_audio is not None:
            current_settings["save_stream_audio"] = request.save_stream_audio
        if request.sleep_timer_minutes is not None:
            current_settings["sleep_timer_minutes"] = request.sleep_timer_minutes
        if request.show_sleep_timer is not None:
            current_settings["show_sleep_timer"] = request.show_sleep_timer

        stream_service.save_settings(current_settings)

        return {"message": "Settings updated", "settings": current_settings}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
