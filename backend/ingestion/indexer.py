"""
Indexer for IP-SAKTI.

Indexes chunks twice, because hybrid retrieval needs both:
  1. A BM25 index on disk  -> exact terms, rule numbers, section references
  2. Embeddings in pgvector -> semantic similarity

Both indexes store the same chunk_id, so results from either can be fused
and traced back to the same citation metadata.

Usage:
    python -m ingestion.indexer            # index everything in data/
    python -m ingestion.indexer --reset    # wipe and rebuild from scratch
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import pickle
import re
import sys
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from pgvector.psycopg import register_vector, register_vector_async
from rank_bm25 import BM25Okapi
from sentence_transformers import SentenceTransformer

from ingestion.chunker import Chunk, chunk_pages, validate_chunks
from ingestion.loader import load_directory

load_dotenv(override=True)

# psycopg's async mode is built on selector-based event loop APIs and raises
# InterfaceError under asyncio's Windows default (ProactorEventLoop) —
# confirmed by reproducing it directly, not a hypothetical. connect_async()
# is what every async retrieval call ultimately goes through, so fixing the
# policy here (at import time, before any loop is created) covers uvicorn,
# every `python -m` CLI entrypoint, and any test script alike, without
# needing the same guard repeated in each one. A no-op on Linux/macOS.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

EMBED_MODEL = os.getenv("EMBED_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
EMBED_DIM = int(os.getenv("EMBED_DIM", "384"))
DATABASE_URL = os.getenv("DATABASE_URL")
BM25_PATH = Path(os.getenv("BM25_INDEX_PATH", "backend/indexes/bm25.pkl"))
DATA_DIR = os.getenv("DATA_DIR", "data")

# Only these two values are ever written to the jurisdiction column or
# accepted from the API — see api/main.py's QueryRequest.jurisdiction and
# ingestion/loader.py's folder-based tagging. Kept as a single source of
# truth so the DB constraint, the loader, and the API can't drift apart.
JURISDICTIONS = ("india", "international")

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS chunks (
    chunk_id        TEXT PRIMARY KEY,
    source_file     TEXT NOT NULL,
    page_number     INTEGER NOT NULL,
    section_heading TEXT NOT NULL,
    text            TEXT NOT NULL,
    embedding       VECTOR({EMBED_DIM}) NOT NULL
);

-- ADD COLUMN IF NOT EXISTS, not part of CREATE TABLE: this repo shipped
-- without a jurisdiction column first, and there's already a live corpus
-- indexed under the old schema. This migrates that table in place
-- (defaulting existing rows to 'india', which is accurate — every
-- document indexed before international support existed was domestic
-- law) instead of forcing a full re-embed via --reset.
ALTER TABLE chunks ADD COLUMN IF NOT EXISTS jurisdiction TEXT NOT NULL DEFAULT 'india';

-- Best-effort keyword tagging (ingestion/chunker.py::tag_statutory_metadata),
-- not authoritative legal categorization. Not used as a hard retrieval
-- filter (a missed heuristic match would silently hide a real answer) —
-- carried through to the API/prompt as advisory context only. Empty array
-- default, not NULL, so callers never need a NULL check.
ALTER TABLE chunks ADD COLUMN IF NOT EXISTS statutory_tags TEXT[] NOT NULL DEFAULT '{{}}';

-- Postgres has no `ADD CONSTRAINT IF NOT EXISTS`, so this re-runs on every
-- ensure_schema() call and must not error the 2nd+ time — catch the
-- duplicate_object error instead of pre-checking pg_constraint, since that
-- check-then-act would itself race under concurrent ingestion.
DO $$
BEGIN
    ALTER TABLE chunks ADD CONSTRAINT chunks_jurisdiction_check
        CHECK (jurisdiction IN {JURISDICTIONS});
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

CREATE INDEX IF NOT EXISTS chunks_embedding_idx
    ON chunks USING hnsw (embedding vector_cosine_ops);

CREATE INDEX IF NOT EXISTS chunks_source_idx ON chunks (source_file);
CREATE INDEX IF NOT EXISTS chunks_jurisdiction_idx ON chunks (jurisdiction);
"""


