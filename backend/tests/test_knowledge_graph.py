"""
Tests for the knowledge-graph enrichment layer (graph_kg/), the first real
slice of the PS's suggested "stage 2" — see graph_kg/build_kg.py and
graph_kg/kg.py's module docstrings for the design rationale.

Requires the knowledge graph to already be built (`python -m
graph_kg.build_kg`, run once against the live corpus) for the tests that
check real content — same precondition test_retrieval_determinism.py has
on the corpus itself being indexed. The pure-logic tests (empty input,
unknown tag) don't depend on that and would still pass even against a
missing graph file, since graph_kg.kg is designed to degrade to [] rather
than raise either way.

Usage:
    python -m pytest tests/test_knowledge_graph.py -v
"""

from __future__ import annotations

import pytest

from graph.nodes import expand_related_provisions_node, rerank_node, retrieve
from graph_kg.kg import KG_PATH, related_provisions_for


def test_knowledge_graph_file_exists():
    """Precondition for every other test in this file — if this fails, run
    `python -m graph_kg.build_kg` first (see that module's docstring)."""
    assert KG_PATH.exists(), (
        f"{KG_PATH} not found — run `python -m graph_kg.build_kg` against "
        f"the live corpus before running these tests."
    )


def test_empty_input_returns_empty_not_error():
    assert related_provisions_for([]) == []


def test_unknown_tag_returns_empty_not_error():
    """A tag that doesn't exist in the graph (typo, stale reference, or a
    genuinely new chunker.py rule not yet rebuilt into the graph) must
    degrade silently, matching this module's fail-open contract — same
    philosophy as api/translation.py and api/tts.py."""
    assert related_provisions_for(["NotARealStatutoryTag_XYZ"]) == []


def test_domestic_tk_tag_surfaces_real_international_counterpart():
    """The flagship case this whole module exists for: a domestic
    Section 3(p) tag should point at the WIPO GRATK Treaty's real,
    separately-indexed TK/disclosure provisions — a genuine cross-
    jurisdiction pointer, not a fabricated one (see
    graph_kg/build_kg.py::CROSS_JURISDICTION_PAIRS)."""
    results = related_provisions_for(["Patents_Act_Sec3p"])
    assert results, "expected at least one related provision for Patents_Act_Sec3p"

    international_hits = [r for r in results if r["jurisdiction"] == "international"]
    assert international_hits, f"expected an international counterpart, got: {results}"

    for r in international_hits:
        assert r["relation"] == "cross_jurisdiction_counterpart"
        assert r["source_file"], "related provision must point at a real source file"
        assert r["tag"] != "Patents_Act_Sec3p", "must not point back at its own input tag"


def test_related_provisions_are_capped_and_deduped():
    """Even a tag with many graph neighbors returns a short, deduped
    list — MAX_RELATED, not the whole neighborhood — and never repeats
    the same related tag twice."""
    results = related_provisions_for(["Patents_Act_Sec3p", "TKDL"])
    tags_seen = [r["tag"] for r in results]
    assert len(tags_seen) == len(set(tags_seen)), f"duplicate related tags: {tags_seen}"
    assert len(results) <= 5


@pytest.mark.asyncio
async def test_expand_related_provisions_node_end_to_end():
    """Same real query as test_retrieval_determinism.py's flagship
    regression test, run one stage further: after real retrieval+rerank
    against the live india-jurisdiction corpus, the knowledge-graph node
    should surface a real international pointer — proving the full
    retrieve -> rerank -> expand_related_provisions chain works together,
    not just the KG lookup in isolation."""
    state = {
        "rewritten_query": "What does Section 3(p) say about traditional knowledge?",
        "jurisdiction": "india",
        "flags": {"abstained": False},
    }
    state.update(await retrieve(state))
    state.update(await rerank_node(state))

    result = expand_related_provisions_node(state)
    assert "related_provisions" in result
    assert any(
        r["jurisdiction"] == "international" for r in result["related_provisions"]
    ), f"expected an international pointer, got: {result['related_provisions']}"


def test_expand_related_provisions_node_returns_empty_on_abstention():
    """No sources on an abstention, same rule as attach_citations_node —
    nothing was actually used to answer, so nothing to point onward from."""
    state = {"reranked": [{"statutory_tags": ["Patents_Act_Sec3p"]}], "flags": {"abstained": True}}
    result = expand_related_provisions_node(state)
    assert result == {"related_provisions": []}
