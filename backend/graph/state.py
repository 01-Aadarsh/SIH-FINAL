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
    rewritten_query: str
    candidates: list[dict]  # fused top-20, before reranking
    reranked: list[dict]  # reranked top-5
    answer: str
    citations: list[dict]
    flags: dict


# Callers must seed state with this, not {} — nodes only ever set flags they
# have a reason to change (e.g. retry_rewrite_query only runs on the retry
# path), so starting from {} means "retried" is simply absent from the
# response on the common no-retry path instead of present-and-False. That
# makes the API response shape inconsistent for callers checking flags["retried"].
DEFAULT_FLAGS = {"abstained": False, "retried": False}

# Every caller seeding GraphState must set jurisdiction explicitly (see
# api/main.py) — this is only the fallback for direct/CLI callers that don't.
DEFAULT_JURISDICTION = "india"
