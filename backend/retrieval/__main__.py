"""
End-to-end retrieval demo for IP-SAKTI.

Runs one query through every stage and prints what each contributes, so you
can see a chunk enter via BM25 or dense, then watch its rank move through
fusion and reranking.

Usage:
    python -m retrieval "Section 3(p) traditional knowledge"
"""

from __future__ import annotations

import asyncio
import sys

from retrieval.bm25_search import search as bm25_search
from retrieval.dense_search import search as dense_search
from retrieval.fusion import fuse
from retrieval.reranker import rerank


def _print_stage(title: str, results: list[dict], limit: int = 5) -> None:
    print(f"\n--- {title} ---")
    if not results:
        print("  (no results)")
        return
    for r in results[:limit]:
        score_key = "rerank_score" if "rerank_score" in r else "score"
        source = r.get("source_file", "?")
        page = r.get("page_number", "?")
        print(f"  #{r['rank']:>2}  {r[score_key]:.4f}  {r['chunk_id']}  ({source} p{page})")


async def run(query: str, jurisdiction: str = "india") -> None:
    print(f"\nQuery: {query!r}  (jurisdiction={jurisdiction})")

    bm25_results = bm25_search(query, top_k=20, jurisdiction=jurisdiction)
    _print_stage("BM25 top 5", bm25_results)

    dense_results = await dense_search(query, top_k=20, jurisdiction=jurisdiction)
    _print_stage("Dense top 5", dense_results)

    fused_results = await fuse(bm25_results, dense_results, top_k=20, jurisdiction=jurisdiction)
    _print_stage("Fused (RRF) top 5", fused_results)

    reranked_results = rerank(query, fused_results, top_k=5)
    _print_stage("Reranked top 5", reranked_results)


if __name__ == "__main__":
    query = " ".join(sys.argv[1:])
    if not query:
        print('Usage: python -m retrieval "your query"')
        sys.exit(1)
    asyncio.run(run(query))
