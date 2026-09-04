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
from graph.state import DEFAULT_FLAGS, DEFAULT_JURISDICTION, GraphState
from retrieval.bm25_search import search as bm25_search_sync
from retrieval.dense_search import search as dense_search
from retrieval.fusion import fuse
from retrieval.reranker import rerank as rerank_sync

log = logging.getLogger(__name__)

FUSED_TOP_K = 20
RERANK_TOP_K = 5

# ms-marco cross-encoder outputs raw logits (observed range roughly -3 to
# +9 on this corpus): positive scores tracked genuinely relevant chunks in
# testing, negative scores tracked irrelevant ones. 0.0 is a reasonable
# starting cutoff, not a rigorously tuned one — revisit if the retry fires
# too often or too rarely in practice.
RERANK_SCORE_THRESHOLD = 0.0


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


async def retrieve(state: GraphState) -> dict:
    """Hybrid retrieval (BM25 + dense, fused by RRF) on the current query,
    scoped to state["jurisdiction"]. BM25 has no I/O (in-memory index), so
    it's only offloaded to a thread to avoid blocking the event loop; dense
    search and the fusion metadata backfill hit pgvector and are natively
    async all the way down.
    """
    query = state["rewritten_query"]
    jurisdiction = state.get("jurisdiction") or DEFAULT_JURISDICTION

    bm25_results = await asyncio.to_thread(bm25_search_sync, query, top_k=FUSED_TOP_K)
    dense_results = await dense_search(query, top_k=FUSED_TOP_K, jurisdiction=jurisdiction)
    candidates = await fuse(
        bm25_results, dense_results, top_k=FUSED_TOP_K, jurisdiction=jurisdiction
    )
    return {"candidates": candidates}


async def rerank_node(state: GraphState) -> dict:
    query = state["rewritten_query"]
    reranked = await asyncio.to_thread(rerank_sync, query, state["candidates"], top_k=RERANK_TOP_K)
    return {"reranked": reranked}


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
    answer = await agenerate(query, state["reranked"])

    flags = dict(state.get("flags") or {})
    flags["abstained"] = is_abstention(answer)
    return {"answer": answer, "flags": flags}


def attach_citations_node(state: GraphState) -> dict:
    """No sources on an abstention — nothing was actually used to answer."""
    if (state.get("flags") or {}).get("abstained"):
        return {"citations": []}
    return {"citations": attach_citations(state["reranked"])}


async def run_retrieval_stage(rewritten_query: str, jurisdiction: str) -> tuple[list[dict], bool]:
    """retrieve -> rerank -> bounded retry, composed from the same node
    functions the compiled graph uses for this exact sequence (see
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
    """
    state: GraphState = {
        "rewritten_query": rewritten_query,
        "jurisdiction": jurisdiction,
        "flags": dict(DEFAULT_FLAGS),
    }
    state.update(await retrieve(state))
    state.update(await rerank_node(state))

    if should_retry(state) == "retry":
        state.update(await retry_rewrite_query(state))
        state.update(await retrieve(state))
        state.update(await rerank_node(state))

    return state["reranked"], state["flags"]["retried"]