# IPR/AYUSH domain synonym pairs. Each entry: (pattern matching one surface
# form, canonical alias phrase for the OTHER form). tokenize() appends the
# alias whenever the pattern matches, so both surface forms end up sharing
# tokens in the same document/query — this is additive (original wording
# stays searchable too), not a replacement, and runs once over the
# original text (no re-scanning of appended output, so no expansion loops).
#
# Added after a real, reproduced failure: "What is a trademark?" (one word)
# scored the actual Trade Marks Act far below unrelated chunks, because the
# Act's own text says "trade marks" (two words, its literal title) and BM25
# is exact-token matching — "trademark" and "trade"+"marks" share zero
# tokens. Confirmed by running the query through bm25_search.py directly:
# Trade_Marks_Act_1999 didn't appear in the BM25 top 5 at all despite being
# the single most relevant document in the corpus for that question.
DOMAIN_SYNONYMS: list[tuple] = []


def _compile_domain_synonyms():
    pairs = [
        (r"\btrade[\s-]*marks?\b", "trademark"),
        (r"\btrademarks?\b", "trade mark"),
        (r"\bipr\b", "intellectual property rights"),
        (r"\bintellectual property rights?\b", "ipr"),
        (r"\bp\s*(?:&|and)\s*p\b", "patent or proprietary medicine"),
        (r"\bpatent\s+or\s+proprietary\s+medicine[s]?\b", "p and p"),
        (r"\btk\b", "traditional knowledge"),
        (r"\btraditional knowledge\b", "tk"),
        (r"\bbd\s+act\b", "biological diversity act"),
        (r"\bbiological diversity act\b", "bd act"),
        (r"\bbda\b", "biological diversity act"),
        (r"\bnba\b", "national biodiversity authority"),
        (r"\bnational biodiversity authority\b", "nba"),
        (r"\btkdl\b", "traditional knowledge digital library"),
        (r"\bd\s*&\s*c\s+act\b", "drugs and cosmetics act"),
        (r"\bfssai\b", "food safety and standards authority of india"),
        (r"\bgi\b", "geographical indication"),
        (r"\bgeographical indications?\b", "gi"),
    ]
    return [(re.compile(p, re.IGNORECASE), alias) for p, alias in pairs]


DOMAIN_SYNONYMS = _compile_domain_synonyms()


def _augment_domain_synonyms(text: str) -> str:
    """Append canonical aliases for any recognized domain term variant found
    in `text`, so BM25 sees matching tokens regardless of which surface form
    (acronym vs. spelled-out, one-word vs. two-word) the document or the
    query happens to use. See DOMAIN_SYNONYMS above for the real bug this
    fixes and how it was found."""
    if not DOMAIN_SYNONYMS:
        return text
    extras = [alias for pattern, alias in DOMAIN_SYNONYMS if pattern.search(text)]
    return text if not extras else text + " " + " ".join(extras)


def tokenize(text: str) -> list[str]:
    """
    Lowercase word tokenizer for BM25, with IPR/AYUSH domain synonym
    normalization (see DOMAIN_SYNONYMS).

    Kept deliberately simple and identical to the one used at query time —
    if the two ever diverge, BM25 silently stops matching. The synonym
    augmentation runs first, inside this shared function, specifically so
    it can never diverge either: bm25_search.py imports this exact
    function, so index-time and query-time normalization are structurally
    the same code path, not two implementations that could drift apart.
    """
    text = _augment_domain_synonyms(text)
    return [token for token in "".join(
        char.lower() if char.isalnum() else " " for char in text
    ).split() if token]


def connect() -> psycopg.Connection:
    if not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL is not set. Copy .env.example to .env and fill it in."
        )
    conn = psycopg.connect(DATABASE_URL)
    # Must run before register_vector(): on a fresh database the `vector`
    # type doesn't exist until this extension is created, and register_vector
    # looks that type up immediately. Creating the schema's tables/indexes
    # can still wait until ensure_schema().
    with conn.cursor() as cur:
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
    conn.commit()
    register_vector(conn)
    return conn


