"""
Knowledge-graph builder for IP-SAKTI — the PS's "stage 2" (after the
citation-grounded retrieval MVP), first real slice of it.

Builds a small graph over the corpus's own statutory tags (see
ingestion/chunker.py::_compile_statutory_tag_rules) with three edge types:

  1. co_occurrence     — DATA-DERIVED. Two tags get an edge if they ever
                          appear together on the same real indexed chunk.
                          Counted directly from the database; nothing here
                          is authored or guessed.
  2. category_tags      — REUSED, not new. graph/formulation.py's
                          CATEGORY_STATUTORY_TAGS already maps each
                          formulation category to a reviewed tag set; this
                          just stores that same mapping for the graph
                          lookup to use, rather than duplicating the logic.
  3. cross_jurisdiction — THE ONE GENUINELY NEW PIECE OF CONTENT. A small,
                          explicitly authored table (CROSS_JURISDICTION_PAIRS
                          below) pairing a domestic (India) tag with an
                          international tag that covers the same real-world
                          regulatory concern (e.g. the domestic NBA
                          approval process and the Nagoya Protocol's ABS
                          Clearing-House are both about access-and-
                          benefit-sharing). This is a STRUCTURAL/
                          INFORMATIONAL claim — "these two already-indexed,
                          already-cited provisions are about the same
                          topic" — not new legal text, and not a substitute
                          for legal review. Same heuristic-first-pass
                          status as every other tag rule in this codebase
                          (see chunker.py's own docstring) — a domain
                          expert should review this table before it's
                          treated as authoritative cross-referencing.

Every tag this module knows about is resolved to one real example chunk
(chunk_id/source_file/page_number/section_heading/jurisdiction) from the
live index, so a "related provision" is always a pointer to something
actually retrievable and citable — never a bare tag name floating free of
its source. Fails loudly (not silently) if a tag named in
CROSS_JURISDICTION_PAIRS doesn't actually exist in the live index — same
philosophy as ingestion/indexer.py's chunk-metadata validation: a
misconfigured cross-reference is a real bug to catch here, not something
to paper over.

Usage:
    python -m graph_kg.build_kg
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

from graph.formulation import CATEGORY_STATUTORY_TAGS
from graph_kg.kg import MIN_COOCCURRENCE
from ingestion.indexer import connect

load_dotenv(override=True)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

KG_PATH = Path(os.getenv("KG_INDEX_PATH", "indexes/knowledge_graph.json"))

# Each pair covers the same real-world regulatory concern across the
# domestic/international split — grounded in what the paired documents are
# actually about (see data/international/README.md for what each
# international document is), not an invented legal equivalence:
#   - Access-and-benefit-sharing: the domestic NBA-approval/SBB pathway
#     (Biological Diversity Act + 2023 amendment + 2024 Rules) and the
#     Nagoya Protocol's international ABS Clearing-House mechanism.
#   - Traditional-knowledge patent exclusion / disclosure: Section 3(p)'s
#     domestic patentability bar and the WIPO GRATK Treaty's mandatory
#     disclosure obligation + its TK/genetic-resources coverage.
#   - TKDL as India's defensive-publication/prior-art tool and the same
#     treaty's TK/genetic-resources coverage it's meant to pre-empt.
CROSS_JURISDICTION_PAIRS: list[tuple[str, str]] = [
    ("BDA_Sec6_NBA_Approval", "Nagoya_ABS_Clearing_House"),
    ("BDA_Sec7_SBB_Exemption", "Nagoya_ABS_Clearing_House"),
    ("NBA_Section6", "Nagoya_ABS_Clearing_House"),
    ("ABS_Formula", "Nagoya_ABS_Clearing_House"),
    ("Patents_Act_Sec3p", "Traditional_Knowledge"),
    ("Patents_Act_Sec3p", "Mandatory_Patent_Disclosure"),
    ("Patents_Act_Sec3p", "Genetic_Resources"),
    ("TKDL", "Traditional_Knowledge"),
    ("TKDL", "Genetic_Resources"),
]


def _fetch_tagged_chunks(conn) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT chunk_id, source_file, page_number, section_heading,
                   jurisdiction, statutory_tags
            FROM chunks
            WHERE statutory_tags != '{}'
            ORDER BY chunk_id
            """
        )
        rows = cur.fetchall()
    return [
        {
            "chunk_id": r[0],
            "source_file": r[1],
            "page_number": r[2],
            "section_heading": r[3],
            "jurisdiction": r[4],
            "statutory_tags": r[5] or [],
        }
        for r in rows
    ]


