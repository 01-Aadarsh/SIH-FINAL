# IP-SAKTI Sahayak

Smart India Hackathon 2026, problem statement **SIH26045**, Ministry of Ayush.

IP-SAKTI Sahayak is a source-cited AI assistant for questions about
Intellectual Property and regulatory rules relevant to Ayurveda in India. A
user asks a question; the system retrieves relevant text from a fixed set of
official government documents and answers using only that text, showing
which document, page, and section each part of the answer came from. If the
answer isn't in the indexed documents, it says so instead of guessing.

Scope is the national (Indian) IP/regulatory framework only. International
regimes are out of scope for this build.

## Why the architecture is what it is

Independent evaluations of commercial legal AI tools found hallucination
rates of 17–33% even with retrieval-augmented generation in place. In a
regulatory domain, a confidently wrong answer is worse than no answer. Four
decisions follow from that — full reasoning is in [idea.md](idea.md):

1. **Hybrid retrieval** (BM25 + dense embeddings, fused by Reciprocal Rank
   Fusion) instead of dense-only, because legal text needs exact matching on
   section numbers and statutory terms, not just semantic similarity.
2. **Cross-encoder reranking** to re-score candidates for actual relevance
   before anything reaches the LLM.
3. **Programmatic citations** — the LLM never writes a citation. The code
   tracks which chunks were actually retrieved and attaches their source
   file, page, and section directly. A citation cannot be hallucinated if the
   model never generates it.
4. **Abstention guardrail** — if the retrieved context doesn't contain the
   answer, the system says so explicitly and asks a clarifying question,
   rather than filling the gap from the model's general knowledge.

Orchestration is a **deterministic LangGraph DAG**, not an autonomous agent
loop — a fixed sequence of nodes with one bounded conditional retry, not
unbounded cycles.

## Pipeline

```
User query
  → Query rewriter        (standalone question, using chat history if any)
  → Hybrid retrieval       BM25 + dense embeddings, merged by RRF   → top 20
  → Cross-encoder reranker                                          → top 5
  → Grounded generation    (LLM, retrieved text only, no outside knowledge)
  → Citation attacher      (code-attached from retrieved chunks, not the LLM)
  → Answer + sources   —or—   Abstains, with one clarifying question
```

One bounded exception to the straight-line flow: if the top rerank score is
weak, the graph retries retrieval once with a reworded query before giving
up and generating an answer (or abstaining). Never more than one retry.

## Tech stack

| Layer | Choice |
|---|---|
| Orchestration | LangGraph (deterministic DAG) |
| API | FastAPI |
| Sparse retrieval | rank_bm25 (BM25Okapi) |
| Dense retrieval | sentence-transformers, `all-MiniLM-L6-v2` (384 dims) |
| Vector store | pgvector on Postgres (Neon, in this deployment) |
| Reranker | cross-encoder, `ms-marco-MiniLM-L-6-v2`, run locally |
| LLM primary | Groq API — direct, no local-first guessing on the live path |
| LLM offline fallback | Ollama (local), gated behind `OFFLINE_MODE=true` — an explicit operator flag, not tried automatically per-request (see `idea.md` for why that changed) |
| Translation | Sarvam AI — question in, answer out, wired into `/query` (not `/query/stream`) |
| Frontend | Not built here — being handled by a teammate against `docs/API_CONTRACT.md` (see Current status) |

## Repo layout

```
├── idea.md                 Standing project context / architecture rationale
├── PROJECT_GUIDE.md        Original team build plan
├── README.md               This file
├── docs/
│   └── API_CONTRACT.md    Backend HTTP interface, for frontend integration
├── data/                   Source PDFs (gitignored — see Document corpus)
│   └── international/      Placeholder — no documents indexed yet, see its README
└── backend/
    ├── ingestion/          loader.py, chunker.py (hierarchical statutory chunking), indexer.py
    ├── retrieval/          bm25_search.py, dense_search.py, fusion.py, reranker.py
    ├── generation/         prompts.py, llm_client.py, citation.py
    ├── graph/              state.py, nodes.py, build_graph.py, formulation.py
    ├── api/                main.py, translation.py, tts.py, text_chunking.py
    ├── tests/              pytest suite — retrieval determinism, translation, API contract
    ├── run.py              actual entrypoint on Windows — see API_CONTRACT.md
    ├── Procfile
    ├── requirements.txt
    └── env.example.txt
```

## Setup

```bash
git clone https://github.com/ayushanand27/sih-2026.git
cd sih-2026/backend

python -m venv .venv
.venv\Scripts\activate          # Windows; `source .venv/bin/activate` on Mac/Linux
pip install -r requirements.txt

cp env.example.txt .env
```

Fill in `.env`:
- `DATABASE_URL` — a Postgres connection string with the `pgvector` extension
  available (a free Neon or Supabase project works). The indexer runs
  `CREATE EXTENSION IF NOT EXISTS vector;` itself on first connect; if your
  provider restricts that for app-level connections, run it once yourself in
  their SQL editor.
