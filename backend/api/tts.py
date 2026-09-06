"""
Sarvam AI (Bulbul) text-to-speech for IP-SAKTI.

Replaces an earlier Groq-based implementation that used
`canopylabs/orpheus-v1-english` — abandoned because (a) it required an org
admin to manually accept the model's usage terms in the Groq console before
any request would succeed at all, a step this project's Groq account never
completed, and (b) even once working it was English-only, so a Hindi (or
any other non-English) answer could never actually be *heard* in the
language it was translated into.

Sarvam's Bulbul model needs no such manual approval — SARVAM_API_KEY (see
api/translation.py, already required and already configured for this
project) is sufficient — and it natively supports voice synthesis in 11 of
the languages api/translation.py already translates into (see
BULBUL_SUPPORTED_LANGUAGES below), so the answer can actually be spoken in
the user's selected language instead of always falling back to English.
Languages Sarvam translates but Bulbul cannot voice (e.g. Sanskrit,
Manipuri) fall back to skipping audio, not to a mispronounced or
wrong-language voice — same fail-open philosophy as api/translation.py.

Confirmed against Sarvam's own docs (docs.sarvam.ai/api-reference-docs/
text-to-speech/api/rest-api and .../models/bulbul, 2026-09): endpoint,
auth header, request/response shape, the 36 valid speaker names, and the
2500-character-per-request limit for bulbul:v3. Not verified against a
live call in this environment (SARVAM_API_KEY here is a real project key,
but no request was fired during this edit) — if a genuinely wrong field
name slipped through despite matching the docs, this fails open (returns
None, logs a warning) exactly like every other failure mode below, so a
demo never breaks on it; only real TTS silently doesn't play.

Usage:
    python -m api.tts "Section 3(p) excludes traditional knowledge from patentability." en-IN
    python -m api.tts "पारंपरिक ज्ञान" hi-IN
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

load_dotenv(override=True)

log = logging.getLogger(__name__)

SARVAM_API_KEY = os.getenv("SARVAM_API_KEY")
SARVAM_BASE_URL = "https://api.sarvam.ai"
SARVAM_TTS_MODEL = os.getenv("SARVAM_TTS_MODEL", "bulbul:v3")
# "shubh" (Sarvam's own documented default) rather than an arbitrary pick
# from the 36 available speakers — matches the model's own fallback choice
# rather than introducing a preference this project has no basis for.
SARVAM_TTS_SPEAKER = os.getenv("SARVAM_TTS_SPEAKER", "shubh")
TTS_TIMEOUT = 20.0
# bulbul:v3's documented hard limit is 2500 exactly; 2200 leaves margin the
# same way api/translation.py's SARVAM_MAX_CHARS=950 leaves margin under
# translate's 1000 — without meaningfully increasing chunk count for a
# typical answer length.
TTS_MAX_CHARS = int(os.getenv("TTS_MAX_CHARS", "2200"))

# The 11 languages Bulbul can voice, out of the larger set
# api/translation.py can translate into (TARGET_LANGUAGE_CODES) — a
# request for any other language_code skips audio rather than guessing.
BULBUL_SUPPORTED_LANGUAGES: frozenset[str] = frozenset({
    "en-IN", "hi-IN", "bn-IN", "gu-IN", "kn-IN", "ml-IN",
    "mr-IN", "od-IN", "pa-IN", "ta-IN", "te-IN",
})

_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(base_url=SARVAM_BASE_URL, timeout=TTS_TIMEOUT)
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
    Text-to-speech via Sarvam's Bulbul model, in `language` directly — pass
    the already-translated answer and its actual target language (not the
    pre-translation English), so what's spoken matches what's shown.
    Returns base64-encoded WAV audio, or None if synthesis isn't possible
    or fails for any reason — this never raises, matching
    translate_text()'s fail-open contract.
    """
    if not text.strip():
        return None
    if language not in BULBUL_SUPPORTED_LANGUAGES:
        # %r (not %s) on the caller-supplied value: repr() escapes newlines/
        # control characters, so an arbitrary `language` string can't forge
        # extra log lines (CWE-117) — this project's own log format has no
        # other structure a forged line could impersonate, but there's no
        # reason to pass user-controlled text into a log call unescaped.
        log.info(
            "TTS skipped: Bulbul does not support language_code=%r "
            "(supported: %s)",
            language, sorted(BULBUL_SUPPORTED_LANGUAGES),
        )
        return None
    if not SARVAM_API_KEY:
        log.warning("SARVAM_API_KEY not set — skipping TTS")
        return None

    chunks = split_text(text, TTS_MAX_CHARS)
    audio_chunks: list[bytes] = []

    try:
        client = _get_client()
        for chunk in chunks:
            response = await client.post(
                "/text-to-speech",
                headers={"api-subscription-key": SARVAM_API_KEY},
                json={
                    "text": chunk,
                    "language_code": language,
                    "speaker": SARVAM_TTS_SPEAKER,
                    "model": SARVAM_TTS_MODEL,
                },
            )
            response.raise_for_status()
            audios = response.json().get("audios") or []
            if not audios:
                raise ValueError(f"Sarvam TTS response had no audios: {response.json()!r}")
            for audio_b64 in audios:
                audio_chunks.append(base64.b64decode(audio_b64))
    except Exception as exc:
        log.warning("TTS synthesis failed: %s — returning no audio", exc)
        return None

    if not audio_chunks:
        return None

    combined = _concat_wav(audio_chunks)
    return base64.b64encode(combined).decode("ascii")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    args = sys.argv[1:]
    if not args:
        print('Usage: python -m api.tts "text to speak" [language_code]')
        sys.exit(1)

    text = args[0]
    language = args[1] if len(args) > 1 else "en-IN"

    result = asyncio.run(synthesize_speech(text, language))
    if result is None:
        print("No audio produced — check the log lines above for why.")
        sys.exit(1)

    out_path = "tts_output.wav"
    with open(out_path, "wb") as f:
        f.write(base64.b64decode(result))
    print(f"Wrote {out_path} ({len(result)} base64 chars)")
