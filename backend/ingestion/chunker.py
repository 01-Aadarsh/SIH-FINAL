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
from dataclasses import dataclass, asdict, field

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
    # Best-effort keyword tagging, not authoritative legal categorization —
    # see tag_statutory_metadata() below for exactly what it checks and why
    # it should be treated as a first pass, not a finished taxonomy.
    statutory_tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# (source_file substring, text pattern, tag) — a chunk gets `tag` when its
# source_file contains the substring AND its text matches the pattern. Tied
# to documents actually in this corpus; see graph/formulation.py's
# CATEGORY_STATUTORY_TAGS for how a query's formulation category maps to
# these same tag names, and idea.md for which tags (WIPO GRATK, Nagoya,
# Budapest) have no source document indexed yet and so can never actually
# fire — they're reserved names, not implemented coverage.
_STATUTORY_TAG_RULES: list[tuple[str, "re.Pattern", str]] = []


# (source_file substring, heading pattern, tag) — checked against
# section_heading, which HierarchicalStatutoryChunker now produces as an
# exact "Section N. Title, clause (x)" string (see chunk_statutory_document
# above). Far more precise than scanning body text: a heading match means
# "this chunk IS clause 3(p)", not "this chunk's text happens to mention
# 3(p) somewhere" (which could be a cross-reference from an unrelated
# section). Only fires for documents HierarchicalStatutoryChunker actually
# parsed — section_heading is "Unlabelled section" or a plain detected-
# heading string otherwise, which these patterns won't match, so chunks
# from the sliding-window fallback still rely on the body-text rules below.
_HEADING_TAG_RULES: list[tuple[str, str, str]] = []


def _compile_heading_tag_rules():
    rules = [
        ("Patents_Act", r"section\s+3\..*clause\s*\(p\)", "Patents_Act_Sec3p"),
        ("Patents_Act", r"section\s+3\..*clause\s*\(d\)", "Patents_Act_Sec3d"),
        ("Patents_Act", r"section\s+3\..*clause\s*\(e\)", "Patents_Act_Sec3e"),
        ("Biological_Diversity_Act", r"section\s+6\b", "BDA_Sec6_NBA_Approval"),
        ("Biological_Diversity_Act", r"section\s+7\b", "BDA_Sec7_SBB_Exemption"),
    ]
    return [(src, re.compile(pat, re.IGNORECASE), tag) for src, pat, tag in rules]


def _compile_statutory_tag_rules():
    rules = [
        ("Patents_Act", r"\b3\s*\(\s*p\s*\)|section\s+3\s*\(\s*p\s*\)", "Patents_Act_Sec3p"),
        ("Patent_Office_Manual", r"\b3\s*\(\s*p\s*\)|traditional knowledge", "Patents_Act_Sec3p"),
        ("Patents_Act", r"\b3\s*\(\s*d\s*\)|section\s+3\s*\(\s*d\s*\)", "Patents_Act_Sec3d"),
        ("Patents_Act", r"\b3\s*\(\s*e\s*\)|section\s+3\s*\(\s*e\s*\)", "Patents_Act_Sec3e"),
        ("", r"\btkdl\b|traditional knowledge digital library", "TKDL"),
        ("Drugs_and_Cosmetics", r"first schedule", "D&C_First_Schedule"),
        ("Drugs_and_Cosmetics", r"phytopharmaceutical|rule\s*158", "D&C_Rule_158B"),
        ("Drugs_and_Cosmetics", r"\bcosmetic", "D&C_Cosmetic_Rules"),
        ("Biological_Diversity_Act", r"\bexempt", "BDA_Sec7_SBB_Exemption"),
        ("Biological_Diversity_Act", r"vaids?|hakims?|codified traditional knowledge|state biodiversity board", "BDA_Sec7_SBB_Exemption"),
        ("Biological_Diversity_Act", r"national biodiversity authority|\bform\s+i\b|\bform\s+ii\b|section\s+6\b", "BDA_Sec6_NBA_Approval"),
        ("Ayurveda_Aahara", r".", "FSSAI_Ayurveda_Aahar_2022"),  # whole document is this regulation
        ("NDCT_Rules", r".", "NDCT_Rules_2019"),  # whole document is this regulation
        ("", r"therapeutic claim", "No_Therapeutic_Claim"),
        ("", r"clinical trial|clinical validation|clinical stud(y|ies)", "Clinical_Validation"),
    ]
    return [(src, re.compile(pat, re.IGNORECASE), tag) for src, pat, tag in rules]