- `GROQ_API_KEY` — free tier at [console.groq.com](https://console.groq.com).
  **Check `GROQ_MODEL` against your own key's access** — model availability
  varies by account; `client.models.list()` shows what's actually usable.
  `openai/gpt-oss-120b` is confirmed working as of this writing.
- Everything else has a working default — see `env.example.txt` for what
  each variable does.

Put source PDFs in `data/` at the repo root (sibling of `backend/`, not
inside it). Filenames matter: they're shown to the user as the citation
source, so name them for what they are (`GI_Act_1999.pdf`, not `doc1.pdf`).

Then, from `backend/`:
```bash
python -m ingestion.indexer --reset
```
This loads every PDF in `data/`, chunks it, embeds it into pgvector, and
builds the BM25 index. It fails loudly (not silently) if any chunk is
missing citation metadata — that's deliberate, since every downstream
citation depends on it.

## Running things

All commands below are run from `backend/`, with the venv active.

**Test retrieval alone** (prints BM25 / dense / fused / reranked results
side by side for one query):
```bash
python -m retrieval "Section 3(p) traditional knowledge"
```

**Test generation end to end** (retrieval → LLM → answer + citations,
printed to the terminal):
```bash
python -m generation "What does Section 3(p) say about traditional knowledge?"
```

**Run the full LangGraph pipeline** directly (same result as `/query`, no
HTTP layer):
```bash
python -m graph "What does Section 3(p) say about traditional knowledge?"
```

**Run the API:**
```bash
python run.py
```
**On Windows, use `python run.py`, not a bare `uvicorn api.main:app`** —
`uvicorn` creates its event loop before importing the app, which breaks
psycopg's async mode under Windows' default event loop. `run.py` fixes
this before anything else is imported. `uvicorn api.main:app --reload`
still works for hot-reload dev on Linux/macOS, where this doesn't apply.

Then `GET /health`, `POST /query`, `POST /query/stream`, `POST /ingest`.
Full request/response shapes, real example responses, and timing
expectations are in [docs/API_CONTRACT.md](docs/API_CONTRACT.md) — that's
the source of truth for frontend integration, not this file.

## Current status

**Done:**
- Ingestion — PDF loading, hierarchical statutory chunking (each Act
  section/clause is its own citable chunk, not a slice of a fixed-size
  window) with best-effort statutory tagging, dual indexing into pgvector
  + BM25 (jurisdiction-partitioned)
- Hybrid retrieval (BM25 + dense, fused by RRF) with a calibrated
  cross-encoder reranker
- Grounded generation with programmatic citation attachment and a verified
  abstention guardrail
- Deterministic formulation-category triage (classical / proprietary /
  phytopharmaceutical / Ayurveda-Aahar / cosmetic), keyword-based, with
  genuine-ambiguity clarification detection
- LangGraph DAG wiring the above into one async pipeline, with a bounded
  single retry on weak retrieval
- Multilingual — Sarvam AI, question translated to English before
  retrieval, answer translated back after generation (`/query` only)
- FastAPI layer: `/query`, `/query/stream` (SSE token streaming),
  `/health`, `/ingest` — see `docs/API_CONTRACT.md` for the full, current
  contract (this file is not the source of truth for API shape)
- Automated test suite (`backend/tests/`) covering retrieval determinism,
  translation correctness, and API-contract drift, run against the real
  live pipeline, not mocks

**Not yet built:**
- Frontend — being handled by a teammate against `docs/API_CONTRACT.md`
- TTS — wired in (Groq), but not yet functional: the Groq account backing
  this hasn't accepted the TTS model's usage terms. See
  `backend/env.example.txt`'s `GROQ_TTS_VOICE` comment for the one manual
  step that unblocks it.
- International-jurisdiction documents — the routing and DB partitioning
  exist end to end, but `data/international/` has no PDFs in it yet (see
  that folder's README for what's expected). Real source documents are
  required before this becomes real coverage — no placeholder/fabricated
  content has been added.
- Deployment — everything above has only been run locally so far
  (`backend/Procfile` exists for Render/Railway, unused so far)

**LLM backend, current architecture (changed since this file was first
written):** Groq is the direct primary path now — no local-first guessing
on every request. Local Ollama is still available for a venue-WiFi-fails
scenario, gated behind an explicit `OFFLINE_MODE=true` flag in the
backend's `.env`, not tried automatically. See `idea.md` for why this
flipped from the original Ollama-primary design (short version: Ollama's
GPU path crashes on the dev machine this was built on, forcing slow
CPU-only inference that was costing every request real, measured delay
before ever reaching Groq).

## Document corpus

The system can only answer from what's actually indexed — and what's
indexed changes as the corpus grows, so this file doesn't hand-maintain a
copy of that list (it did once; it went stale). The authoritative list is
always:
```bash
ls data/*.pdf
```
or, for chunk counts per document, the output of the last
`python -m ingestion.indexer` run (also queryable directly: `SELECT
source_file, COUNT(*) FROM chunks GROUP BY source_file;`).

Questions outside what's actually indexed — including
`jurisdiction: "international"` questions right now (see above), or
anything unrelated to Indian IP/Ayurveda regulation — are expected to
trigger the abstention guardrail, not a guessed answer. That's the
intended behavior, not a gap to route around.