async def connect_async() -> psycopg.AsyncConnection:
    """
    Async counterpart of connect(), for the live query path (retrieval/*).

    ingestion.indexer itself stays synchronous — it's an offline batch job
    (embed + upsert the whole corpus), not a per-request hot path, so there's
    nothing to gain from making it async. This exists because dense_search
    and fusion run inside the async LangGraph nodes and must not block the
    event loop with a synchronous DB round-trip.

    Retries with backoff (initial attempt, then after 1s, 2s, 4s — 4 attempts
    total): reproduced directly against the live Neon DB this project uses,
    the actual failure is `psycopg.OperationalError: [Errno 11002]
    getaddrinfo failed` — a transient failure in asyncio's own DNS
    resolution path on Windows, not a dead or unreachable host (a plain
    synchronous `socket.getaddrinfo()` against the same hostname at the same
    moment resolved instantly). A single 2s retry wasn't enough margin —
    this still failed on the second attempt in testing — so this now allows
    several. Also covers Neon/Supabase's free-tier compute suspending after
    a few idle minutes and taking a moment to wake on the next connection,
    the same shape of problem. Observed symptom this fixes: the very first
    query after a lull fails with a generic 500, and a manual retry seconds
    later succeeds because the transient condition has cleared — the retry
    belongs here instead of in every caller.
    """
    if not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL is not set. Copy .env.example to .env and fill it in."
        )
    last_error: psycopg.OperationalError | None = None
    for attempt, backoff in enumerate((0, 1, 2, 4)):
        if backoff:
            log.warning(
                "DB connect failed (attempt %d) — retrying in %ds", attempt, backoff
            )
            await asyncio.sleep(backoff)
        try:
            conn = await psycopg.AsyncConnection.connect(DATABASE_URL)
            break
        except psycopg.OperationalError as exc:
            last_error = exc
    else:
        assert last_error is not None
        raise last_error
    async with conn.cursor() as cur:
        await cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
    await conn.commit()
    await register_vector_async(conn)
    return conn