def _build_co_occurrence(rows: list[dict]) -> dict[str, dict[str, int]]:
    co: dict[str, dict[str, int]] = {}
    for row in rows:
        tags = sorted(set(row["statutory_tags"]))
        for i, a in enumerate(tags):
            for b in tags[i + 1:]:
                co.setdefault(a, {}).setdefault(b, 0)
                co.setdefault(b, {}).setdefault(a, 0)
                co[a][b] += 1
                co[b][a] += 1
    return co


def _build_tag_examples(rows: list[dict]) -> tuple[dict[str, dict], dict[str, list[str]]]:
    """First chunk seen (by chunk_id order, so deterministic re-run to
    re-run) carrying each tag becomes that tag's example. Also tracks every
    jurisdiction a tag has actually appeared under — most tags are
    single-jurisdiction by construction (chunker.py's rules are keyed to
    one source file's document set), but nothing enforces that, so this
    records reality rather than assuming it."""
    examples: dict[str, dict] = {}
    jurisdictions: dict[str, set[str]] = {}
    for row in rows:
        for tag in row["statutory_tags"]:
            jurisdictions.setdefault(tag, set()).add(row["jurisdiction"])
            if tag not in examples:
                examples[tag] = {
                    "chunk_id": row["chunk_id"],
                    "source_file": row["source_file"],
                    "page_number": row["page_number"],
                    "section_heading": row["section_heading"],
                    "jurisdiction": row["jurisdiction"],
                }
    return examples, {tag: sorted(js) for tag, js in jurisdictions.items()}


def build() -> dict:
    with connect() as conn:
        rows = _fetch_tagged_chunks(conn)

    if not rows:
        raise RuntimeError(
            "No chunks with statutory_tags found — run ingestion "
            "(python -m ingestion.indexer) before building the knowledge graph."
        )

    all_tags = {tag for row in rows for tag in row["statutory_tags"]}
    co_occurrence = _build_co_occurrence(rows)
    tag_examples, tag_jurisdictions = _build_tag_examples(rows)

    missing = [
        tag for pair in CROSS_JURISDICTION_PAIRS for tag in pair if tag not in all_tags
    ]
    if missing:
        raise RuntimeError(
            f"CROSS_JURISDICTION_PAIRS references tag(s) not present in any "
            f"indexed chunk: {sorted(set(missing))} — a rule in "
            f"ingestion/chunker.py either doesn't exist or hasn't fired yet. "
            f"Fix the tag name or re-run ingestion before building the graph."
        )

    cross_jurisdiction: dict[str, list[str]] = {}
    for a, b in CROSS_JURISDICTION_PAIRS:
        cross_jurisdiction.setdefault(a, []).append(b)
        cross_jurisdiction.setdefault(b, []).append(a)

    kg = {
        "tags": sorted(all_tags),
        "co_occurrence": co_occurrence,
        "cross_jurisdiction": cross_jurisdiction,
        "category_tags": CATEGORY_STATUTORY_TAGS,
        "tag_jurisdictions": tag_jurisdictions,
        "tag_examples": tag_examples,
    }

    KG_PATH.parent.mkdir(parents=True, exist_ok=True)
    KG_PATH.write_text(json.dumps(kg, indent=2), encoding="utf-8")

    log.info(
        "Knowledge graph written to %s: %d tags, %d cross-jurisdiction pairs, "
        "%d tags with >=1 co-occurrence edge",
        KG_PATH, len(all_tags), len(CROSS_JURISDICTION_PAIRS),
        sum(1 for t in co_occurrence if any(c >= MIN_COOCCURRENCE for c in co_occurrence[t].values())),
    )
    return kg


if __name__ == "__main__":
    build()
