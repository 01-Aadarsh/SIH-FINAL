"""
Chunker for IP-SAKTI.

Splits page text into overlapping chunks. Every chunk carries source_file,
page_number, section_heading and chunk_id — if any of these is missing, the
citation shown to the user will be wrong or empty, so validate_chunks()
enforces it before anything reaches the indexer.

Usage:
    python -m ingestion.chunker data/AYUSH_IP_Circular.pdf
"""

from __future__ import annotations

import logging
import re
import sys
from dataclasses import dataclass, asdict

from ingestion.loader import Page, load_directory, load_pdf

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

# Roughly 500 tokens. English averages ~4 characters per token, so 2000
# characters is a close enough proxy without pulling in a tokenizer.
CHUNK_CHARS = 2000
OVERLAP_CHARS = 200

# Headings common in Indian regulatory documents: "Section 3(p)", "Rule 12",
# "Chapter IV", "5.2 Scope", or a short ALL CAPS line.
#
# Pattern 1 previously had no end anchor, so `.match()` treated it as a
# prefix test: any body sentence starting with "Section 33 of the Drugs
# and Cosmetics Act..." matched, and detect_heading() returned the whole
# sentence as the "heading". The other two patterns already end in `$`
# (whole-line match); this one now does too, with a bounded optional
# title so real headings like "Rule 12: Definitions" still match while a
# sentence fragment — which continues in lowercase with no punctuation
# break — does not.
#
# Pattern 2's title cap was 60 chars, tighter than the page-level 80-char
# pre-filter below it — so a genuinely numbered heading whose title runs a
# bit long (e.g. "08.03.05.15 An invention which in effect, is traditional
# knowledge or Section 3(p)", 82 chars total) was silently skipped, and
# detect_heading() fell through to a later, unrelated heading further down
# the same page that happened to be short enough to match. Raised to 90 so
# the pattern's own cap isn't the binding constraint — the page-level
# pre-filter (also raised, see below) is.
HEADING_PATTERNS = [
    re.compile(
        r"^(Section|Rule|Chapter|Clause|Part|Schedule)\s+[\dIVXLC]+"
        r"(\([\w\-]+\))*(\s*[:.\-–—]\s*[A-Z].{0,90})?$",
        re.I,
    ),
    re.compile(r"^\d+(\.\d+)*\s+[A-Z][A-Za-z].{0,90}$"),
    re.compile(r"^[A-Z][A-Z\s,\-()&]{6,60}$"),
]


@dataclass
class Chunk:
    """A retrievable unit of text plus everything needed to cite it."""

    chunk_id: str
    source_file: str
    page_number: int
    section_heading: str
    text: str
    jurisdiction: str = "india"

    def to_dict(self) -> dict:
        return asdict(self)


def detect_heading(text: str) -> str:
    """
    Find the most recent heading-looking line in a block of text.

    Returns an empty string if none is found — the caller falls back to the
    last known heading from earlier in the document.
    """
    for line in text.split("\n"):
        stripped = line.strip()
        # 100, not 80: this is a coarse pre-filter to skip obviously-too-long
        # lines before running regex matching, not the thing enforcing
        # "looks like a heading" — each pattern's own `$` anchor already
        # requires a full-line structural match, so raising this doesn't
        # relax what counts as heading-shaped. It exists because a real
        # numbered heading (e.g. "08.03.05.15 An invention which in effect,
        # is traditional knowledge or Section 3(p)", 82 chars) was being
        # rejected here before pattern matching even ran, and detect_heading
        # fell through to a later, unrelated heading on the same page.
        if not stripped or len(stripped) > 100:
            continue
        for pattern in HEADING_PATTERNS:
            if pattern.match(stripped):
                return stripped
    return ""


def split_with_overlap(text: str, size: int, overlap: int) -> list[str]:
    """
    Split text into overlapping windows, breaking at sentence boundaries where
    possible so a chunk does not end mid-sentence.
    """
    text = text.strip()
    if not text:
        return []
    if len(text) <= size:
        return [text]

    pieces: list[str] = []
    start = 0

    while start < len(text):
        end = start + size

        if end < len(text):
            # Look backwards from the hard limit for a sentence break.
            window = text[start:end]
            break_at = max(
                window.rfind(". "),
                window.rfind(".\n"),
                window.rfind("\n\n"),
            )
            # Only honour the break if it is not absurdly early in the window.
            if break_at > size * 0.5:
                end = start + break_at + 1

        piece = text[start:end].strip()
        if piece:
            pieces.append(piece)

        if end >= len(text):
            break
        start = max(end - overlap, start + 1)

    return pieces


def chunk_pages(
    pages: list[Page],
    size: int = CHUNK_CHARS,
    overlap: int = OVERLAP_CHARS,
) -> list[Chunk]:
    """Turn loaded pages into chunks, carrying headings forward across pages."""
    chunks: list[Chunk] = []
    counter: dict[str, int] = {}
    last_heading: dict[str, str] = {}

    for page in pages:
        source = page.source_file
        # Headings are detected from raw_text because it preserves the original
        # line breaks; page.text has them joined into paragraphs.
        heading_source = page.raw_text or page.text
        heading = detect_heading(heading_source) or last_heading.get(source, "")
        if heading:
            last_heading[source] = heading

        for piece in split_with_overlap(page.text, size, overlap):
            counter[source] = counter.get(source, 0) + 1
            stem = source.rsplit(".", 1)[0]
            chunks.append(
                Chunk(
                    chunk_id=f"{stem}::p{page.page_number}::c{counter[source]}",
                    source_file=source,
                    page_number=page.page_number,
                    section_heading=heading or "Unlabelled section",
                    text=piece,
                    jurisdiction=page.jurisdiction,
                )
            )

    log.info("Produced %d chunks from %d pages", len(chunks), len(pages))
    return chunks


def validate_chunks(chunks: list[Chunk]) -> None:
    """
    Fail loudly if any chunk is missing citation metadata.

    This is the gate described in the project guide: if source_file is empty
    anywhere, stop and fix it before indexing, because every downstream
    citation depends on it.
    """
    problems: list[str] = []

    for chunk in chunks:
        if not chunk.source_file:
            problems.append(f"{chunk.chunk_id}: empty source_file")
        if not chunk.page_number or chunk.page_number < 1:
            problems.append(f"{chunk.chunk_id}: invalid page_number")
        if not chunk.text.strip():
            problems.append(f"{chunk.chunk_id}: empty text")
        if not chunk.chunk_id:
            problems.append("a chunk has an empty chunk_id")
        if chunk.jurisdiction not in ("india", "international"):
            problems.append(
                f"{chunk.chunk_id}: invalid jurisdiction {chunk.jurisdiction!r}"
            )

    ids = [c.chunk_id for c in chunks]
    if len(ids) != len(set(ids)):
        problems.append("duplicate chunk_ids found")

    if problems:
        for problem in problems[:20]:
            log.error(problem)
        raise ValueError(
            f"{len(problems)} metadata problem(s) found. "
            "Fix these before indexing — citations depend on this metadata."
        )

    log.info("Metadata validation passed for %d chunks", len(chunks))


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "data"
    pages = load_pdf(target) if target.endswith(".pdf") else load_directory(target)

    chunks = chunk_pages(pages)
    validate_chunks(chunks)

    for chunk in chunks[:3]:
        print(f"\n--- {chunk.chunk_id} ---")
        print(f"source: {chunk.source_file} | page: {chunk.page_number}")
        print(f"section: {chunk.section_heading}")
        print(f"chars: {len(chunk.text)}")
        print(chunk.text[:300])
