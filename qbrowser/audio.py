"""Explicitly configured OpenAI-compatible audio transcription adapter."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from urllib.parse import urlsplit

from qbrowser.credentials import get_secret

SUPPORTED_AUDIO = {".flac", ".m4a", ".mp3", ".mp4", ".mpeg", ".mpga", ".ogg", ".oga", ".wav", ".webm"}
MAX_TRANSCRIPTION_BYTES = 25 * 1024 * 1024


def transcribe_audio(path: Path, *, limit: int = 2_000_000) -> str:
    """Transcribe an explicit audio read through the configured API; never auto-upload on import."""
    path = Path(path)
    if path.is_symlink() or path.suffix.lower() not in SUPPORTED_AUDIO:
        raise ValueError("Audio source must be a regular supported audio file")
    if not 1 <= limit <= 2_000_000:
        raise ValueError("Transcript character limit must be between 1 and 2000000")
    base_url = os.getenv("QBROWSER_TRANSCRIPTION_BASE_URL", "").strip()
    key = get_secret("QBROWSER_TRANSCRIPTION_API_KEY")
    if not base_url or not key:
        raise RuntimeError(
            "Audio transcription is disabled until QBROWSER_TRANSCRIPTION_BASE_URL and "
            "QBROWSER_TRANSCRIPTION_API_KEY are configured")
    endpoint = urlsplit(base_url)
    local_host = endpoint.hostname in {"localhost", "127.0.0.1", "::1"}
    if (endpoint.scheme != "https" and not (endpoint.scheme == "http" and local_host)) or (
            not endpoint.hostname or endpoint.username or endpoint.password or endpoint.fragment):
        raise ValueError("Transcription endpoint must use HTTPS or localhost")
    try:
        timeout = float(os.getenv("QBROWSER_TRANSCRIPTION_TIMEOUT_SECONDS", "180"))
    except ValueError as exc:
        raise ValueError("QBROWSER_TRANSCRIPTION_TIMEOUT_SECONDS must be numeric") from exc
    if not 1 <= timeout <= 600:
        raise ValueError("Transcription timeout must be between 1 and 600 seconds")
    model = os.getenv("QBROWSER_TRANSCRIPTION_MODEL", "whisper-1").strip()
    if not model or len(model) > 120:
        raise ValueError("QBROWSER_TRANSCRIPTION_MODEL must contain 1-120 characters")
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("Install Qbrowser's native extra for audio transcription: pip install -e '.[native]'") from exc
    client = OpenAI(api_key=key, base_url=base_url, timeout=timeout, max_retries=1)
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, os.O_RDONLY | no_follow)
    except OSError as exc:
        raise ValueError("Audio source must be a readable regular file") from exc
    with os.fdopen(fd, "rb") as audio_file:
        info = os.fstat(audio_file.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("Audio source must be a regular supported audio file")
        if info.st_size > MAX_TRANSCRIPTION_BYTES:
            raise ValueError("Audio exceeds the 25 MB transcription request limit")
        result = client.audio.transcriptions.create(model=model, file=audio_file, response_format="text")
    text = result if isinstance(result, str) else getattr(result, "text", None)
    if not isinstance(text, str):
        raise RuntimeError("Transcription endpoint returned no text")
    return text[:limit]
