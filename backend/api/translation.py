"""
Sarvam AI translation bridge for IP-SAKTI.

Retrieval and generation only ever operate on English (dense/sparse
retrieval and the grounded-generation prompt are tuned against English
statutory text — see generation/prompts.py). This module is what lets a
non-English question in and a non-English answer back out around that:
translate the incoming question to English before it reaches the graph,
translate the graph's English answer back to the user's language before it
leaves the process. See idea.md: Sarvam is the interim provider (working
access now); the PS names Bhashini specifically, so this gets swapped if a
Bhashini key arrives before the demo — this module is the only place that
swap touches.

Fails open, not closed: translation is a layer over an already-correct
English pipeline, not something the demo can die on. Any failure (timeout,
bad key, Sarvam outage, unexpected response shape) logs and returns the
original text untouched rather than raising — worst case the user sees
their answer in English instead of Hindi, not a 500.

Chunks past SARVAM_MAX_CHARS: Sarvam's `mayura:v1` model hard-rejects input
over exactly 1000 characters ("Input text must not exceed 1000 characters
for mayura:v1") — confirmed empirically against the live API, not from
docs. A real grounded answer (several sentences, sometimes a bulleted list
of Act sections) routinely exceeds that; translating one straight through
without chunking meant every realistically-sized answer 400'd and silently
fell back to English every time — reproduced directly, not hypothetical.
Chunks are translated concurrently and rejoined; if any chunk fails, the
whole call falls back to the original, unchunked text rather than
returning some sentences translated and others not.

Usage:
    python -m api.translation "traditional knowledge patent exclusion" hi-IN en-IN
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import sys

import httpx
from dotenv import load_dotenv

from api.text_chunking import split_text

load_dotenv(override=True)

log = logging.getLogger(__name__)

SARVAM_API_KEY = os.getenv("SARVAM_API_KEY")
SARVAM_BASE_URL = "https://api.sarvam.ai"
TRANSLATE_TIMEOUT = 10.0
# Sarvam's confirmed hard limit is 1000 exactly; 950 leaves margin without
# meaningfully increasing the chunk count for typical answer lengths.
SARVAM_MAX_CHARS = int(os.getenv("SARVAM_MAX_CHARS", "950"))

# Sarvam's documented language codes (docs.sarvam.ai/api-reference/text/translate-text,
# confirmed 2026-09). "auto" is source-only — you can't translate an answer
# *into* "auto" — so it's excluded from TARGET_LANGUAGE_CODES, which is also
# what api/main.py uses as QueryRequest.language's Pydantic Literal (that
# field is both the question's source language and the answer's target
# language, so it can never legitimately be "auto").
TARGET_LANGUAGE_CODES: tuple[str, ...] = (
    "en-IN", "hi-IN", "bn-IN", "gu-IN", "kn-IN", "ml-IN", "mr-IN", "od-IN",
    "pa-IN", "ta-IN", "te-IN", "as-IN", "brx-IN", "doi-IN", "kok-IN", "ks-IN",
    "mai-IN", "mni-IN", "ne-IN", "sa-IN", "sat-IN", "sd-IN", "ur-IN",
)
SOURCE_LANGUAGE_CODES: tuple[str, ...] = ("auto", *TARGET_LANGUAGE_CODES)

# Ayurvedic technical terms that must survive translation unchanged, not be
# transliterated into an approximate English gloss (e.g. Bhasma -> "ash",
# which loses the specific pharmaceutical meaning the corpus's Drugs &
# Cosmetics Act text actually uses). Maps every recognized surface form —
# common Latin-script spelling variants and Devanagari — to one canonical
# English term. Not exhaustive; the six terms named in the request plus
# Arishta (near-synonym of Asava, common enough alongside it to be worth
# including) and their most common alternate spellings.
PROTECTED_AYURVEDIC_TERMS: dict[str, str] = {
    "churna": "Churna", "churn": "Churna", "chura": "Churna", "चूर्ण": "Churna",
    "bhasma": "Bhasma", "bhasm": "Bhasma", "भस्म": "Bhasma",
    "taila": "Taila", "tail": "Taila", "तैल": "Taila", "तेल": "Taila",
    "kwath": "Kwatha", "kwatha": "Kwatha", "kashaya": "Kwatha", "काढा": "Kwatha", "क्वाथ": "Kwatha",
    "rasa shastra": "Rasa Shastra", "rasashastra": "Rasa Shastra",
    "रस शास्त्र": "Rasa Shastra", "रसशास्त्र": "Rasa Shastra",
    "asava": "Asava", "आसव": "Asava",
    "arishta": "Arishta", "अरिष्ट": "Arishta",
}

# Purely numeric, not "XPROTECTEDTERMX{index}X" as originally written — found
# to be a real bug, not a hypothetical, by capturing actual API output while
# writing docs/API_CONTRACT.md: an alphabetic placeholder reads as an
# unrecognized English word to Sarvam, which transliterated it into
# Devanagari instead of passing it through ("XPROTECTEDTERMX0X" came back as
# "एक्सप्रोटेक्टेडटेरएमएक्स0एक्स"), so the exact-string restore below never
# matched and the mangled placeholder leaked into a real user-facing answer.
# Confirmed by testing several formats against the live API: purely numeric
# placeholders survive verbatim (Sarvam recognizes them as numbers, not
# words). 9911...1199 wrapping is long and distinctive enough that it won't
# coincidentally collide with a real number already in the source text
# (section numbers, years, page numbers are all far shorter).
_PROTECT_PLACEHOLDER = "9911{index:04d}1199"


def _protect_terms(text: str) -> tuple[str, dict[str, str]]:
    """Swap every recognized Ayurvedic term for an opaque, translation-model-
    safe placeholder token, so Sarvam never actually sees the term and can't
    mistranslate or transliterate it. Longest terms first, so a multi-word
    entry (e.g. "rasa shastra") matches before its component single words
    could claim part of it. Returns the modified text and a placeholder ->
    canonical-term mapping for _restore_terms() to reverse afterward.
    """
    terms_longest_first = sorted(PROTECTED_AYURVEDIC_TERMS, key=len, reverse=True)
    mapping: dict[str, str] = {}
    counter = [0]

    for term in terms_longest_first:
        canonical = PROTECTED_AYURVEDIC_TERMS[term]
        pattern = re.compile(re.escape(term), re.IGNORECASE)

        def _replace(match: re.Match, canonical: str = canonical) -> str:
            placeholder = _PROTECT_PLACEHOLDER.format(index=counter[0])
            counter[0] += 1
            mapping[placeholder] = canonical
            return placeholder

        text = pattern.sub(_replace, text)

    return text, mapping


def _restore_terms(text: str, mapping: dict[str, str]) -> str:
    for placeholder, canonical in mapping.items():
        text = text.replace(placeholder, canonical)
    return text


_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(base_url=SARVAM_BASE_URL, timeout=TRANSLATE_TIMEOUT)
    return _client


async def _translate_chunk(
    client: httpx.AsyncClient, text: str, source_lang: str, target_lang: str
) -> str:
    """One Sarvam call, <= SARVAM_MAX_CHARS input. Raises on any failure —
    translate_text() is what catches and applies the fail-open contract."""
    response = await client.post(
        "/translate",
        headers={"api-subscription-key": SARVAM_API_KEY},
        json={
            "input": text,
            "source_language_code": source_lang,
            "target_language_code": target_lang,
            # speaker_gender is optional and only accepts "Male"/"Female" per
            # Sarvam's spec — omitted rather than guessed, since we have no
            # actual gender preference to supply and a bad enum value would
            # 400 every request.
        },
    )
    response.raise_for_status()
    translated = response.json().get("translated_text")
    if not translated:
        raise ValueError(f"Sarvam response had no translated_text: {response.json()!r}")
    return translated


async def translate_text(text: str, source_lang: str, target_lang: str) -> str:
    """
    Translate `text` from source_lang to target_lang via Sarvam AI.

    Returns the original text, unchanged, in two cases: source and target
    are the same language, or any part of the API call fails for any
    reason. The caller never needs a try/except — this function does not
    raise. See module docstring for why long text is chunked, and why a
    partial-chunk failure falls back to the *entire* original text rather
    than returning some sentences translated and others not.

    Deliberately does NOT special-case "source is English" as a shortcut —
    only source_lang == target_lang skips the API call entirely. This
    function runs in both directions (api/main.py's /query: request
    language -> en-IN before retrieval, then en-IN -> request language
    after generation), and a same-language check is the only rule that's
    correct both ways. An earlier version also skipped whenever
    source_lang.startswith("en"), which seemed like a harmless optimization
    for the first direction but silently broke the second one outright: the
    answer's source_lang is always "en-IN", so that rule would skip
    translating it to the user's language on every single request. Caught
    by testing an en-IN -> hi-IN call directly and seeing the Hindi text
    come back as unmodified English.
    """
    if not text.strip():
        return text
    if source_lang == target_lang:
        return text

    if not SARVAM_API_KEY:
        log.warning("SARVAM_API_KEY is not set — returning text untranslated")
        return text

    protected_text, term_mapping = _protect_terms(text)
    chunks = split_text(protected_text, SARVAM_MAX_CHARS)
    try:
        client = _get_client()
        translated_chunks = await asyncio.gather(
            *(_translate_chunk(client, chunk, source_lang, target_lang) for chunk in chunks)
        )
        return _restore_terms(" ".join(translated_chunks), term_mapping)
    except Exception as exc:
        log.warning(
            "Translation failed (%s -> %s, %d chunk(s)): %s — returning original text",
            source_lang, target_lang, len(chunks), exc,
        )
        return text


if __name__ == "__main__":
    # Windows consoles default to cp1252, which can't encode Devanagari and
    # other non-Latin scripts this module routinely prints.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    args = sys.argv[1:]
    if len(args) < 3:
        print('Usage: python -m api.translation "<text>" <source_lang> <target_lang>')
        print("e.g.:  python -m api.translation \"traditional knowledge\" en-IN hi-IN")
        sys.exit(1)

    text, source_lang, target_lang = args[0], args[1], args[2]
    result = asyncio.run(translate_text(text, source_lang, target_lang))
    print(f"\n{source_lang} -> {target_lang}")
    print(f"in:  {text}")
    print(f"out: {result}")
