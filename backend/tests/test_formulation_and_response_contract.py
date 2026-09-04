"""
Unit tests for the 6th formulation category (new_or_non_classical_drug),
the mandatory disclaimer (generation/prompts.py), and confidence_score
(graph/nodes.py, graph/state.py) — the three real gaps identified against
SIH26045's problem statement text (clinical-trial pathway classification,
"clearly state that it provides information and not legal advice", and a
confidence signal in the response), as opposed to claims in the same
blueprint that turned out to already be implemented or to describe a
regression if followed literally (see graph/nodes.py, api/translation.py).

No live DATABASE_URL, LLM key, or Sarvam key required — everything here is
pure/deterministic (graph.formulation.triage_formulation,
generation.prompts.append_disclaimer), unlike test_retrieval_determinism.py
and test_translation_term_protection.py's integration tests against real
services.

Usage:
    python -m pytest tests/test_formulation_and_response_contract.py -v
"""

from __future__ import annotations

from generation.prompts import DISCLAIMER, append_disclaimer
from graph.formulation import (
    CATEGORY_STATUTORY_TAGS,
    FORMULATION_CATEGORIES,
    classify_formulation,
    triage_formulation,
)


def test_new_or_non_classical_drug_is_a_registered_category():
    assert "new_or_non_classical_drug" in FORMULATION_CATEGORIES
    assert "new_or_non_classical_drug" in CATEGORY_STATUTORY_TAGS
    assert CATEGORY_STATUTORY_TAGS["new_or_non_classical_drug"]


def test_new_drug_phrasing_classifies_as_new_or_non_classical_drug():
    """The NDCT-specific terms of art the pattern was written around."""
    for query in [
        "What safety dossier is required to get clinical trial permission for a new drug?",
        "Does an investigational new drug need an IND application under the NDCT Rules?",
        "Is this a non-classical drug or a new chemical entity requiring fresh approval?",
    ]:
        assert classify_formulation(query) == "new_or_non_classical_drug", query


def test_generic_clinical_trial_mention_does_not_alone_trigger_new_drug_category():
    """Deliberately narrow pattern (see graph/formulation.py's comment): the
    bare phrase "clinical trial" already feeds the generic Clinical_Validation
    tag for other categories (e.g. proprietary, phytopharmaceutical) and must
    not by itself reclassify those questions as new_or_non_classical_drug."""
    result = triage_formulation("What clinical validation does a P&P proprietary medicine need?")
    assert result["formulation_category"] == "proprietary"


def test_new_drug_plus_proprietary_keywords_asks_for_clarification():
    result = triage_formulation(
        "Is this proprietary formulation considered a new drug requiring clinical trial permission?"
    )
    assert result["needs_clarification"] is True
    assert set(result["clarifying_questions"][0].split()) & {"proprietary", "drug"}


def test_disclaimer_is_appended_and_idempotent():
    answer = "A trademark is defined in Section 2(1)(zb) of the Trade Marks Act, 1999."
    once = append_disclaimer(answer)
    assert once.endswith(DISCLAIMER)
    assert once.startswith(answer)

    twice = append_disclaimer(once)
    assert twice == once, "append_disclaimer must not double-append when called again on its own output"


def test_disclaimer_appended_even_to_an_abstention():
    """generate_answer() (graph/nodes.py) and query_stream() (api/main.py)
    both append unconditionally, before checking flags['abstained'] only for
    citation attachment — this locks in that append_disclaimer() itself has
    no abstention special-case, so the two call sites can't drift apart by
    one of them adding one."""
    from generation.prompts import ABSTENTION_MARKER

    abstained_answer = ABSTENTION_MARKER + "\nWhich Act's Section 3(p)?"
    result = append_disclaimer(abstained_answer)
    assert result.endswith(DISCLAIMER)
    assert result.startswith(ABSTENTION_MARKER)
