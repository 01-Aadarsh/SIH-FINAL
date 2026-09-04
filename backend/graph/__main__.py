"""
End-to-end graph demo for IP-SAKTI.

Usage:
    python -m graph "What does Section 3(p) say about traditional knowledge?"
"""

from __future__ import annotations

import asyncio
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from graph.build_graph import build_graph
from graph.state import DEFAULT_FLAGS, DEFAULT_JURISDICTION


async def run(query: str, jurisdiction: str = DEFAULT_JURISDICTION) -> None:
    app = build_graph()
    result = await app.ainvoke(
        {"query": query, "history": [], "jurisdiction": jurisdiction, "flags": dict(DEFAULT_FLAGS)}
    )

    print(f"\nQuery: {query!r}\n")
    print("--- Answer ---")
    print(result["answer"])
    print("\n--- Flags ---")
    print(result.get("flags"))

    citations = result.get("citations") or []
    print("\n--- Sources ---")
    if not citations:
        print("  (no sources)")
    for i, c in enumerate(citations, start=1):
        print(f"  [{i}] {c['source_file']} — page {c['page_number']} — {c['section_heading']}")


if __name__ == "__main__":
    query = " ".join(sys.argv[1:])
    if not query:
        print('Usage: python -m graph "your query"')
        sys.exit(1)
    asyncio.run(run(query))