def ensure_schema(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute(SCHEMA)
    conn.commit()
    log.info("Schema ready")


def reset_tables(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS chunks;")
    conn.commit()
    if BM25_PATH.exists():
        BM25_PATH.unlink()
    log.info("Existing index wiped")


def embed_chunks(chunks: list[Chunk], model: SentenceTransformer):
    log.info("Embedding %d chunks with %s", len(chunks), EMBED_MODEL)
    return model.encode(
        [chunk.text for chunk in chunks],
        batch_size=32,
        show_progress_bar=True,
        normalize_embeddings=True,
    )


def store_in_pgvector(conn: psycopg.Connection, chunks: list[Chunk], embeddings) -> None:
    """Upsert so re-running the script updates rather than duplicating."""
    rows = [
        (
            chunk.chunk_id,
            chunk.source_file,
            chunk.page_number,
            chunk.section_heading,
            chunk.text,
            chunk.jurisdiction,
            chunk.statutory_tags,
            embedding,
        )
        for chunk, embedding in zip(chunks, embeddings)
    ]

    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO chunks
                (chunk_id, source_file, page_number, section_heading, text, jurisdiction, statutory_tags, embedding)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (chunk_id) DO UPDATE SET
                source_file     = EXCLUDED.source_file,
                page_number     = EXCLUDED.page_number,
                section_heading = EXCLUDED.section_heading,
                text            = EXCLUDED.text,
                jurisdiction    = EXCLUDED.jurisdiction,
                statutory_tags  = EXCLUDED.statutory_tags,
                embedding       = EXCLUDED.embedding;
            """,
            rows,
        )
    conn.commit()
    log.info("Stored %d chunks in pgvector", len(rows))


def build_bm25(chunks: list[Chunk]) -> None:
    """
    Persist one BM25 index *per jurisdiction*, not one shared index.

    Previously a single BM25Okapi covered the whole corpus regardless of
    jurisdiction, and an "international" query still searched India-only
    text — harmless in effect (fusion.py drops the mismatched-jurisdiction
    results before they reach the LLM) but not actually "filtered during
    the BM25 pass" the way dense_search's SQL WHERE clause is. Building a
    separate index per jurisdiction makes BM25 itself jurisdiction-scoped,
    the same as dense retrieval, instead of relying on a post-filter to
    catch what BM25 shouldn't have returned in the first place.

    The order of chunk_ids must match the order of the corpus passed to
    each BM25Okapi — the search code maps score positions back to ids by
    index, per jurisdiction's own sub-list.
    """
    bm25_by_jurisdiction: dict[str, BM25Okapi] = {}
    chunk_ids_by_jurisdiction: dict[str, list[str]] = {}

    for jurisdiction in JURISDICTIONS:
        subset = [c for c in chunks if c.jurisdiction == jurisdiction]
        chunk_ids_by_jurisdiction[jurisdiction] = [c.chunk_id for c in subset]
        # BM25Okapi errors on an empty corpus (division by zero computing
        # average document length) — "international" is legitimately empty
        # right now (see data/international/README.md), so this must be a
        # real, distinct case, not an incidental crash.
        bm25_by_jurisdiction[jurisdiction] = BM25Okapi([tokenize(c.text) for c in subset]) if subset else None

    BM25_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(BM25_PATH, "wb") as handle:
        pickle.dump(
            {
                "bm25_by_jurisdiction": bm25_by_jurisdiction,
                "chunk_ids_by_jurisdiction": chunk_ids_by_jurisdiction,
            },
            handle,
        )
    for jurisdiction in JURISDICTIONS:
        log.info(
            "BM25 index for jurisdiction=%s: %d chunks",
            jurisdiction, len(chunk_ids_by_jurisdiction[jurisdiction]),
        )
    log.info("BM25 index written to %s", BM25_PATH)


def verify(conn: psycopg.Connection) -> None:
    """
    The gate from the project guide: if source_file is empty on any row,
    stop here rather than discovering it when citations render blank.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM chunks;")
        total = cur.fetchone()[0]

        cur.execute(
            "SELECT COUNT(*) FROM chunks "
            "WHERE source_file IS NULL OR source_file = '' "
            "   OR page_number IS NULL OR text = '';"
        )
        broken = cur.fetchone()[0]

        cur.execute(
            "SELECT source_file, COUNT(*) FROM chunks "
            "GROUP BY source_file ORDER BY source_file;"
        )
        breakdown = cur.fetchall()

        cur.execute(
            "SELECT jurisdiction, COUNT(*) FROM chunks "
            "GROUP BY jurisdiction ORDER BY jurisdiction;"
        )
        jurisdiction_breakdown = cur.fetchall()

    if total == 0:
        raise RuntimeError("No chunks were stored. Check that data/ contains PDFs.")
    if broken:
        raise RuntimeError(
            f"{broken} rows are missing citation metadata. "
            "Fix the ingestion before moving on — citations depend on this."
        )

    log.info("Verification passed: %d chunks indexed", total)
    for source_file, count in breakdown:
        log.info("  %-50s %4d chunks", source_file, count)
    log.info("By jurisdiction:")
    for jurisdiction, count in jurisdiction_breakdown:
        log.info("  %-50s %4d chunks", jurisdiction, count)


def run(reset: bool = False) -> None:
    pages = load_directory(DATA_DIR)
    if not pages:
        raise RuntimeError(f"No usable pages found in {DATA_DIR}/")

    chunks = chunk_pages(pages)
    validate_chunks(chunks)

    model = SentenceTransformer(EMBED_MODEL)
    embeddings = embed_chunks(chunks, model)

    with connect() as conn:
        if reset:
            reset_tables(conn)
        ensure_schema(conn)
        store_in_pgvector(conn, chunks, embeddings)
        build_bm25(chunks)
        verify(conn)

    log.info("Ingestion complete")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Index documents for IP-SAKTI")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Drop the existing table and BM25 index before rebuilding",
    )
    args = parser.parse_args()
    run(reset=args.reset)