def tag_statutory_metadata(source_file: str, text: str, section_heading: str = "") -> list[str]:
    """
    Best-effort statutory tagging by keyword/filename matching — NOT
    authoritative legal categorization. A domain expert should review these
    before relying on them for a compliance decision; this exists so
    formulation-category context (see graph/formulation.py) has *something*
    concrete to point at in the prompt, not to replace legal review.

    Checks section_heading against the precise per-clause rules first (see
    _HEADING_TAG_RULES) — a real win once HierarchicalStatutoryChunker
    produces an exact "Section 3. ..., clause (p)" heading, since that
    means "this chunk IS clause 3(p)" rather than "the body text happens to
    mention 3(p)", which the body-text rules below could also pick up from
    an unrelated cross-reference. Both rule sets run and their tags merge;
    a chunk from a document the hierarchical parser didn't apply to just
    gets nothing from the heading rules and falls through to body-text
    matching as before.

    Deliberately keyword/filename-based rather than a learned classifier or
    LLM call, for the same determinism reason as graph/formulation.py's
    query-side classifier: this runs once at ingestion time per chunk, and
    needs to produce the same tags on every re-ingestion of the same PDF.
    """
    global _STATUTORY_TAG_RULES, _HEADING_TAG_RULES
    if not _STATUTORY_TAG_RULES:
        _STATUTORY_TAG_RULES = _compile_statutory_tag_rules()
    if not _HEADING_TAG_RULES:
        _HEADING_TAG_RULES = _compile_heading_tag_rules()

    tags = []
    if section_heading:
        for source_substring, pattern, tag in _HEADING_TAG_RULES:
            if source_substring and source_substring not in source_file:
                continue
            if pattern.search(section_heading):
                tags.append(tag)

    for source_substring, pattern, tag in _STATUTORY_TAG_RULES:
        if source_substring and source_substring not in source_file:
            continue
        if pattern.search(text):
            tags.append(tag)
    return sorted(set(tags))


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


# Matches a genuine numbered-section header — "4. Inventions relating to
# atomic energy not patentable.—No patent shall be granted..." — and
# specifically NOT a Table-of-Contents entry, which in the real PDFs looks
# identical except for missing the dash: "3. What are not inventions."
# (bare period, no body). Requiring ".{dash}" right after the title is what
# tells them apart. Verified against real extracted text from
# Patents_Act_1970.pdf and Trade_Marks_Act_1999.pdf before relying on it —
# not assumed from the section number format alone.
SECTION_HEADER_PATTERN = re.compile(r"^(\d+[A-Z]?)\.\s+(.+?)\.\s*[-–—]\s*(.*)$")

# A lettered/numbered clause opening a line within a section's body — "(a)",
# "(zb)", "(1)", "(i)".
CLAUSE_PATTERN = re.compile(r"^\(([0-9]+[a-z]*|[a-z]{1,3})\)\s+")

# The real top-level definition-clause sequence used across these Acts:
# (a), (b), (c) ... (z), then (za), (zb), (zc) ... — verified directly
# against Trade_Marks_Act_1999's Section 2 (which runs (a) through (zg)).
# Used to tell a genuine top-level clause apart from a roman-numeral
# sub-clause of the CURRENT one: "(i)" and "(ii)" inside the trade mark
# definition, Section 2(1)(zb), are sub-parts of (zb) ("(i) in relation to
# Chapter XII... and (ii) in relation to other provisions..."), not new
# top-level definitions — but a naive "any (letter) starts a new clause"
# rule can't tell them apart, since "i" is both the 9th letter and a roman
# numeral. Found by testing: without this, (zb)'s chunk was truncated to
# "means a mark capable of being represented graphically ... and—",
# missing the actual substance, because "(i)" was misread as ending it.
_LETTER_SEQUENCE = list("abcdefghijklmnopqrstuvwxyz") + [
    "z" + c for c in "abcdefghijklmnopqrstuvwxyz"
]
# How many letters ahead in _LETTER_SEQUENCE a label is allowed to jump and
# still count as "the next top-level clause" — Indian Acts routinely omit
# repealed letters (e.g. Trade_Marks_Act_1999 has no clause (d) or (f); the
# PDF shows a footnote marker like "2* * * * *" where it was struck out),
# so requiring an exact next-letter match would wrongly treat the clause
# after a gap as a sub-clause. 5 tolerates a handful of consecutive
# omissions without being so loose it accepts an unrelated match.
_LETTER_GAP_TOLERANCE = 5

# A document needs at least this many detected section headers before its
# hierarchical parse is trusted over the plain sliding-window chunker — one
# stray false-positive match on an unrelated document (a gazette
# notification, a policy brief) shouldn't silently switch its entire
# chunking strategy.
MIN_SECTIONS_TO_TRUST_HIERARCHICAL_PARSE = 3


