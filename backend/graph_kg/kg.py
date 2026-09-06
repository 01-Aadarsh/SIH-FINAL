"""
Knowledge-graph lookup for IP-SAKTI — read side of graph_kg/build_kg.py.

Pure in-memory lookup against the JSON file build_kg.py writes; no I/O
beyond loading that file once. Used by graph/nodes.py::expand_related_provisions
as a deterministic, no-LLM enrichment step — same "no I/O, no LLM call"
category as graph/formulation.py's triage, for the same reason: this
project's retry mechanism exists because LLM-driven decisions aren't
perfectly reproducible run to run, so anything that doesn't need an LLM
call stays deterministic instead.

related_provisions_for() never raises: a missing or stale KG file, or a
tag no longer in it, degrades to an empty result (no related provisions
surfaced) rather than breaking the request — consistent with this
project's fail-open philosophy for every non-essential enrichment layer
(api/translation.py, api/tts.py). Losing "related provisions" is a
degraded response, not a broken one; the grounded answer and its citations
never depend on this module.

Usage:
    python -m graph_kg.kg Patents_Act_Sec3p
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

log = logging.getLogger(__name__)

KG_PATH = Path(os.getenv("KG_INDEX_PATH", "indexes/knowledge_graph.json"))

# Cross-jurisdiction pointers are always shown first (highest-value per the
# PS's own ask — pointing a domestic-jurisdiction question at its real
# international counterpart, or vice versa); co-occurrence fills any
# remaining slots up to this cap so the field stays a short, scannable
# "see also" list rather than dumping the whole graph neighborhood.
MAX_RELATED = 5

# Minimum real co-occurrence count before two tags count as "related" by
# that signal — 1 would surface every incidental pairing from a single
# chunk that happens to trip two keyword rules at once; this requires the
# pattern to repeat across at least a few chunks before it's treated as a
# genuine structural relationship rather than noise. The single source of
# truth for this threshold; build_kg.py imports it from here (not the
# other way around) so this lightweight, no-heavy-imports read module
# never has to pull in build_kg's psycopg/ingestion dependency chain just
# to read one constant.
MIN_COOCCURRENCE = 3

_kg_cache: dict | None = None


def _load() -> dict | None:
    global _kg_cache
    if _kg_cache is not None:
        return _kg_cache
    if not KG_PATH.exists():
        log.info(
            "Knowledge graph file not found at %s — run `python -m "
            "graph_kg.build_kg` to enable related-provisions lookups. "
            "Skipping, not failing.",
            KG_PATH,
        )
        return None
    try:
        _kg_cache = json.loads(KG_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        log.warning("Failed to load knowledge graph from %s: %s — skipping", KG_PATH, exc)
        return None
    return _kg_cache


def _candidate_tags(
    tags: list[str], cross_jurisdiction: dict, co_occurrence: dict, tag_examples: dict
) -> list[tuple[int, str, str]]:
    """(priority, related_tag, relation) triples, priority 0 = cross-
    jurisdiction (always ranked first), 1 = co-occurrence — split out of
    related_provisions_for() purely to keep that function's own branching
    shallow; no behavior difference from having it inline."""
    input_tags = set(tags)
    seen_related: set[str] = set()
    candidates: list[tuple[int, str, str]] = []

    def _consider(related: str, priority: int, relation: str) -> None:
        if related in input_tags or related in seen_related:
            return
        if related not in tag_examples:
            return
        seen_related.add(related)
        candidates.append((priority, related, relation))

    for tag in tags:
        for related in cross_jurisdiction.get(tag, []):
            _consider(related, 0, "cross_jurisdiction_counterpart")

    for tag in tags:
        ranked_neighbors = sorted(co_occurrence.get(tag, {}).items(), key=lambda kv: -kv[1])
        for related, count in ranked_neighbors:
            if count >= MIN_COOCCURRENCE:
                _consider(related, 1, "co_occurs_with")

    return candidates


def related_provisions_for(tags: list[str]) -> list[dict]:
    """
    Given the statutory tags actually present on this query's retrieved
    chunks, return related provisions from the knowledge graph: real
    cross-jurisdiction counterparts first, then real corpus co-occurrence,
    deduped, capped at MAX_RELATED, each resolved to one real example
    chunk it can point at. Returns [] if the graph isn't built yet, no
    input tags are given, or nothing related is found — never raises.
    """
    if not tags:
        return []
    kg = _load()
    if kg is None:
        return []

    tag_examples: dict = kg.get("tag_examples", {})
    candidates = _candidate_tags(
        tags, kg.get("cross_jurisdiction", {}), kg.get("co_occurrence", {}), tag_examples
    )
    candidates.sort(key=lambda c: c[0])

    results = []
    for _, related_tag, relation in candidates[:MAX_RELATED]:
        example = tag_examples[related_tag]
        results.append(
            {
                "tag": related_tag,
                "relation": relation,
                "source_file": example["source_file"],
                "page_number": example["page_number"],
                "section_heading": example["section_heading"],
                "jurisdiction": example["jurisdiction"],
            }
        )
    return results


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if len(sys.argv) < 2:
        print("Usage: python -m graph_kg.kg <tag> [<tag> ...]")
        sys.exit(1)
    for r in related_provisions_for(sys.argv[1:]):
        print(r)
