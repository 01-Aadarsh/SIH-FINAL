"""
Sentence-boundary text chunking, shared by api/translation.py and api/tts.py.

Both Sarvam's translate endpoint (1000 chars, confirmed empirically — see
translation.py) and Groq's TTS endpoint (~200 chars per Groq's docs, not
independently confirmed — see tts.py) reject input past a hard character
limit, and real grounded answers routinely exceed either. Same algorithm
both places: split at sentence boundaries first, only hard-splitting the
rare single sentence that's already longer than the limit on its own.
"""

from __future__ import annotations

import re


def split_text(text: str, max_chars: int) -> list[str]:
    """Split text into pieces <= max_chars, breaking at sentence boundaries
    where possible."""
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    chunks: list[str] = []
    current = ""

    for sentence in sentences:
        if not sentence:
            continue
        candidate = f"{current} {sentence}".strip() if current else sentence
        if len(candidate) <= max_chars:
            current = candidate
            continue

        if current:
            chunks.append(current)
            current = ""

        if len(sentence) > max_chars:
            for i in range(0, len(sentence), max_chars):
                chunks.append(sentence[i : i + max_chars])
        else:
            current = sentence

    if current:
        chunks.append(current)
    return chunks
