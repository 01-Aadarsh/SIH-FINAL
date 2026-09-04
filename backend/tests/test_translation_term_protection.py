"""
Regression test for a real bug found while capturing live API output for
docs/API_CONTRACT.md, not from theory: api/translation.py's protected-term
placeholder used to be alphabetic ("XPROTECTEDTERMX{index}X"), which Sarvam
read as an unrecognized English word and transliterated into Devanagari
instead of passing through untouched. The exact-string restore in
_restore_terms() then never matched, and the mangled placeholder
("एक्सप्रोटेक्टेडटेरएमएक्स0एक्स") leaked into a real, user-facing answer —
reproduced with `python -m api.translation`, not assumed. Fixed by switching
to a purely numeric placeholder (confirmed against the live API: numbers
survive Sarvam's translation verbatim, words don't). This test is what
would catch the placeholder format regressing back to something
word-shaped.

Requires SARVAM_API_KEY — this is an integration test against the real
Sarvam API, not a mock, on purpose: the bug this guards against was only
visible against real translation behavior, which a mock can't reproduce.

Usage:
    python -m pytest tests/test_translation_term_protection.py -v
"""

from __future__ import annotations

import pytest

from api.translation import translate_text


@pytest.mark.asyncio
async def test_protected_term_survives_translation_in_a_full_sentence():
    """The exact scenario that failed: a longer, natural sentence (not a
    short isolated test string) containing a protected term, translated
    en-IN -> hi-IN. Must contain the term's canonical spelling, and must
    NOT contain any fragment of a mangled placeholder."""
    text = (
        "Could you specify the specific regulatory provision, textbook, or "
        "schedule with the recommended adult dosage for Bhasma?"
    )
    result = await translate_text(text, "en-IN", "hi-IN")

    assert "Bhasma" in result, f"Protected term did not survive translation: {result!r}"
    mangled_markers = ["एक्स", "PROTECTEDTERM", "X0X"]
    for marker in mangled_markers:
        assert marker not in result, (
            f"Found a mangled placeholder fragment {marker!r} in the translated "
            f"output — the placeholder leaked instead of being restored: {result!r}"
        )


@pytest.mark.asyncio
async def test_multiple_protected_terms_in_one_sentence():
    result = await translate_text(
        "What is the correct dosage for Bhasma and Churna preparations?",
        "en-IN", "hi-IN",
    )
    assert "Bhasma" in result
    assert "Churna" in result


@pytest.mark.asyncio
async def test_devanagari_input_resolves_to_canonical_english_term():
    """A user typing the term directly in Devanagari script (not the
    Latin-script spelling) must still resolve to the canonical English term
    on the way back to English — not a generic gloss like "ash", and not
    left as untranslated Devanagari."""
    result = await translate_text("भस्म की सही खुराक क्या है?", "hi-IN", "en-IN")
    assert "Bhasma" in result
