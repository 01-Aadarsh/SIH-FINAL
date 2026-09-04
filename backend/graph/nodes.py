"""
Graph nodes for IP-SAKTI.

Each node takes the running GraphState and returns a dict of only the keys
it changes — LangGraph merges that into the state. The real logic already
lives in retrieval/ and generation/; nodes just call into it, they don't
reimplement it.

Nodes that do I/O (LLM calls, pgvector queries) are async, awaited via
compiled_graph.ainvoke() in api/main.py — not run_in_threadpool. Nodes that
are pure CPU-bound logic with no I/O (should_retry, attach_citations_node)
stay sync; LangGraph runs sync and async nodes side by side in the same
graph without issue.
"""

from __future__ import annotations

import asyncio
import logging

from generation.citation import attach_citations, is_abstention
from generation.llm_client import acomplete, agenerate
from generation.prompts import append_disclaimer
from graph.formulation import CATEGORY_STATUTORY_TAGS, triage_formulation
from graph.state import DEFAULT_FLAGS, DEFAULT_JURISDICTION, GraphState
from retrieval.bm25_search import search as bm25_search_sync
from retrieval.dense_search import search as dense_search
from retrieval.fusion import fuse
from retrieval.reranker import rerank as rerank_sync

log = logging.getLogger(__name__)

# 40, not 20: raised after finding a real regression from
# HierarchicalStatutoryChunker, not a hypothetical one. Clause-level chunks
# are shorter and lexically sparser than the old ~2000-char sliding-window
# chunks, so a single relevant clause can rank well individually on BM25 or
# dense (e.g. Patents_Act_1970's Section 3(p) clause: BM25 rank 59, dense
# rank 12, both for the literal query "What does Section 3(p) say about
# traditional knowledge?" — a query that worked reliably all session before
# this chunker existed) but still miss RRF's combined top-20 cutoff, since
# RRF rewards a chunk that scores decently on *both* signals over one that
# scores well on only one. Confirmed empirically: at 20, the model saw only
# generic background chunks and correctly-per-its-context abstained
# ("which act's Section 3(p)?"); at 40, the actual Section 3(p) passage
# (Patent_Office_Manual_Practice_Procedure_2011) becomes the top candidate
# at 0.997 confidence. 40 was chosen as the smallest tested value that
# fixed this specific regression — not pushed further, since a similar
# investigation for "What is a trademark?" (a query that was already
# unreliable before this chunker, not something this chunker broke) showed
# that query's actual definitional clause needs top-~500 pooling to
# reach, and doubling every query's reranking cost to chase one already-
# marginal query isn't a trade worth making — see
# tests/test_retrieval_determinism.py's module docstring.
FUSED_TOP_K = 40
RERANK_TOP_K = 5

# rerank_score is now a calibrated sigmoid(raw_logit) in [0, 1] — see
# retrieval/reranker.py's module docstring for the empirical spread this was
# set against (on-topic queries: 0.92-0.999; queries with no real answer in
# this corpus: ~0.000). 0.15 sits well above the "nothing here" cluster.
RERANK_SCORE_THRESHOLD = 0.15

# A separate, lower bar for the weak-grounding diagnostic below — distinct
# from RERANK_SCORE_THRESHOLD (which decides whether to retry), this one
# flags a specific, more informative situation: BM25 found what looks like
# a real lexical hit (raw score above BM25_STRONG_MATCH), but the
# cross-encoder still isn't confident. That combination means "probably
# retrieval's fault, not the corpus's" — worth surfacing to a caller
# distinctly from a generic abstention, per the fallback-diagnostic ask.
BM25_STRONG_MATCH = 8.0
WEAK_GROUNDING_CONFIDENCE = 0.10


