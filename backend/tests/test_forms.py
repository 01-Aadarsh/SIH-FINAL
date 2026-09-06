"""
Tests for the Form & Registry Navigator (compliance/form_navigator.py).

Pure/deterministic — no DB, no LLM, no network — same category as
tests/test_formulation_and_response_contract.py.

Usage:
    python -m pytest tests/test_forms.py -v
"""

from __future__ import annotations

import pytest

from compliance.form_navigator import FORM_CATALOG, match_forms


def test_every_catalog_entry_has_the_documented_fields():
    """The exact fields the feature spec asks a caller to see — a missing
    one here would silently break FormCard's Pydantic validation in
    api/main.py, so catch it at the data layer instead."""
    required = {
        "form_id", "agency", "jurisdiction", "title", "statutory_mandate",
        "submission_portal", "required_attachments", "deadline", "keywords",
    }
    for form in FORM_CATALOG:
        missing = required - form.keys()
        assert not missing, f"{form.get('form_id')} missing fields: {missing}"
        assert form["required_attachments"], f"{form['form_id']} has no required_attachments"
        assert form["submission_portal"].startswith("https://"), form["form_id"]


def test_form_ids_are_unique():
    ids = [f["form_id"] for f in FORM_CATALOG]
    assert len(ids) == len(set(ids)), f"duplicate form_id(s) in catalog: {ids}"


def test_international_jurisdiction_returns_empty():
    """Every catalog entry is a domestic Indian registry — abstain rather
    than guess at an international equivalent, same rule this project
    applies everywhere else an international counterpart doesn't exist."""
    assert match_forms("patent my ayurvedic formulation", jurisdiction="international") == []


def test_patent_intent_surfaces_nba_form_7_not_a_wrong_number():
    """The flagship correction this module makes: applying for IPR/patents
    based on Indian biological resources is real NBA Form 7 (verified
    against data/BD_Rules_2024.pdf Rule 16(1)(a)), not "Form 3" as an
    earlier, uncorrected version of this catalog would have said. This
    locks in the correct number so a future edit can't silently revert it."""
    results = match_forms("I want to patent my Ayurvedic formulation")
    form_ids = [r["form_id"] for r in results]
    assert "NBA_FORM_7" in form_ids, f"expected NBA_FORM_7, got: {form_ids}"

    nba_form_7 = next(r for r in results if r["form_id"] == "NBA_FORM_7")
    assert "6" in nba_form_7["statutory_mandate"], nba_form_7["statutory_mandate"]
    assert nba_form_7["submission_portal"] == "https://absefiling.nic.in"


def test_commercial_access_intent_surfaces_form_2_not_form_1():
    """Second correction: NBA access for COMMERCIAL utilisation is real
    Form 2 (Rule 13(1)), not "Form 1" — Form 1 is the research/bio-survey
    variant of the same rule."""
    results = match_forms("apply for commercial utilization of a biological resource")
    form_ids = [r["form_id"] for r in results]
    assert "NBA_FORM_2" in form_ids, f"expected NBA_FORM_2, got: {form_ids}"
    assert "NBA_FORM_1" not in form_ids


def test_early_publication_and_expedited_examination_are_separate_forms():
    """Third correction: these are two distinct real IPO forms (Form 9,
    Rule 24A vs. Form 18A, Rule 24C), not one "Form 8" as originally
    specified — Form 8 is actually the unrelated "mention of inventor"
    form (Section 28)."""
    early_pub = match_forms("I want early publication of my patent application")
    assert any(r["form_id"] == "IPO_FORM_9" for r in early_pub)

    expedited = match_forms("how do I get expedited examination for my patent")
    assert any(r["form_id"] == "IPO_FORM_18A" for r in expedited)


def test_classical_formulation_category_does_not_over_trigger_sbb_form():
    """Regression test for a real false positive found by
    scripts/evaluate_pipeline.py: NBA_FORM_8 was previously linked to
    formulation_category "classical", but "classical" is the *default
    fallback* category for nearly any Ayurveda IP question (see
    graph/formulation.py), so that form was attaching to almost every
    answer regardless of relevance -- including ones with nothing to do
    with the Section 7 community/SBB route. NBA_FORM_8 must NOT fire on
    formulation_category alone; only on its own keywords or the real
    per-chunk statutory_tags (see test_statutory_tags_are_an_additive_signal
    for the tag-based path, which is unaffected by this fix)."""
    results = match_forms("what IP protection applies", formulation_category="classical")
    assert not any(r["form_id"] == "NBA_FORM_8" for r in results)


def test_sbb_exemption_keyword_still_surfaces_nba_form_8():
    """The precise signal NBA_FORM_8 should still fire on, after removing
    the over-broad formulation_category link above."""
    results = match_forms("what is the vaids and hakims SBB exemption")
    assert any(r["form_id"] == "NBA_FORM_8" for r in results)


def test_statutory_tags_are_an_additive_signal():
    results = match_forms("", statutory_tags=["BDA_Sec6_NBA_Approval"])
    assert any(r["form_id"] == "NBA_FORM_7" for r in results)


def test_no_match_returns_empty_not_error():
    assert match_forms("what is the weather today") == []


@pytest.mark.asyncio
async def test_run_query_attaches_no_forms_on_a_real_abstention():
    """Integration-level regression test, against the real live pipeline
    (real DB, real LLM) rather than a mock -- same "no mocks against the
    live pipeline" standard as tests/test_retrieval_determinism.py. This
    is the actual bug scripts/evaluate_pipeline.py's benchmark run found:
    a completely out-of-scope question ("what is the capital of France?")
    was getting NBA_FORM_8 attached to its abstention. Guards against
    fixing this in one call site and regressing it in the other
    (run_query() vs. the /query/stream generator both compute
    actionable_forms independently)."""
    from api.main import run_query

    response = await run_query(
        question="What is the capital of France?",
        history=[],
        jurisdiction="india",
        language="en-IN",
        synthesize_audio=False,
    )
    assert response.flags.abstained is True
    assert response.actionable_forms == []


def test_returned_cards_never_leak_match_only_fields():
    """`keywords`, `formulation_categories`, `statutory_tags` are matching
    machinery, not part of what a caller should see."""
    results = match_forms("patent my ayurvedic formulation")
    assert results
    for card in results:
        assert "keywords" not in card
        assert "formulation_categories" not in card
        assert "statutory_tags" not in card
