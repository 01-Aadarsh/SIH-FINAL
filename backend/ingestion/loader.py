"""
PDF loader for IP-SAKTI.

Extracts text from PDFs page by page, keeping the source filename and page
number attached to every page. These two fields flow all the way through to
the citation shown to the user, so they must never be lost.

Usage:
    python -m ingestion.loader data/AYUSH_IP_Circular.pdf
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from pathlib import Path

import pdfplumber

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)


@dataclass
class Page:
    """One page of text with the metadata needed for citation."""

    source_file: str
    page_number: int
    text: str
    raw_text: str = ""
    # "india" or "international" — set by which directory the PDF was found
    # in (see load_directory), not detected from content. Flows through
    # Chunk and into the DB's jurisdiction column so retrieval can filter
    # by it without guessing from text.
    jurisdiction: str = "india"

    def is_usable(self) -> bool:
        """Pages with almost no text are scans or blank separators."""
        return len(self.text.strip()) >= 50


def clean_text(raw: str) -> str:
    """
    Normalise whitespace without destroying paragraph structure.

    PDF extraction inserts line breaks at the end of every visual line, which
    breaks sentences in half. We join those, but keep blank-line breaks since
    they usually mark real paragraph boundaries.
    """
    if not raw:
        return ""

    paragraphs = raw.split("\n\n")
    cleaned = []

    for para in paragraphs:
        joined = " ".join(line.strip() for line in para.split("\n") if line.strip())
        joined = " ".join(joined.split())
        if joined:
            cleaned.append(joined)

    return "\n\n".join(cleaned)


def load_pdf(path: str | Path, jurisdiction: str = "india") -> list[Page]:
    """
    Read one PDF and return its pages.

    Raises FileNotFoundError if the path does not exist, so a typo in the
    filename fails loudly instead of silently indexing nothing.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"PDF not found: {path}")

    pages: list[Page] = []

    with pdfplumber.open(path) as pdf:
        for index, pdf_page in enumerate(pdf.pages, start=1):
            raw = pdf_page.extract_text() or ""
            page = Page(
                source_file=path.name,
                page_number=index,
                text=clean_text(raw),
                # Kept unjoined: headings sit on their own visual line, and
                # clean_text merges them into the following paragraph.
                raw_text=raw,
                jurisdiction=jurisdiction,
            )

            if page.is_usable():
                pages.append(page)
            else:
                log.warning(
                    "%s page %d has almost no extractable text (likely a scan)",
                    path.name,
                    index,
                )

    log.info("%s: loaded %d usable pages", path.name, len(pages))
    return pages


def load_directory(directory: str | Path = "data") -> list[Page]:
    """
    Load every PDF in a directory, tagging jurisdiction by location.

    PDFs directly in `directory` are "india". PDFs under `directory/international/`
    are "international" — the folder is the source of truth, not filename
    guessing, so dropping a file in the right place is all ingestion needs.
    Skips files that fail rather than aborting.
    """
    directory = Path(directory)
    if not directory.exists():
        raise FileNotFoundError(f"Directory not found: {directory}")

    india_paths = sorted(directory.glob("*.pdf"))
    international_paths = sorted((directory / "international").glob("*.pdf"))
    targets = [(p, "india") for p in india_paths] + [
        (p, "international") for p in international_paths
    ]

    if not targets:
        log.warning("No PDFs found in %s", directory)
        return []

    all_pages: list[Page] = []
    for pdf_path, jurisdiction in targets:
        try:
            all_pages.extend(load_pdf(pdf_path, jurisdiction=jurisdiction))
        except Exception as exc:
            log.error("Failed to load %s: %s", pdf_path.name, exc)

    log.info("Loaded %d pages from %d PDFs", len(all_pages), len(targets))
    return all_pages


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "data"
    pages = load_pdf(target) if target.endswith(".pdf") else load_directory(target)

    if not pages:
        log.error("Nothing loaded. Check the path and that the PDFs are not scans.")
        sys.exit(1)

    first = pages[0]
    print(f"\n--- {first.source_file}, page {first.page_number} ---")
    print(first.text[:800])
    print(f"\nTotal usable pages: {len(pages)}")
