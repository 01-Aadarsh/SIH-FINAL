"""
Cross-encoder reranking for IP-SAKTI.

BM25 and dense retrieval each rank chunks by comparing separate embeddings
or token overlap; a cross-encoder reads the query and a chunk together in one
forward pass, which is more accurate for real relevance. This is the last
filter before chunks reach the LLM, so it decides what the model is even
allowed to answer from.

Score calibration: ms-marco-MiniLM outputs raw, unbounded logits (observed
range roughly -11 to +7 on this corpus — see the empirical spread below),
not a probability. rerank_score is now sigmoid(raw_logit), a 0-1 confidence
score comparable across queries, which raw logits are not (a "-3.5" means
something different depending on how spread out that particular query's
candidate scores happen to be). Empirical spread, five real queries against
the live corpus, top-3 each: on-topic queries the corpus genuinely covers
(traditional knowledge / Section 3(p), geographical indications) scored
sigmoid 0.92-0.999; queries with no real answer in this corpus (capital of
France, baking a cake) scored sigmoid ~0.000. RERANK_SCORE_THRESHOLD = 0.15
sits well above the "clearly nothing here" cluster and well below the
"clearly answered" cluster — calibrated against that small, real spread,
not against a rigorous labeled precision/recall study across the full
corpus, which this doesn't claim to be.

Usage:
    python -m retrieval.reranker "Section 3(p) traditional knowledge"
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import sys

from dotenv import load_dotenv
from sentence_transformers import CrossEncoder

from retrieval.fusion import search as fused_search

load_dotenv(override=True)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

RERANKER_MODEL = os.getenv("RERANKER_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2")

_model: CrossEncoder | None = None


def _get_model() -> CrossEncoder:
    """Load and cache the cross-encoder — loaded once, reused across calls."""
    global _model
    if _model is None:
        log.info("Loading cross-encoder %s", RERANKER_MODEL)
        _model = CrossEncoder(RERANKER_MODEL)
    return _model


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def rerank(query: str, candidates: list[dict], top_k: int = 5) -> list[dict]:
    """Score each (query, chunk_text) pair with the cross-encoder, keep top_k.

    rerank_score is the calibrated sigmoid(raw_logit) — see module
    docstring. raw_logit is kept alongside it (uncalibrated, for debugging/
    diagnostics only; nothing downstream should threshold on it directly).
    Metadata on each candidate is passed through untouched otherwise.
    """
    if not candidates:
        return []

    model = _get_model()
    pairs = [(query, candidate["text"]) for candidate in candidates]
    raw_scores = model.predict(pairs)

    reranked = sorted(zip(candidates, raw_scores), key=lambda pair: pair[1], reverse=True)[:top_k]

    return [
        {
            **candidate,
            "rerank_score": _sigmoid(float(raw_score)),
            "raw_logit": float(raw_score),
            "rank": rank,
        }
        for rank, (candidate, raw_score) in enumerate(reranked, start=1)
    ]


async def search(
    query: str, fused_top_k: int = 20, top_k: int = 5, jurisdiction: str = "india"
) -> list[dict]:
    """Run the full retrieval + rerank pipeline for a single query.

    rerank() itself is CPU-bound cross-encoder inference, not I/O, so it's
    only offloaded to a thread here (for callers running inside an event
    loop); fused_search's own async-ness is about the DB query underneath it.
    """
    candidates = await fused_search(query, top_k=fused_top_k, jurisdiction=jurisdiction)
    return await asyncio.to_thread(rerank, query, candidates, top_k=top_k)


if __name__ == "__main__":
    query = " ".join(sys.argv[1:]) or "traditional knowledge patent exclusion"
    results = asyncio.run(search(query, top_k=5))
    print(f"\nReranked results for: {query!r}\n")
    if not results:
        print("  (no results)")
    for r in results:
        print(
            f"  #{r['rank']:>2}  {r['rerank_score']:.3f}  {r['chunk_id']}  "
            f"({r['source_file']} p{r['page_number']})"
        )
        print(f"       {r['text'][:150]}...")