async def rewrite_query(state: GraphState) -> dict:
    """Standalone-question rewrite from chat history.

    A no-op without history, so a single-turn query passes through
    unchanged end to end — this keeps single-turn graph output identical
    to calling retrieval/generation directly.
    """
    history = state.get("history") or []
    query = state["query"]

    if not history:
        return {"rewritten_query": query}

    transcript = "\n".join(f"{turn['role']}: {turn['content']}" for turn in history)
    prompt = (
        "Rewrite the latest user question as a standalone question that "
        "makes sense without the prior conversation below. Keep it short. "
        "Output only the rewritten question, nothing else.\n\n"
        f"{transcript}\nuser: {query}"
    )
    rewritten = await acomplete(prompt)
    return {"rewritten_query": rewritten or query}


def triage_formulation_node(state: GraphState) -> dict:
    """Deterministic keyword classification — no I/O, no LLM call. Runs
    after rewrite_query (so it sees the standalone-question form) and
    before retrieve. See graph/formulation.py for why this is intentionally
    not LLM-based, and for needs_clarification/clarifying_questions: set
    when the question's keywords span 2+ formulation categories, surfaced
    to the caller but not blocking — generation proceeds using the
    first-matched category regardless."""
    query = state.get("rewritten_query") or state["query"]
    triage = triage_formulation(query)
    return {
        "formulation_category": triage["formulation_category"],
        "statutory_tags": CATEGORY_STATUTORY_TAGS[triage["formulation_category"]],
        "needs_clarification": triage["needs_clarification"],
        "clarifying_questions": triage["clarifying_questions"],
    }


async def retrieve(state: GraphState) -> dict:
    """Hybrid retrieval (BM25 + dense, fused by RRF) on the current query,
    scoped to state["jurisdiction"]. BM25 has no I/O (in-memory index), so
    it's only offloaded to a thread to avoid blocking the event loop; dense
    search and the fusion metadata backfill hit pgvector and are natively
    async all the way down.
    """
    query = state["rewritten_query"]
    jurisdiction = state.get("jurisdiction") or DEFAULT_JURISDICTION

    bm25_results = await asyncio.to_thread(bm25_search_sync, query, top_k=FUSED_TOP_K, jurisdiction=jurisdiction)
    dense_results = await dense_search(query, top_k=FUSED_TOP_K, jurisdiction=jurisdiction)
    candidates = await fuse(
        bm25_results, dense_results, top_k=FUSED_TOP_K, jurisdiction=jurisdiction
    )
    # Carried into rerank_node purely for the weak-grounding diagnostic
    # below — not used for ranking or filtering.
    bm25_top_score = max((r["score"] for r in bm25_results), default=0.0)
    return {"candidates": candidates, "bm25_top_score": bm25_top_score}


async def rerank_node(state: GraphState) -> dict:
    query = state["rewritten_query"]
    reranked = await asyncio.to_thread(rerank_sync, query, state["candidates"], top_k=RERANK_TOP_K)

    flags = dict(state.get("flags") or {})
    bm25_top_score = state.get("bm25_top_score", 0.0)
    top_confidence = reranked[0]["rerank_score"] if reranked else 0.0
    flags["weak_grounding"] = bm25_top_score >= BM25_STRONG_MATCH and top_confidence < WEAK_GROUNDING_CONFIDENCE
    if flags["weak_grounding"]:
        log.warning(
            "Weak grounding: BM25 found a strong lexical match (score %.1f) but the "
            "cross-encoder's top confidence is only %.3f — likely a retrieval/reranking "
            "gap rather than the corpus genuinely lacking an answer.",
            bm25_top_score, top_confidence,
        )

    # Exposed to the API response as QueryResponse.confidence_score — the
    # same calibrated sigmoid(raw_logit) that decides should_retry, not a
    # separately-invented number. 0.0 (not missing) when reranked is empty,
    # so callers never need a None check.
    return {"reranked": reranked, "flags": flags, "confidence_score": top_confidence}


