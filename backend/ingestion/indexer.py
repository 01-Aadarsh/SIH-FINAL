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
import logging
import os
import pickle
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from pgvector.psycopg import register_vector
from rank_bm25 import BM25Okapi
from sentence_transformers import SentenceTransformer

from ingestion.chunker import Chunk, chunk_pages, validate_chunks
from ingestion.loader import load_directory

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

EMBED_MODEL = os.getenv("EMBED_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
EMBED_DIM = int(os.getenv("EMBED_DIM", "384"))
DATABASE_URL = os.getenv("DATABASE_URL")
BM25_PATH = Path(os.getenv("BM25_INDEX_PATH", "backend/indexes/bm25.pkl"))
DATA_DIR = os.getenv("DATA_DIR", "data")

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS chunks (
    chunk_id        TEXT PRIMARY KEY,
    source_file     TEXT NOT NULL,
    page_number     INTEGER NOT NULL,
    section_heading TEXT NOT NULL,
    text            TEXT NOT NULL,
    embedding       VECTOR({EMBED_DIM}) NOT NULL
);

CREATE INDEX IF NOT EXISTS chunks_embedding_idx
    ON chunks USING hnsw (embedding vector_cosine_ops);

CREATE INDEX IF NOT EXISTS chunks_source_idx ON chunks (source_file);
"""


def tokenize(text: str) -> list[str]:
    """
    Lowercase word tokenizer for BM25.

    Kept deliberately simple and identical to the one used at query time —
    if the two ever diverge, BM25 silently stops matching.
    """
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
            embedding,
        )
        for chunk, embedding in zip(chunks, embeddings)
    ]

    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO chunks
                (chunk_id, source_file, page_number, section_heading, text, embedding)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (chunk_id) DO UPDATE SET
                source_file     = EXCLUDED.source_file,
                page_number     = EXCLUDED.page_number,
                section_heading = EXCLUDED.section_heading,
                text            = EXCLUDED.text,
                embedding       = EXCLUDED.embedding;
            """,
            rows,
        )
    conn.commit()
    log.info("Stored %d chunks in pgvector", len(rows))


def build_bm25(chunks: list[Chunk]) -> None:
    """
    Persist the BM25 index alongside the chunk_ids it was built from.

    The order of chunk_ids must match the order of the corpus passed to
    BM25Okapi — the search code maps score positions back to ids by index.
    """
    corpus = [tokenize(chunk.text) for chunk in chunks]
    bm25 = BM25Okapi(corpus)

    BM25_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(BM25_PATH, "wb") as handle:
        pickle.dump(
            {"bm25": bm25, "chunk_ids": [chunk.chunk_id for chunk in chunks]},
            handle,
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