class HierarchicalStatutoryChunker:
    """
    Chunks a statutory document (an Act, in this corpus) at its actual legal
    boundaries — one chunk per lettered/numbered clause within a section,
    each carrying its parent section's number and title — instead of a
    fixed character window that cuts across clause boundaries indifferent
    to what they mean.

    Why this matters concretely: "What is a trademark?" was a known,
    reproduced retrieval weak spot (see tests/test_retrieval_determinism.py)
    even after fixing BM25 tokenization — the sliding-window chunker mixed
    Trade_Marks_Act_1999's actual definition, Section 2(1)(zb), into a
    2000-character window alongside a dozen unrelated definitions ("(b)
    assignment", "(c) associated trade marks", ...), diluting it. This
    chunker gives clause (zb) its own chunk, headed "Section 2(1).
    Definitions and interpretation. Clause (zb):", so retrieval sees a
    passage that IS the definition, not one that merely contains it among
    others.

    Deliberately not a full parse-tree/AST: real nesting in these Acts goes
    section -> sub-section -> lettered clause -> sub-clause (e.g. 2(1)(zb)
    (i)), and building a fully general, verified-correct parser for every
    nesting pattern across every Indian legislative drafting convention is
    a much larger undertaking than this pass covers. What's implemented is
    the single most common and highest-value pattern — a numbered section
    containing a flat list of lettered clauses — verified against two real
    documents (Trade_Marks_Act_1999's Section 2, Patents_Act_1970's Section
    3) before being trusted, and gated by
    MIN_SECTIONS_TO_TRUST_HIERARCHICAL_PARSE so a document that doesn't
    actually have this structure falls back to the plain chunker rather
    than being silently mis-chunked by a pattern match that doesn't apply
    to it. Not run against every document in the corpus for that reason —
    see chunk_pages().
    """

    def __init__(self, source_file: str, jurisdiction: str, size: int = CHUNK_CHARS):
        self.source_file = source_file
        self.jurisdiction = jurisdiction
        self.size = size
        self.section_number: str | None = None
        self.section_title: str = ""
        self.clause_label: str | None = None
        self.clause_lines: list[str] = []
        self.current_page = 0
        self._letter_index = 0
        self._numeric_index = 1
        # (section_heading, clause_label, text, page_number) — page_number is
        # the page the clause was FLUSHED on, i.e. where it ends. A clause
        # that starts on one page and continues onto the next is attributed
        # to the later page; approximate for a multi-page clause, but a
        # defensible citation (that's the page a reader needs to see the
        # clause in full) rather than an arbitrary choice.
        self.records: list[tuple[str, str, str, int]] = []

    def _reset_sequence(self) -> None:
        self._letter_index = 0
        self._numeric_index = 1

    def _accept_as_top_level(self, label: str) -> bool:
        """True if `label` is the next expected clause in the CURRENT
        section's sequence (numeric 1,2,3.. or lettered a,b,c..z,za,zb..),
        in which case it starts a new top-level clause and the sequence
        advances. False means treat it as a nested sub-part of whatever
        clause is currently being accumulated instead — see
        _LETTER_SEQUENCE's comment for the concrete bug this exists to fix
        and what it doesn't attempt to handle."""
        if label.isdigit():
            if int(label) == self._numeric_index:
                self._numeric_index += 1
                return True
            return False

        lower = label.lower()
        window = _LETTER_SEQUENCE[self._letter_index : self._letter_index + _LETTER_GAP_TOLERANCE]
        if lower in window:
            self._letter_index += window.index(lower) + 1
            return True
        return False

    def _flush_clause(self) -> None:
        text = " ".join(line.strip() for line in self.clause_lines if line.strip())
        if text:
            heading = f"Section {self.section_number}. {self.section_title}".strip()
            if self.clause_label:
                heading = f"{heading}, clause ({self.clause_label})"
            self.records.append((heading, self.clause_label or "", text, self.current_page))
        self.clause_lines = []

    def feed_page(self, page_number: int, raw_text: str) -> None:
        self.current_page = page_number
        for line in (raw_text or "").split("\n"):
            section_match = SECTION_HEADER_PATTERN.match(line.strip())
            if section_match:
                self._flush_clause()
                self.current_page = page_number
                self.section_number = section_match.group(1)
                self.section_title = section_match.group(2).strip()
                self.clause_label = None
                self._reset_sequence()
                remainder = section_match.group(3)
                clause_match = CLAUSE_PATTERN.match(remainder)
                if clause_match and self._accept_as_top_level(clause_match.group(1)):
                    self.clause_label = clause_match.group(1)
                    self.clause_lines = [remainder[clause_match.end():]]
                else:
                    self.clause_lines = [remainder]
                continue

            if self.section_number is None:
                # Front matter / TOC / preamble before the first real
                # section — not this chunker's concern, see chunk_pages().
                continue

            clause_match = CLAUSE_PATTERN.match(line.strip())
            if clause_match and self._accept_as_top_level(clause_match.group(1)):
                self._flush_clause()
                self.current_page = page_number
                self.clause_label = clause_match.group(1)
                self.clause_lines = [line.strip()[clause_match.end():]]
            else:
                self.clause_lines.append(line)

    def finish(self) -> list[tuple[str, str, str, int]]:
        self._flush_clause()
        return self.records


