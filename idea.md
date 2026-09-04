# IP-SAKTI Sahayak — Project Context

This file gives you (Claude Code) the standing context for this repo. Read it before writing anything.

## What this project is

Smart India Hackathon 2026, problem statement **SIH26045**, Ministry of Ayush.

**IP-SAKTI Sahayak** — a multilingual, RAG-based, source-cited AI assistant for Intellectual Property and regulatory guidance in Ayurveda.

A user asks a question about Ayurveda-related IP or regulatory rules. The system retrieves relevant text from official Indian government documents and answers using only that text, showing exactly which document, page and section each answer came from. If the answer isn't in the documents, it says so instead of guessing.

**Scope:** National (Indian) framework only. International IP regimes are future work, not built.

## Why the architecture is what it is

Independent evaluations of commercial legal AI tools found hallucination rates of 17–33% even with RAG in place. In a regulatory domain a confidently wrong answer is worse than no answer. Four decisions follow from that:

1. **Hybrid retrieval, not dense-only.** BM25 (exact terms, rule numbers, section references) fused with dense embeddings (semantic similarity) via Reciprocal Rank Fusion. Legal text needs exact matching, not just "similar meaning."

2. **Cross-encoder reranking.** Re-scores candidates for real relevance before they reach the LLM.

3. **Programmatic citations — the most important rule in this repo.** The LLM never writes citations. Our code tracks which chunks were retrieved, with their `source_file`, `page_number` and `section_heading`, and attaches those. A citation cannot be hallucinated if the model never generates it. **Never** change this to have the model emit citations.

4. **Abstention guardrail.** If retrieved context doesn't contain the answer, respond "not found in my sources" and ask one clarifying question. Never fill the gap from model knowledge.

**Orchestration:** LangGraph as a **deterministic DAG**, not an autonomous agent loop. Fixed graph, traceable, debuggable. Do not introduce unbounded cycles.

## Pipeline

```
User query (+ jurisdiction: india | international)
  → Query rewriter (standalone question using chat history)
  → Hybrid retrieval (BM25 + dense, merged by RRF, filtered to jurisdiction) → top 20
  → Cross-encoder reranker                                                  → top 5
  → Grounded generation (LLM, retrieved text only — streamed as SSE tokens on /query/stream)
  → Citation attacher (code-attached, verified)
  → Answer + sources  |  or  Abstains
```

`jurisdiction=international` currently abstains on every query — no
international documents are indexed yet (`data/international/` is a
placeholder, see its README). That's the correct behavior, not a bug: the
alternative (silently falling back to Indian law, or crashing) would be
worse than an honest "not found."

The graph itself (`backend/graph/build_graph.py`) is unchanged — same nodes,
same edges, same bounded single retry. What changed is how it's driven:
`compiled_graph.ainvoke()`, not `.invoke()` via a thread pool — see
`backend/api/main.py`. Every node that does I/O (LLM calls, pgvector
queries) is a real `async def`, not a sync function offloaded to a thread.

## Tech stack

| Layer | Choice |
|---|---|
| Orchestration | LangGraph (deterministic DAG, driven async via `.ainvoke()`) |
| API | FastAPI — `/query` (single JSON response) and `/query/stream` (SSE token streaming) |
| Sparse retrieval | rank_bm25 |
| Dense retrieval | sentence-transformers (`all-MiniLM-L6-v2`, 384 dims), async pgvector query, filtered by jurisdiction |
| Vector store | pgvector on Postgres (Supabase or Neon free tier) |
| Reranker | cross-encoder, `ms-marco-MiniLM` class, local |
| LLM primary | Groq API (`AsyncGroq`), direct — no local-first guessing/timeout on the live path |
| LLM offline fallback | Ollama, local quantized model, gated behind `OFFLINE_MODE=true` — explicit operator flag for venue WiFi failure, not an auto-detected condition |
| Translation | Sarvam AI (have working access now). PS names Bhashini specifically — switch if a Bhashini key arrives before the demo. Not wired up yet; frontend and demo prep come first. |
| Frontend | React / Next.js |
| Hosting | Render or Railway (backend, `Procfile` — `WEB_CONCURRENCY` workers, default 2: each worker loads its own copy of the embedding + cross-encoder models in memory, so raise it only if the host has RAM to match), Vercel (frontend) |

**Groq is the default primary as of the async rewrite, not Ollama** — this
flips the original "local-first" decision below. Reason: on the dev machine,
Ollama's GPU path crashes (CUDA driver mismatch), forcing CPU-only inference
measured at ~163s per grounded-generation call. That was costing every
request a real, observed delay, not a hypothetical one. `OFFLINE_MODE=true`
still exists for the venue-WiFi-fails scenario the original decision was
protecting against — it's now an explicit flag instead of a per-request
guess-and-timeout.

## Repo layout

```
ip-sakti/
├── idea.md
├── backend/
│   ├── ingestion/        loader.py, chunker.py, indexer.py    [DONE, tested]
│   ├── retrieval/        bm25_search.py, dense_search.py, fusion.py, reranker.py
│   ├── generation/       prompts.py, llm_client.py, citation.py
│   ├── graph/            state.py, nodes.py, build_graph.py
│   ├── api/              main.py
│   ├── Procfile          multi-worker launch command (Render/Railway)
│   ├── requirements.txt
│   └── .env.example
├── frontend/
├── data/                 India-jurisdiction source PDFs (gitignored)
│   └── international/    international-jurisdiction PDFs (gitignored, placeholder — see its README)
└── docs/
```

## Build order

Strictly sequential: ingestion → retrieval → reranker → generation → citation → graph → api → deploy.

Parallel: data collection, frontend (against mock responses), presentation.

**Status:** ingestion is written and tested. Next is `backend/retrieval/`.

## Rules for this repo

- Every chunk carries `source_file`, `page_number`, `section_heading`, `chunk_id`. If any is missing, citations break — validate, don't paper over it.
- The BM25 tokenizer used at index time and query time must be identical. If they drift, BM25 silently stops matching. It currently lives in `ingestion/indexer.py::tokenize` — import it, don't rewrite it.
- The BM25 pickle stores `chunk_ids` in the same order as the corpus. Score positions map back to ids by index.
- Never commit `.env`, PDFs, or the BM25 pickle.
- Test each module standalone (`python -m ingestion.chunker <pdf>`) before wiring the next one.
- Prefer failing loudly over silently returning empty results.
- Every retrieval/generation function that does I/O (LLM calls, pgvector queries) is `async def`. If you add a new one, make it async too — a sync blocking call anywhere in this chain stalls the whole event loop, not just its own request.
- `jurisdiction` ("india" or "international") is the single source of truth in `ingestion/indexer.py::JURISDICTIONS` — the DB `CHECK` constraint, `QueryRequest.jurisdiction`'s pydantic `Literal`, and `loader.py`'s folder tagging must all agree with it.

## Demo requirements

- 5–6 verified questions that answer well
- One deliberately out-of-scope question, to show abstention as a feature
- The fully offline path (local Ollama, no internet) must work
- A backup demo video
