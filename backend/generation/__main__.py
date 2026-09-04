"""
End-to-end generation demo for IP-SAKTI.

Runs the retrieval pipeline, generates a grounded answer with Groq, and
prints the answer followed by its sources — or a clear abstention with no
sources shown, if the retrieved context didn't contain the answer.

Usage:
    python -m generation "What does Section 3(p) say about traditional knowledge?"
"""

from __future__ import annotations

import asyncio
import sys

# Windows consoles default to cp1252, which can't encode every character a
# model might use (curly quotes, narrow no-break spaces, etc). Reconfigure
# stdout to UTF-8 so a normal answer never crashes the demo script.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from generation.citation import attach_citations, is_abstention
from generation.llm_client import agenerate
from retrieval.reranker import search as retrieve


async def run(query: str) -> None:
    chunks = await retrieve(query, fused_top_k=20, top_k=5)
    answer = await agenerate(query, chunks)

    print(f"\nQuery: {query!r}\n")
    print("--- Answer ---")
    print(answer)

    if is_abstention(answer):
        print("\n(abstained — no sources shown)")
        return

    citations = attach_citations(chunks)
    print("\n--- Sources ---")
    if not citations:
        print("  (no sources retrieved)")
    for i, c in enumerate(citations, start=1):
        print(f"  [{i}] {c['source_file']} — page {c['page_number']} — {c['section_heading']}")


if __name__ == "__main__":
    query = " ".join(sys.argv[1:])
    if not query:
        print('Usage: python -m generation "your query"')
        sys.exit(1)
    asyncio.run(run(query))
