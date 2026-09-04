"""
Graph state for IP-SAKTI.

A single TypedDict threaded through every node. LangGraph merges each node's
returned dict into this state, so a node only needs to return the keys it
actually changes.
"""

from __future__ import annotations

from typing import TypedDict


class GraphState(TypedDict, total=False):
    query: str
    history: list[dict]  # optional prior turns: [{"role": ..., "content": ...}]
    jurisdiction: str  # "india" or "international" — see ingestion.indexer.JURISDICTIONS
    formulation_category: str  # see graph.formulation.FORMULATION_CATEGORIES
    statutory_tags: list[str]  # see graph.formulation.CATEGORY_STATUTORY_TAGS
    needs_clarification: bool  # true when triage matched 2+ formulation categories
    clarifying_questions: list[str]  # informational — generation proceeds regardless, using the first-matched category
    rewritten_query: str
    candidates: list[dict]  # fused top-20, before reranking
    bm25_top_score: float  # top raw BM25 score from this round's retrieve() — diagnostic only
    reranked: list[dict]  # reranked top-5, rerank_score is a calibrated 0-1 confidence (see retrieval/reranker.py)
    confidence_score: float  # top reranked chunk's rerank_score, 0.0 if reranked is empty — exposed as QueryResponse.confidence_score
    answer: str
    citations: list[dict]
    flags: dict


# Callers must seed state with this, not {} — nodes only ever set flags they
# have a reason to change (e.g. retry_rewrite_query only runs on the retry
# path), so starting from {} means "retried" is simply absent from the
# response on the common no-retry path instead of present-and-False. That
# makes the API response shape inconsistent for callers checking flags["retried"].
#
# weak_grounding is set unconditionally by rerank_node on every path (unlike
# retried), but included here anyway so the key is never simply absent for a
# caller reading flags before rerank_node has run.
DEFAULT_FLAGS = {"abstained": False, "retried": False, "weak_grounding": False}

# Every caller seeding GraphState must set jurisdiction explicitly (see
# api/main.py) — this is only the fallback for direct/CLI callers that don't.
DEFAULT_JURISDICTION = "india"
