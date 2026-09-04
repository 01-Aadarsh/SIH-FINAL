# IP-SAKTI Sahayak — Project Context

This file gives you (Claude Code) the standing context for this repo. Read it before writing anything.

## What this project is

Smart India Hackathon 2026, problem statement **SIH26045**, Ministry of Ayush.

**IP-SAKTI Sahayak** — a multilingual, RAG-based, source-cited AI assistant for Intellectual Property and regulatory guidance in Ayurveda.

A user asks a question about Ayurveda-related IP or regulatory rules. The system retrieves relevant text from official Indian government documents and answers using only that text, showing exactly which document, page and section each answer came from. If the answer isn't in the documents, it says so instead of guessing.

**Scope:** National (Indian) framework is what's actually indexed. International jurisdiction routing exists end to end (API field, DB column, per-jurisdiction BM25 index, dense-retrieval SQL filter) and correctly abstains rather than crashing or defaulting to Indian law — but `data/international/` has no documents in it yet (WIPO GRATK Treaty 2024, Nagoya Protocol, Budapest Treaty, PCT — see its README for what's expected and why none of it can be fabricated here). Treat "international" as scaffolded, not built.

## Why the architecture is what it is

Independent evaluations of commercial legal AI tools found hallucination rates of 17–33% even with RAG in place. In a regulatory domain a confidently wrong answer is worse than no answer. Four decisions follow from that:

1. **Hybrid retrieval, not dense-only.** BM25 (exact terms, rule numbers, section references) fused with dense embeddings (semantic similarity) via Reciprocal Rank Fusion. Legal text needs exact matching, not just "similar meaning."

2. **Cross-encoder reranking.** Re-scores candidates for real relevance before they reach the LLM.

3. **Programmatic citations — the most important rule in this repo.** The LLM never writes citations. Our code tracks which chunks were retrieved, with their `source_file`, `page_number` and `section_heading`, and attaches those. A citation cannot be hallucinated if the model never generates it. **Never** change this to have the model emit citations.

4. **Abstention guardrail.** If retrieved context doesn't contain the answer, respond "not found in my sources" and ask one clarifying question. Never fill the gap from model knowledge.

**Orchestration:** LangGraph as a **deterministic DAG**, not an autonomous agent loop. Fixed graph, traceable, debuggable. Do not introduce unbounded cycles.

## Pipeline

```
User query (+ jurisdiction: india | international, + language)
  → Query rewriter (standalone question using chat history)
  → Formulation triage (deterministic keyword classifier — see below)
  → Hybrid retrieval (BM25 + dense, both filtered to jurisdiction at the source) → top 20
  → Cross-encoder reranker (calibrated sigmoid confidence)                      → top 5
  → Grounded generation (LLM, retrieved text + formulation framing — streamed as SSE tokens on /query/stream)
  → Citation attacher (code-attached, verified)
  → Answer + sources  |  or  Abstains
```

`jurisdiction=international` currently abstains on every query — no
international documents are indexed yet (`data/international/` is a
placeholder, see its README). That's the correct behavior, not a bug: the
alternative (silently falling back to Indian law, or crashing) would be
worse than an honest "not found." BM25 is now genuinely partitioned per
jurisdiction (`ingestion/indexer.py::build_bm25` builds one BM25Okapi per
jurisdiction, not one shared index post-filtered afterward) — the filter
applies during retrieval, not as cleanup after.

**Formulation triage** (`backend/graph/formulation.py`) classifies every
question into one of 5 categories (classical / proprietary (P&P) /
phytopharmaceutical / Ayurveda-Aahar / cosmetic) via keyword matching — not
an LLM call, deliberately: this project's retry mechanism exists because
LLM-driven decisions aren't perfectly reproducible run to run (see below),
and a second LLM call for triage would reintroduce that one step earlier.
The category + its mapped statutory tags get injected into the generation
prompt as advisory framing, never as ground truth the model cites. A
matching best-effort tagger (`ingestion/chunker.py::tag_statutory_metadata`)
keyword/filename-tags chunks at ingestion time with the same tag
vocabulary — 572/2907 chunks currently carry at least one tag. Both the
classifier and the tagger are a first pass a domain expert should review,
not a finished legal taxonomy — said plainly in both modules' docstrings.

