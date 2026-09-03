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
User query
  → Query rewriter (standalone question using chat history)
  → Hybrid retrieval (BM25 + dense, merged by RRF)  → top 20
  → Cross-encoder reranker                          → top 5
  → Grounded generation (LLM, retrieved text only)
  → Citation attacher (code-attached, verified)
  → Answer + sources  |  or  Abstains
```

## Tech stack

| Layer | Choice |
|---|---|
| Orchestration | LangGraph (deterministic DAG) |
| API | FastAPI |
| Sparse retrieval | rank_bm25 |
| Dense retrieval | sentence-transformers (`all-MiniLM-L6-v2`, 384 dims) |
| Vector store | pgvector on Postgres (Supabase or Neon free tier) |
| Reranker | cross-encoder, `ms-marco-MiniLM` class, local |
| LLM primary | Ollama, local quantized model — works offline |
| LLM fallback | Groq API — used when local is unavailable |
| Translation | Sarvam AI (have working access now). PS names Bhashini specifically — switch if a Bhashini key arrives before the demo. Not wired up yet; frontend and demo prep come first. |
| Frontend | React / Next.js |
| Hosting | Render or Railway (backend), Vercel (frontend) |

Local model is primary because venue WiFi fails. Cloud is an enhancement, never a dependency.

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
│   ├── requirements.txt
│   └── .env.example
├── frontend/
├── data/                 source PDFs (gitignored)
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

## Demo requirements

- 5–6 verified questions that answer well
- One deliberately out-of-scope question, to show abstention as a feature
- The fully offline path (local Ollama, no internet) must work
- A backup demo video
