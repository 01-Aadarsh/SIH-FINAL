"""
Sarvam AI (Saaras) speech-to-text for IP-SAKTI.

Reuses SARVAM_API_KEY (see api/translation.py, api/tts.py) — no separate
key. Confirmed against Sarvam's own docs (docs.sarvam.ai/api-reference-docs/
speech-to-text/transcribe and .../models/saaras, 2026-09): endpoint, auth
header, multipart field names, and response shape. Not verified against a
live call in this environment at write time — if a field name is wrong
despite matching the docs, transcribe_audio() raises RuntimeError with the
real HTTP status/body, so the failure is loud and specific rather than a
silently wrong transcript (unlike api/translation.py and api/tts.py, ASR
has no "fall back to the original text" option — there is no text yet — so
this module does NOT fail open; see transcribe_audio()'s docstring).

Usage:
    python -m api.asr path/to/audio.wav
    python -m api.asr path/to/audio.wav hi-IN
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(override=True)

log = logging.getLogger(__name__)

SARVAM_API_KEY = os.getenv("SARVAM_API_KEY")
SARVAM_BASE_URL = "https://api.sarvam.ai"
SARVAM_ASR_MODEL = os.getenv("SARVAM_ASR_MODEL", "saaras:v3")
ASR_TIMEOUT = 30.0

# The four formats the /voice/transcribe and /voice/query endpoints accept,
# per this feature's own spec — a deliberately narrower list than what
# Sarvam's API itself supports (WAV, MP3, AAC, AIFF, OGG, OPUS, FLAC,
# MP4/M4A, AMR, WMA, WebM, PCM), so a client gets a clear 415 here instead
# of an opaque Sarvam-side error for a format this endpoint doesn't claim
# to support.
ALLOWED_EXTENSIONS: frozenset[str] = frozenset({".wav", ".mp3", ".m4a", ".webm"})

# BCP-47 codes Sarvam accepts for STT language_code, per docs.sarvam.ai —
# "unknown" triggers auto-detection rather than asserting a language.
# Deliberately a small, explicit set matching this feature's own spec
# (hi-IN, ta-IN, en-IN) plus "unknown" and the rest of Sarvam's documented
# STT language coverage, rather than reusing api/translation.py's larger
# TARGET_LANGUAGE_CODES — STT and translation are different Sarvam APIs
# with their own independently documented language support, and the two
# lists happening to overlap heavily is not a guarantee they're identical.
SUPPORTED_LANGUAGE_CODES: frozenset[str] = frozenset({
    "unknown", "hi-IN", "ta-IN", "en-IN", "bn-IN", "gu-IN", "kn-IN",
    "ml-IN", "mr-IN", "od-IN", "pa-IN", "te-IN",
})


class UnsupportedAudioFormat(ValueError):
    """Raised for a file extension outside ALLOWED_EXTENSIONS — a 415 at
    the API layer, not a 502 (this is a client-input problem, not a
    Sarvam-call failure)."""


def _validate_extension(filename: str) -> None:
    ext = Path(filename or "").suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise UnsupportedAudioFormat(
            f"Unsupported audio format {ext or '(none)'!r} — expected one of "
            f"{sorted(ALLOWED_EXTENSIONS)}."
        )


_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(base_url=SARVAM_BASE_URL, timeout=ASR_TIMEOUT)
    return _client


async def transcribe_audio(
    audio_bytes: bytes, filename: str, language_code: str = "unknown"
) -> tuple[str, str]:
    """
    Transcribe `audio_bytes` via Sarvam's Saaras model. Returns
    (transcript, detected_language).

    Does NOT fail open, unlike api/translation.py and api/tts.py: those
    modules degrade to "return the original text unchanged" or "return no
    audio" because a reasonable fallback exists either way. ASR has none —
    there is no text to fall back to — so any failure (bad key, unreachable
    API, unexpected response shape, Sarvam-side error) raises RuntimeError
    with the real cause, for the caller (api/main.py) to turn into an
    honest error response instead of silently proceeding with an empty or
    wrong transcript.

    Raises UnsupportedAudioFormat (ValueError) before ever calling Sarvam
    if `filename`'s extension isn't one of ALLOWED_EXTENSIONS, and
    ValueError if `language_code` isn't one of SUPPORTED_LANGUAGE_CODES —
    both are caller-input problems, checked here so the API layer can map
    them to 415/422 without inspecting exception text.
    """
    _validate_extension(filename)
    if language_code not in SUPPORTED_LANGUAGE_CODES:
        raise ValueError(
            f"Unsupported language_code {language_code!r} — expected one of "
            f"{sorted(SUPPORTED_LANGUAGE_CODES)}."
        )
    if not SARVAM_API_KEY:
        raise RuntimeError("SARVAM_API_KEY is not set — cannot transcribe audio.")

    client = _get_client()
    try:
        response = await client.post(
            "/speech-to-text",
            headers={"api-subscription-key": SARVAM_API_KEY},
            files={"file": (filename, audio_bytes)},
            data={
                "model": SARVAM_ASR_MODEL,
                "language_code": language_code,
                "mode": "transcribe",
            },
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise RuntimeError(
            f"Sarvam STT request failed: {exc.response.status_code} {exc.response.text}"
        ) from exc
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Sarvam STT request failed: {exc}") from exc

    body = response.json()
    transcript = body.get("transcript")
    if transcript is None:
        raise RuntimeError(f"Sarvam STT response had no transcript: {body!r}")

    # language_probability isn't surfaced by this function's return type
    # (transcript, detected_language) — this feature's spec only asks for
    # the detected language string, not a confidence score alongside it.
    detected_language = body.get("language_code") or language_code
    return transcript, detected_language


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if len(sys.argv) < 2:
        print("Usage: python -m api.asr <audio_file> [language_code]")
        sys.exit(1)

    audio_path = Path(sys.argv[1])
    lang = sys.argv[2] if len(sys.argv) > 2 else "unknown"

    transcript, detected = asyncio.run(
        transcribe_audio(audio_path.read_bytes(), audio_path.name, lang)
    )
    print(f"Detected language: {detected}")
    print(f"Transcript: {transcript}")