**Retrieval determinism**: BM25's tokenizer now applies IPR/AYUSH domain
synonym normalization (`ingestion/indexer.py::DOMAIN_SYNONYMS` — trademark
↔ trade mark, IPR ↔ intellectual property rights, TK ↔ traditional
knowledge, BD Act ↔ Biological Diversity Act, and others) before indexing
and before every query, via the same shared `tokenize()` function so the
two can't drift apart. Found and fixed from a real, reproduced bug: "What
is a trademark?" scored the actual Trade Marks Act far below unrelated
documents, because the Act's own text says "trade marks" (two words) and
BM25 is exact-token matching. `tests/test_retrieval_determinism.py` proves
(bit-identical scores, 10 runs) that retrieve()/rerank_node() are now fully
deterministic — the remaining source of flakiness is exclusively the
bounded retry's own LLM-driven rephrase step, which fires deterministically
now (same decision every run) but still isn't itself reproducible when it
does fire. That gap is real and stayed out of scope for this pass — the
test suite says so directly rather than claiming it's fixed.

The cross-encoder reranker (`backend/retrieval/reranker.py`) outputs a
calibrated `sigmoid(raw_logit)` confidence in [0,1] now, not a raw
unbounded logit — `RERANK_SCORE_THRESHOLD = 0.15`, set against a real
(if small) empirical spread: on-topic queries scored 0.92-0.999, queries
with no real answer in this corpus scored ~0.000. A `weak_grounding` flag
(`graph/nodes.py::rerank_node`) fires when BM25 found a strong lexical
match but cross-encoder confidence is still low — a structured signal that
retrieval/reranking likely underperformed on that specific query, distinct
from the corpus genuinely lacking an answer, surfaced in the API response
rather than only visible as a generic abstention.

The graph itself (`backend/graph/build_graph.py`) has one new node
(triage_formulation, sync, no I/O) beyond the async rewrite from before —
same bounded single retry, same deterministic-DAG shape otherwise. Driven
by `compiled_graph.ainvoke()`, not `.invoke()` via a thread pool — see
`backend/api/main.py`. Every node that does I/O (LLM calls, pgvector
queries) is a real `async def`, not a sync function offloaded to a thread.

**Multilingual is wired into `/query` now** (`backend/api/translation.py`,
Sarvam AI): question translates to English before retrieval, answer
translates back to `QueryRequest.language` after generation. Retrieval and
the LLM prompt never see anything but English — `language` only affects
the two translation calls at the edges. Not wired into `/query/stream` yet
(translating a live token stream is a separate, harder problem —
sentence-boundary detection against a partial buffer). Sarvam's
`mayura:v1` model hard-caps input at exactly 1000 characters — confirmed
against the live API, not from docs — so both directions chunk text at
sentence boundaries and translate the pieces concurrently
(`api/text_chunking.py`, shared with TTS below). A same-language request
(the `en-IN` default) skips translation entirely: zero added latency,
identical behavior to before this existed. A fixed lexicon of Ayurvedic
technical terms (Churna, Bhasma, Taila, Kwatha, Rasa Shastra, Asava,
Arishta — Latin-script variants and Devanagari) is protected around every
Sarvam call: swapped for an opaque placeholder before translation, restored
to the canonical English spelling after, so Sarvam never sees the term at
all and can't transliterate or gloss it into something else. Verified
against the live API both directions, including a Devanagari-script input
("भस्म" → placeholder → translated → restored to "Bhasma", not "ash" or any
other approximation).

**TTS is opt-in on `/query`** (`QueryRequest.synthesize_audio`,
`backend/api/tts.py`, Groq). English-only, always — Groq's TTS models
(`canopylabs/orpheus-v1-english`, `canopylabs/orpheus-arabic-saudi`,
confirmed by querying this project's own account) have no Indian-language
voice, so it reads the pre-translation English answer regardless of
`language`. **Not yet functional**: this Groq account hasn't accepted
`canopylabs/orpheus-v1-english`'s model terms, which blocks even
discovering a valid voice name — `GROQ_TTS_VOICE` is deliberately unset
rather than guessed. `/query` degrades gracefully either way
(`audio_base64: null`, never a failed request) — see `env.example.txt` for
the one manual step (visit the Groq Playground, accept the terms, set
`GROQ_TTS_VOICE`) that turns this on.

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
| Translation | Sarvam AI, wired into `/query` (question in, answer out — see below). PS names Bhashini specifically — switch if a Bhashini key arrives before the demo. |
| TTS | Groq (`canopylabs/orpheus-v1-english`), opt-in on `/query`, English-only. Not yet functional pending model-terms acceptance on the Groq account — see below. |
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
│   ├── graph/            state.py, nodes.py, build_graph.py, formulation.py
│   ├── api/              main.py, translation.py, tts.py, text_chunking.py
│   ├── tests/            test_retrieval_determinism.py (pytest)
│   ├── pytest.ini
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
