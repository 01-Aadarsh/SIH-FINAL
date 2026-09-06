"""
Contract test for docs/API_CONTRACT.md.

Not a test of behavior — a test that the *documented* field set actually
matches the real Pydantic models backend/api/main.py serves. Exists because
this doc has gone stale before without anyone noticing until a teammate
building against it got confused: it predated /query/stream entirely and
was missing 7 request/response fields (jurisdiction, language,
synthesize_audio, formulation_category, needs_clarification,
clarifying_questions, audio_base64, flags.weak_grounding) by the time it
was rewritten. This test won't catch every kind of doc drift (wrong
example values, stale timing numbers), but it will catch the most damaging
kind: a field silently added or removed from the actual API that the doc
doesn't mention, which is exactly what happened last time.

When this test fails: a field was added/removed/renamed in QueryRequest,
QueryResponse, or Flags. Update docs/API_CONTRACT.md's field tables to
match, then update DOCUMENTED_* below to match the doc.

Usage:
    python -m pytest tests/test_api_contract.py -v
"""

from __future__ import annotations

from api.main import Flags, QueryRequest, QueryResponse

# Keep this in sync with docs/API_CONTRACT.md's "Request body" /
# "Response" field tables by hand — this is the one place both the doc and
# this test both have to agree with, so a mismatch is caught here instead
# of silently in a teammate's frontend code.
DOCUMENTED_REQUEST_FIELDS = {"question", "history", "jurisdiction", "language", "synthesize_audio"}
DOCUMENTED_RESPONSE_FIELDS = {
    "answer", "citations", "flags", "formulation_category", "confidence_score",
    "needs_clarification", "clarifying_questions", "audio_base64", "related_provisions",
    "actionable_forms", "compliance_flags",
}
DOCUMENTED_FLAGS_FIELDS = {"abstained", "retried", "weak_grounding"}


def test_query_request_fields_match_documented_contract():
    actual = set(QueryRequest.model_fields.keys())
    assert actual == DOCUMENTED_REQUEST_FIELDS, (
        f"QueryRequest fields changed and docs/API_CONTRACT.md wasn't updated to "
        f"match. In code but not documented: {actual - DOCUMENTED_REQUEST_FIELDS}. "
        f"Documented but not in code: {DOCUMENTED_REQUEST_FIELDS - actual}."
    )


def test_query_response_fields_match_documented_contract():
    actual = set(QueryResponse.model_fields.keys())
    assert actual == DOCUMENTED_RESPONSE_FIELDS, (
        f"QueryResponse fields changed and docs/API_CONTRACT.md wasn't updated to "
        f"match. In code but not documented: {actual - DOCUMENTED_RESPONSE_FIELDS}. "
        f"Documented but not in code: {DOCUMENTED_RESPONSE_FIELDS - actual}."
    )


def test_flags_fields_match_documented_contract():
    actual = set(Flags.model_fields.keys())
    assert actual == DOCUMENTED_FLAGS_FIELDS, (
        f"Flags fields changed and docs/API_CONTRACT.md wasn't updated to match. "
        f"In code but not documented: {actual - DOCUMENTED_FLAGS_FIELDS}. "
        f"Documented but not in code: {DOCUMENTED_FLAGS_FIELDS - actual}."
    )


def test_language_default_is_english_for_backward_compatibility():
    """A frontend that never sends `language` must keep working exactly as
    before this field existed — documented explicitly in API_CONTRACT.md's
    request field table."""
    assert QueryRequest.model_fields["language"].default == "en-IN"


def test_jurisdiction_default_is_india():
    assert QueryRequest.model_fields["jurisdiction"].default == "india"
