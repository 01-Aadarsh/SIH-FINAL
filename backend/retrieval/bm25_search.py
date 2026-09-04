"""
BM25 sparse retrieval for IP-SAKTI.

Loads the per-jurisdiction BM25 indexes built by ingestion.indexer and
searches with the identical tokenizer used at index time — imported, not
redefined, because if the two ever diverge, BM25 silently stops matching
with no error to tell you.

One BM25Okapi per jurisdiction, not one shared index post-filtered
afterward: jurisdiction is applied *during* the BM25 pass itself, the same
way dense_search's SQL WHERE clause applies it during the dense pass — see
ingestion/indexer.py::build_bm25 for why.

Usage:
    python -m retrieval.bm25_search "Section 3(p) traditional knowledge"
"""

from __future__ import annotations

import logging
import os
import pickle
import sys
from pathlib import Path

from dotenv import load_dotenv

from ingestion.indexer import tokenize

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

BM25_PATH = Path(os.getenv("BM25_INDEX_PATH", "backend/indexes/bm25.pkl"))

_bm25_by_jurisdiction: dict | None = None
_chunk_ids_by_jurisdiction: dict | None = None


def _load_index():
    """Load and cache the BM25 pickle. Fails loudly if it isn't there yet."""
    global _bm25_by_jurisdiction, _chunk_ids_by_jurisdiction
    if _bm25_by_jurisdiction is not None:
        return _bm25_by_jurisdiction, _chunk_ids_by_jurisdiction

    if not BM25_PATH.exists():
        raise FileNotFoundError(
            f"BM25 index not found at {BM25_PATH}. "
            "Run `python -m ingestion.indexer` first to build it."
        )

    with open(BM25_PATH, "rb") as handle:
        payload = pickle.load(handle)

    _bm25_by_jurisdiction = payload["bm25_by_jurisdiction"]
    _chunk_ids_by_jurisdiction = payload["chunk_ids_by_jurisdiction"]
    for jurisdiction, ids in _chunk_ids_by_jurisdiction.items():
        log.info("Loaded BM25 index for jurisdiction=%s: %d chunks", jurisdiction, len(ids))
    return _bm25_by_jurisdiction, _chunk_ids_by_jurisdiction


def search(query: str, top_k: int = 20, jurisdiction: str = "india") -> list[dict]:
    """Query -> top_k chunk_ids ranked by BM25 score, highest first, scoped
    to `jurisdiction`'s own index.

    Score positions map back to chunk_ids by index, since the pickle stores
    each jurisdiction's chunk_ids in the same order as the corpus its
    BM25Okapi was built from.
    """
    bm25_by_jurisdiction, chunk_ids_by_jurisdiction = _load_index()

    bm25 = bm25_by_jurisdiction.get(jurisdiction)
    chunk_ids = chunk_ids_by_jurisdiction.get(jurisdiction, [])
    if bm25 is None or not chunk_ids:
        # A real, expected case (e.g. jurisdiction="international" before
        # any international documents are indexed), not an error.
        return []

    tokens = tokenize(query)
    if not tokens:
        log.warning("Query tokenized to nothing: %r", query)
        return []

    scores = bm25.get_scores(tokens)

    ranked = sorted(
        ((chunk_ids[i], score) for i, score in enumerate(scores) if score > 0),
        key=lambda pair: pair[1],
        reverse=True,
    )[:top_k]

    return [
        {"chunk_id": chunk_id, "score": float(score), "rank": rank}
        for rank, (chunk_id, score) in enumerate(ranked, start=1)
    ]


if __name__ == "__main__":
    query = " ".join(sys.argv[1:]) or "traditional knowledge patent exclusion"
    results = search(query, top_k=10)
    print(f"\nBM25 results for: {query!r}\n")
    if not results:
        print("  (no results)")
    for r in results:
        print(f"  #{r['rank']:>2}  {r['score']:.3f}  {r['chunk_id']}")