def should_retry(state: GraphState) -> str:
    """Conditional edge after reranking.

    Retries retrieval once, and only once, if the top rerank score looks
    weak. flags["retried"] guards against a second retry — once set, this
    always routes to "generate" regardless of score, so the branch is
    bounded, never a loop.
    """
    flags = state.get("flags") or {}
    if flags.get("retried"):
        return "generate"

    reranked = state.get("reranked") or []
    if not reranked or reranked[0]["rerank_score"] < RERANK_SCORE_THRESHOLD:
        log.info(
            "Top rerank score %.3f below threshold %.1f — retrying retrieval once",
            reranked[0]["rerank_score"] if reranked else float("-inf"),
            RERANK_SCORE_THRESHOLD,
        )
        return "retry"
    return "generate"


async def retry_rewrite_query(state: GraphState) -> dict:
    """Only reached on the bounded retry path: ask the LLM to rephrase the
    query differently, in case the original phrasing just didn't match the
    corpus well, then mark flags["retried"] so should_retry can't loop again.
    """
    original = state["rewritten_query"]
    prompt = (
        "This search query returned weak results from a document search "
        "system: " + original + "\n\n"
        "Rephrase it as a different, more specific search query that might "
        "match better. Output only the rephrased query, nothing else."
    )
    rephrased = await acomplete(prompt)

    flags = dict(state.get("flags") or {})
    flags["retried"] = True
    return {"rewritten_query": rephrased or original, "flags": flags}


async def generate_answer(state: GraphState) -> dict:
    query = state["rewritten_query"]
    answer = await agenerate(
        query,
        state["reranked"],
        formulation_category=state.get("formulation_category"),
        statutory_tags=state.get("statutory_tags"),
    )

    flags = dict(state.get("flags") or {})
    # is_abstention() must run on the model's raw output, before the
    # disclaimer is appended — the disclaimer text doesn't start with
    # ABSTENTION_MARKER, so appending first would just be harmless, but
    # checking the raw answer first is the more obviously-correct order and
    # doesn't depend on that being true forever.
    flags["abstained"] = is_abstention(answer)
    return {"answer": append_disclaimer(answer), "flags": flags}


def attach_citations_node(state: GraphState) -> dict:
    """No sources on an abstention — nothing was actually used to answer."""
    if (state.get("flags") or {}).get("abstained"):
        return {"citations": []}
    return {"citations": attach_citations(state["reranked"])}


async def run_retrieval_stage(rewritten_query: str, jurisdiction: str) -> GraphState:
    """triage -> retrieve -> rerank -> bounded retry, composed from the same
    node functions the compiled graph uses for this exact sequence (see
    build_graph.py) — not a reimplementation of the retry threshold logic.

    Exists for api/main.py's SSE streaming endpoint. LangGraph nodes return
    a full state dict on completion; there's no clean way to have the graph
    itself yield partial output mid-node, and token streaming only matters
    for generation, not retrieval. So the streaming endpoint runs this
    helper directly, then streams generate_answer's underlying call itself,
    instead of going through compiled_graph.ainvoke() for the whole
    pipeline. The non-streaming /query endpoint still uses the real
    compiled graph end to end; this helper's output is identical to what
    that graph produces up through reranking, by construction.

    Returns the full working state (reranked, flags, formulation_category,
    statutory_tags, needs_clarification, clarifying_questions) rather than a
    positional tuple — that tuple grew twice already as fields were added
    (weak_grounding, then clarification) and a dict-like state is what
    every node here already produces and consumes, so the caller (api/
    main.py) reads named keys the same way a node would.
    """
    state: GraphState = {
        "rewritten_query": rewritten_query,
        "jurisdiction": jurisdiction,
        "flags": dict(DEFAULT_FLAGS),
    }
    state.update(triage_formulation_node(state))
    state.update(await retrieve(state))
    state.update(await rerank_node(state))

    if should_retry(state) == "retry":
        state.update(await retry_rewrite_query(state))
        state.update(await retrieve(state))
        state.update(await rerank_node(state))

    return state
