"""
Groq text-to-speech for IP-SAKTI.

Reads the answer aloud in English, always — Groq's only TTS models
(queried live against this project's own account: `canopylabs/orpheus-v1-
english`, `canopylabs/orpheus-arabic-saudi`) do not cover Hindi or any
other Indian language, so this synthesizes from the English answer
regardless of what language the text response was translated into (see
api/translation.py). A caller must not present this audio as being in the
user's selected language — it isn't, and can't be, until Groq (or another
provider) ships an Indian-language TTS voice.

Two things this module cannot verify from code, and had to be designed
around rather than confirmed, because the Groq org behind this project's
GROQ_API_KEY has not accepted canopylabs/orpheus-v1-english's model terms
yet (every real request 400s with "requires terms acceptance" regardless of
input — reproduced directly, not assumed):
  1. A valid `voice` name. GROQ_TTS_VOICE has no fabricated default for this
     reason — synthesize_speech() refuses to guess and skips TTS with a
     clear log message instead. To fix: an org admin visits
     https://console.groq.com/playground?model=canopylabs/orpheus-v1-english,
     accepts the terms, finds a real voice name there, and sets
     GROQ_TTS_VOICE.
  2. The exact per-request character limit (~200 per Groq's docs, not
     independently confirmed). TTS_MAX_CHARS defaults conservatively below
     that; text longer than one chunk is split at sentence boundaries and
     synthesized as multiple requests, concatenated into one WAV — safe
     regardless of exactly where the real limit falls, at the cost of doing
     more requests than strictly necessary if it turns out to be higher.

Uses the raw REST endpoint via httpx, not the `groq` Python SDK: the
version already pinned in requirements.txt (0.13.1, used elsewhere in this
codebase for chat completions) predates the SDK's `audio.speech` method —
confirmed by AttributeError, not assumed. Bumping the SDK version to get
that method risked the already-tested AsyncGroq chat-completion path in
generation/llm_client.py; hitting the documented REST endpoint directly
avoids that risk entirely for one extra module.

Usage:
    python -m api.tts "Section 3(p) excludes traditional knowledge from patentability."
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import os
import sys
import wave

import httpx
from dotenv import load_dotenv

from api.text_chunking import split_text

load_dotenv()

log = logging.getLogger(__name__)

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_TTS_MODEL = os.getenv("GROQ_TTS_MODEL", "canopylabs/orpheus-v1-english")
# No fabricated default — see module docstring point 1.
GROQ_TTS_VOICE = os.getenv("GROQ_TTS_VOICE", "")
GROQ_TTS_BASE_URL = "https://api.groq.com/openai/v1"
TTS_TIMEOUT = 20.0
TTS_MAX_CHARS = int(os.getenv("TTS_MAX_CHARS", "180"))

_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(base_url=GROQ_TTS_BASE_URL, timeout=TTS_TIMEOUT)
    return _client


def _concat_wav(wav_chunks: list[bytes]) -> bytes:
    """Properly concatenate WAV audio at the PCM-frame level (not a byte-wise
    file concat, which would leave every chunk after the first with a stray
    embedded RIFF header instead of continuous audio)."""
    output = io.BytesIO()
    writer: wave.Wave_write | None = None

    for chunk in wav_chunks:
        with wave.open(io.BytesIO(chunk), "rb") as reader:
            if writer is None:
                writer = wave.open(output, "wb")
                writer.setnchannels(reader.getnchannels())
                writer.setsampwidth(reader.getsampwidth())
                writer.setframerate(reader.getframerate())
            writer.writeframes(reader.readframes(reader.getnframes()))

    if writer is not None:
        writer.close()
    return output.getvalue()


async def synthesize_speech(text: str, language: str = "en-IN") -> str | None:
    """
    English-only TTS. Returns base64-encoded WAV audio, or None if synthesis
    isn't possible or fails for any reason — this never raises, matching
    translate_text()'s fail-open contract. `language` gates on whether the
    *source* answer is English at all; it does not select a voice language
    (there is only one language of voice available — see module docstring).
    """
    if not text.strip():
        return None
    if not language.startswith("en"):
        log.info(
            "TTS skipped: Groq's TTS models are English-only, answer language is %s",
            language,
        )
        return None
    if not GROQ_API_KEY:
        log.warning("GROQ_API_KEY not set — skipping TTS")
        return None
    if not GROQ_TTS_VOICE:
        log.warning(
            "GROQ_TTS_VOICE not configured (model terms not yet accepted for %s on this "
            "Groq account — see api/tts.py module docstring) — skipping TTS",
            GROQ_TTS_MODEL,
        )
        return None

    chunks = split_text(text, TTS_MAX_CHARS)
    audio_chunks: list[bytes] = []

    try:
        client = _get_client()
        for chunk in chunks:
            response = await client.post(
                "/audio/speech",
                headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
                json={
                    "model": GROQ_TTS_MODEL,
                    "voice": GROQ_TTS_VOICE,
                    "input": chunk,
                    "response_format": "wav",
                },
            )
            response.raise_for_status()
            audio_chunks.append(response.content)
    except Exception as exc:
        log.warning("TTS synthesis failed: %s — returning no audio", exc)
        return None

    if not audio_chunks:
        return None

    combined = _concat_wav(audio_chunks)
    return base64.b64encode(combined).decode("ascii")


if __name__ == "__main__":
    text = " ".join(sys.argv[1:])
    if not text:
        print('Usage: python -m api.tts "text to speak"')
        sys.exit(1)

    result = asyncio.run(synthesize_speech(text))
    if result is None:
        print("No audio produced — check the log lines above for why.")
        sys.exit(1)

    out_path = "tts_output.wav"
    with open(out_path, "wb") as f:
        f.write(base64.b64decode(result))
    print(f"Wrote {out_path} ({len(result)} base64 chars)")