def _chunk_statutory_document(pages: list[Page]) -> list[Chunk] | None:
    """
    Try the hierarchical parse for one document's pages (already filtered to
    a single source_file, in page order). Returns None — signaling the
    caller to fall back to the plain sliding-window chunker — if fewer than
    MIN_SECTIONS_TO_TRUST_HIERARCHICAL_PARSE distinct sections were found,
    since that means this document doesn't actually have the structure this
    parser targets (a gazette notification, a policy brief, a factsheet).
    """
    if not pages:
        return None

    source = pages[0].source_file
    jurisdiction = pages[0].jurisdiction
    parser = HierarchicalStatutoryChunker(source, jurisdiction)

    for page in pages:
        parser.feed_page(page.page_number, page.raw_text or page.text)

    records = parser.finish()
    distinct_sections = len({heading.split(",")[0] for heading, _, _, _ in records})
    if distinct_sections < MIN_SECTIONS_TO_TRUST_HIERARCHICAL_PARSE:
        return None

    chunks: list[Chunk] = []
    counter = 0
    for heading, clause_label, text, page_number in records:
        for piece in split_with_overlap(text, CHUNK_CHARS, OVERLAP_CHARS):
            counter += 1
            stem = source.rsplit(".", 1)[0]
            chunks.append(
                Chunk(
                    chunk_id=f"{stem}::p{page_number}::c{counter}",
                    source_file=source,
                    page_number=page_number,
                    section_heading=heading,
                    text=piece,
                    jurisdiction=jurisdiction,
                    statutory_tags=tag_statutory_metadata(source, piece, heading),
                )
            )

    log.info(
        "%s: hierarchical parse found %d sections, produced %d clause-level chunks",
        source, distinct_sections, len(chunks),
    )
    return chunks


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


def _chunk_document_sliding_window(
    pages: list[Page], size: int, overlap: int
) -> list[Chunk]:
    """The original per-page, fixed-window chunker — still the default for
    any document HierarchicalStatutoryChunker doesn't apply to (see
    chunk_pages()). Unchanged behavior from before the hierarchical parser
    existed: pages already filtered to one source_file, in page order."""
    chunks: list[Chunk] = []
    counter = 0
    last_heading = ""
    source = pages[0].source_file if pages else ""

    for page in pages:
        # Headings are detected from raw_text because it preserves the original
        # line breaks; page.text has them joined into paragraphs.
        heading_source = page.raw_text or page.text
        heading = detect_heading(heading_source) or last_heading
        if heading:
            last_heading = heading

        for piece in split_with_overlap(page.text, size, overlap):
            counter += 1
            stem = source.rsplit(".", 1)[0]
            chunks.append(
                Chunk(
                    chunk_id=f"{stem}::p{page.page_number}::c{counter}",
                    source_file=source,
                    page_number=page.page_number,
                    section_heading=heading or "Unlabelled section",
                    text=piece,
                    jurisdiction=page.jurisdiction,
                    statutory_tags=tag_statutory_metadata(source, piece, heading),
                )
            )

    return chunks


def chunk_pages(
    pages: list[Page],
    size: int = CHUNK_CHARS,
    overlap: int = OVERLAP_CHARS,
) -> list[Chunk]:
    """
    Turn loaded pages into chunks, grouped by document so each document
    gets one chunking decision, not per-page: HierarchicalStatutoryChunker
    is tried first for each document (needs section context that only
    makes sense at document scope, not per page), falling back to the
    plain sliding-window chunker for any document it doesn't apply to.
    """
    pages_by_source: dict[str, list[Page]] = {}
    order: list[str] = []
    for page in pages:
        if page.source_file not in pages_by_source:
            pages_by_source[page.source_file] = []
            order.append(page.source_file)
        pages_by_source[page.source_file].append(page)

    chunks: list[Chunk] = []
    for source in order:
        doc_pages = pages_by_source[source]
        hierarchical = _chunk_statutory_document(doc_pages)
        if hierarchical is not None:
            chunks.extend(hierarchical)
        else:
            chunks.extend(_chunk_document_sliding_window(doc_pages, size, overlap))

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
