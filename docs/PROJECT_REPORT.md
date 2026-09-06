# IP-SAKTI Sahayak — Project Report

SIH26045, Ministry of AYUSH. This document explains **every file in the
repository** — what it does, why it exists, and how it connects to
everything else. For architecture rationale and the reasoning behind
specific design decisions, see [`idea.md`](../idea.md); for the exact HTTP
request/response contract, see [`API_CONTRACT.md`](API_CONTRACT.md). This
file is the map that ties both together.

---

## 1. What the system does, in one paragraph

A user asks a question about Ayurveda-related IP or regulatory rules, by
text or by voice, in one of 23 languages. The system retrieves the actual
relevant passages from a fixed corpus of 30 real government/treaty
documents (never from the model's own training knowledge), reranks them
for relevance, and generates an answer using only that retrieved text —
with a citation to the exact document, page, and section for every claim,
attached by code, never by the LLM. If nothing in the corpus answers the
question, it says so explicitly instead of guessing. Alongside the answer,
it surfaces real cross-jurisdiction pointers (a knowledge graph over the
corpus's own statutory tags) and the actual government forms the question
implies the user will need next.

---

## 2. Architecture diagram

See the diagram in [`README.md`](../README.md#architecture-diagram) for
the full system view (client → API → orchestration → retrieval →
generation → enrichment → data stores → offline ingestion). This report
goes one level deeper: file by file.

---

## 3. Backend — `backend/`

### 3.1 `backend/run.py`

The actual entrypoint on Windows. Sets the asyncio event loop policy to
`WindowsSelectorEventLoopPolicy` *before* importing anything else, then
runs uvicorn programmatically. Necessary because a bare
`uvicorn api.main:app` creates its event loop before the app module is
imported, which breaks psycopg's async mode under Windows' default
`ProactorEventLoop`. On Linux/macOS, plain `uvicorn api.main:app --reload`
works fine and this file isn't needed.

### 3.2 `backend/ingestion/` — turning PDFs into searchable, citable chunks

| File | Role |
|---|---|
| `loader.py` | Extracts text page-by-page from every PDF in `data/` (via `pdfplumber`). PDFs directly in `data/` are tagged `jurisdiction="india"`; PDFs under `data/international/` are tagged `jurisdiction="international"` — the **folder location** is the source of truth, never filename guessing. Warns (doesn't fail) on a page with almost no extractable text (a likely scan). |
| `chunker.py` | Splits loaded pages into chunks. Two strategies: `HierarchicalStatutoryChunker` (for documents with real "Section N" structure — 11 of 30 documents qualify) makes each numbered/lettered clause its own chunk, so e.g. Section 3(p) is a clean, complete, individually-tagged chunk instead of buried in a fixed-size window. Documents without that structure fall back to a plain sliding-window chunker. Also runs `tag_statutory_metadata()` — a best-effort keyword/heading classifier that attaches tags like `Patents_Act_Sec3p`, `BDA_Sec6_NBA_Approval`, `TKDL` to each chunk (see `_compile_statutory_tag_rules()` for the full, explicitly-commented rule table). Every chunk is validated to carry `source_file`, `page_number`, `section_heading`, `chunk_id` — ingestion fails loudly if any is missing, since every downstream citation depends on it. |
| `indexer.py` | Orchestrates ingestion end to end: calls `loader`, calls `chunker`, embeds every chunk with `sentence-transformers/all-MiniLM-L6-v2`, writes them into Postgres/pgvector, and builds one `BM25Okapi` index **per jurisdiction** (not one shared index post-filtered — the partition is real, at build time). Also defines `tokenize()` (with `DOMAIN_SYNONYMS` — e.g. "trademark" ↔ "trade mark" — so BM25 doesn't miss a real match over a one-character tokenization difference) and `connect()`, both reused by other modules rather than reimplemented. `python -m ingestion.indexer --reset` rebuilds everything from scratch. |

### 3.3 `backend/retrieval/` — hybrid search

| File | Role |
|---|---|
| `bm25_search.py` | Loads the pickled BM25 index for a jurisdiction, tokenizes the query with the *same* `tokenize()` indexing used, returns the top-k lexical matches. Exact-term/section-number matching — what dense embeddings alone miss. |
| `dense_search.py` | Embeds the query with the same model used at index time, runs a cosine-similarity query against pgvector, filtered to the requested jurisdiction at the SQL level (not post-filtered in Python). |
| `fusion.py` | Combines BM25 and dense results via **Reciprocal Rank Fusion** (`1/(k+rank)`, k=60) — a chunk ranking well on *both* signals rises to the top, rewarding agreement over either signal alone. Backfills full chunk metadata (needed for citations) for the fused top-k. |
| `reranker.py` | Loads a cross-encoder (`cross-encoder/ms-marco-MiniLM-L-6-v2`), scores each (query, chunk) pair directly (more accurate than comparing separate embeddings), and outputs a **calibrated `sigmoid(raw_logit)` confidence in [0,1]** — empirically, on-topic queries score 0.92–0.999, queries with no real answer score ~0.000, which is what makes a fixed threshold (`RERANK_SCORE_THRESHOLD = 0.15`) meaningful. |

### 3.4 `backend/generation/` — grounded answers with unforgeable citations

| File | Role |
|---|---|
| `prompts.py` | The system prompt: answer *only* from the provided context, never from outside knowledge, and if the context doesn't answer the question, emit the exact literal string in `ABSTENTION_MARKER` ("I could not find this in my sources.") followed by one clarifying question. Also `append_disclaimer()` — appends the standing "information, not legal advice" disclaimer to every answer, including abstentions, satisfying the PS's explicit requirement for one. |
| `llm_client.py` | The LLM backend. **Groq is the direct primary path**, with automatic **multi-key rotation**: `GROQ_API_KEY`, plus `GROQ_API_KEY_2`, `GROQ_API_KEY_3`, ... — on a `RateLimitError` (429, e.g. a daily token quota exhausted — a real failure hit during this project's own benchmark runs, not hypothetical), the next configured key is tried automatically, with rotation state persisted for the life of the process. Local Ollama only runs when `OFFLINE_MODE=true` (an explicit operator flag for venue-WiFi-failure, not a per-request guess — Ollama's GPU path crashes on the dev machine this was built on, making it a ~163s-per-call tax if tried automatically). Provides both non-streaming (`acomplete`/`agenerate`) and streaming (`astream_complete`/`astream_generate`) paths. |
| `citation.py` | **The single most important file in this repo.** `attach_citations()` takes the chunks that were *actually retrieved* and builds the source list from their real metadata — the LLM's own output is never parsed for citations. A citation here cannot be hallucinated because it never passes through the model. Also `is_abstention()` (checks for the exact `ABSTENTION_MARKER` prefix) and `find_ungrounded_references()` (a diagnostic, not an editor: flags a "Section N"-shaped claim in the answer that doesn't appear anywhere in the retrieved text, logged for a human to review, never used to silently rewrite the answer). |

### 3.5 `backend/graph/` — deterministic orchestration

| File | Role |
|---|---|
| `state.py` | `GraphState` — the single `TypedDict` threaded through every node. `DEFAULT_FLAGS` / `DEFAULT_JURISDICTION` are the seed values every caller must start from. |
| `formulation.py` | Deterministic **keyword classifier** (`triage_formulation()`) — not an LLM call, deliberately, since this project's whole retry mechanism exists because LLM decisions aren't perfectly reproducible run to run, and a second LLM call here would reintroduce that one step earlier. Classifies a question into one of 6 formulation categories (`classical`, `proprietary`, `phytopharmaceutical`, `ayurveda_aahar`, `cosmetic`, `new_or_non_classical_drug`) and maps each to a fixed set of statutory tags (`CATEGORY_STATUTORY_TAGS`) injected into the generation prompt as framing, never as ground truth the model cites. Detects genuine ambiguity (2+ categories match) and surfaces a clarifying question — informational, not blocking. |
| `nodes.py` | One function per DAG stage: `rewrite_query` (standalone-question rewrite from chat history), `triage_formulation_node`, `retrieve` (hybrid retrieval, jurisdiction-scoped), `rerank_node` (also computes the `weak_grounding` diagnostic — BM25 found a strong lexical hit but the reranker wasn't confident, a distinct signal from "the corpus genuinely has nothing"), `should_retry` (the bounded-retry conditional — fires at most once, ever), `retry_rewrite_query`, `generate_answer`, `attach_citations_node`, `expand_related_provisions_node` (the knowledge-graph enrichment — see 3.6). Also `run_retrieval_stage()`, a composed helper used by the SSE streaming endpoint. |
| `build_graph.py` | Wires the above into the actual `StateGraph`: `rewrite_query → triage_formulation → retrieve → rerank → (generate_answer \| retry_rewrite_query→retrieve→rerank, once) → generate_answer → attach_citations → expand_related_provisions → END`. A fixed, traceable DAG — not an autonomous agent loop. |

### 3.6 `backend/graph_kg/` — knowledge graph (the PS's "stage 2", first real slice)

| File | Role |
|---|---|
| `build_kg.py` | Builds a small graph over the corpus's own statutory tags with three edge types, each labeled by how it was produced: (1) `co_occurrence` — **data-derived**, counted directly from which tags actually appear together on the same real chunk; (2) `category_tags` — **reused**, `graph/formulation.py`'s already-reviewed category→tag mapping, not duplicated; (3) `cross_jurisdiction` — the one genuinely new piece of content, a small explicit table (`CROSS_JURISDICTION_PAIRS`) pairing a domestic tag with an international tag covering the same real-world concern (e.g. `BDA_Sec6_NBA_Approval` ↔ `Nagoya_ABS_Clearing_House`). Every tag resolves to one real example chunk from the live index. Fails loudly if a configured pair references a tag that doesn't actually exist in the index. Run via `python -m graph_kg.build_kg`. |
| `kg.py` | The read side — `related_provisions_for(tags)` looks up cross-jurisdiction counterparts (priority 0) and real co-occurrence (priority 1, above a minimum count) for a set of input tags, capped and deduped. Pure in-memory lookup, no I/O, no LLM call — degrades to `[]` on any missing/stale graph file rather than raising (fail-open, same as translation/TTS). |

### 3.7 `backend/compliance/` — Form & Registry Navigator

| File | Role |
|---|---|
| `form_navigator.py` | A hardcoded but **verified** catalog of 11 real NBA/IPO forms (`FORM_CATALOG`) with `match_forms()` doing deterministic keyword + formulation-category + statutory-tag matching — no LLM call, same reasoning as the formulation classifier. The module's own docstring documents a real correction made against an earlier assumption: applying for a patent based on Indian biological resources is real **NBA Form 7** (verified against the actual indexed `BD_Rules_2024.pdf`, Rule 16(1)(a)), not "Form 3" as first assumed; similarly IPO's early publication (**Form 9**, Rule 24A) and expedited examination (**Form 18A**, Rule 24C) are two separate real forms, not one "Form 8" (which is actually the unrelated "mention of inventor" form). Every catalog entry is a domestic Indian registry — an `international`-jurisdiction lookup returns `[]` rather than guessing at a non-existent equivalent. |

### 3.8 `backend/api/` — the HTTP layer

| File | Role |
|---|---|
| `main.py` | The FastAPI app. Defines every Pydantic request/response model (`QueryRequest`, `QueryResponse`, `Citation`, `Flags`, `FormCard`, `RelatedProvision`, `TranscribeResponse`, `VoiceQueryResponse`, etc.) and every endpoint: `GET /health`, `GET /sources/{filename}` (serves a citation's source PDF, path-traversal-protected — resolved by basename only, only ever from `data/` or `data/international/`), `POST /query` (thin wrapper around `run_query()`), `POST /query/stream` (same pipeline, SSE token streaming), `POST /ingest` (admin, optionally `X-Admin-Token`-gated), `POST /api/v1/voice/transcribe`, `POST /api/v1/voice/query` (ASR → `run_query()`, composed), `GET /api/v1/compliance/forms`. `run_query()` is the actual pipeline: translate question → English → invoke the compiled graph → translate answer back → optional TTS → `match_forms()` (gated on `flags.abstained`, using the *chunk-level* tags that actually grounded this answer, not the coarser per-category tag list — a real false-positive bug found via `scripts/evaluate_pipeline.py` and fixed here). |
| `translation.py` | Sarvam AI bridge. Question → English before retrieval, English answer → the requested language after generation — retrieval and the LLM prompt never see anything but English. Chunks text at 950 chars (Sarvam's confirmed hard limit is 1000) and protects a fixed lexicon of Ayurvedic terms (Churna, Bhasma, Taila, ...) with numeric placeholders before translation so Sarvam can't transliterate or gloss them into something else. Fails open — any failure returns the original text untranslated, never a 500. |
| `tts.py` | Sarvam's **Bulbul** text-to-speech (replaced an earlier Groq-based implementation that needed manual model-terms approval and was English-only regardless). Speaks the answer in 11 of the 23 languages translation supports (`BULBUL_SUPPORTED_LANGUAGES`); anything else skips audio rather than mis-voicing. Chunks at 2200 chars (bulbul:v3's limit is 2500) and concatenates the resulting WAV audio at the PCM-frame level. Also fails open — `audio_base64: null`, never a failed request. |
| `asr.py` | Sarvam's **Saaras** speech-to-text. `transcribe_audio()` validates the file extension (`.wav`/`.mp3`/`.m4a`/`.webm`) and `language_code` before ever calling Sarvam, then posts multipart form data. Unlike translation/TTS, this does **not** fail open — there's no reasonable fallback transcript, so any failure raises `RuntimeError` with the real cause. |
| `text_chunking.py` | `split_text()` — sentence-boundary-aware chunking shared by `translation.py` and `tts.py`, since both APIs reject input past a hard character limit and a real answer routinely exceeds either. Only hard-splits a single sentence that's already longer than the limit on its own. |

### 3.9 `backend/scripts/evaluate_pipeline.py` — offline benchmark

Runs `data/eval_benchmark.json`'s curated queries through `api.main.run_query()` — the *exact* function `/query` itself calls, not a reimplementation. Reports **citation precision** (did the answer/citations actually contain an expected statutory marker — checked against the real `source_file`/`section_heading`/`chunk_id` fields, with underscores normalized to spaces so `Biological_Diversity_Act_2002.pdf` correctly matches the phrase "Biological Diversity Act"), **abstention faithfulness** (did out-of-scope trap queries correctly abstain, and did in-scope queries *not* incorrectly abstain), and **latency**. Prints a `tabulate`-formatted CLI table and writes a full JSON report. Deliberately not tuned to make every query pass — several queries are realistic tests of whether retrieval generalizes past exact statute phrasing, not softballs.

### 3.10 `backend/tests/` — the automated suite (63 tests, run against the real live pipeline)

| File | Covers |
|---|---|
| `test_retrieval_determinism.py` | BM25/dense/rerank are pure functions of the corpus and query — bit-identical scores across repeated runs. Also the real regressions this proves don't recur: the "trademark" vs "trade marks" tokenization mismatch, and `FUSED_TOP_K` needing to be 40 (not 20) after hierarchical chunking made individual clauses shorter and lexically sparser. |
| `test_formulation_and_response_contract.py` | The formulation classifier's category patterns, the disclaimer's append/idempotence behavior. |
| `test_statutory_and_regimes.py` | Specific statutory-tag/heading classification cases (WIPO GRATK Article 3, BDA 2023 exemption terminology, etc). |
| `test_translation_term_protection.py` | Ayurvedic terms survive a real Sarvam translation round-trip unchanged, including a Devanagari-script input. |
| `test_api_contract.py` | **Drift detector** — `QueryRequest`/`QueryResponse`'s actual Pydantic fields must exactly match a hand-maintained set here, so an added field that isn't documented (or vice versa) fails the suite immediately rather than silently drifting from `API_CONTRACT.md`. |
| `test_knowledge_graph.py` | The graph file builds/loads; a domestic TK tag really does surface a real WIPO GRATK counterpart; abstention correctly suppresses related-provisions. |
| `test_forms.py` | The corrected NBA/IPO form numbers stay corrected (regression tests naming the exact real Rule/Section each maps to); the real false-positive bug (forms attaching to unrelated/abstained answers) stays fixed, checked against the live pipeline via `run_query()`. |
| `test_asr.py` | `transcribe_audio()`'s request/response handling and error mapping, mocked against Sarvam (the one place this suite mocks rather than hits a live service — deliberately, since a true test needs real audio fixtures and this is testing our own code, not Sarvam's transcription accuracy) — plus both voice endpoints via `TestClient`. |
| `test_sources_endpoint.py` | `/sources/{filename}` serves real PDFs from both `data/` and `data/international/`, 404s on an unknown name, and rejects path-traversal attempts before ever touching the filesystem. |

---

## 4. Frontend — `frontend/`

A Next.js 15 / React / Tailwind app (originally built by a teammate against
an earlier assumed API shape; integrated and corrected against the real
backend contract in this pass — see the `git log` for exactly what
changed and why).

| File | Role |
|---|---|
| `src/app/page.tsx` | The root route — renders `IntakeScreen`, pushes to `/chat?jurisdiction=...&category=...` once the user picks a jurisdiction. |
| `src/app/chat/page.tsx` | Reads `jurisdiction`/`category` from the URL, redirects home if `jurisdiction` isn't a real value (`"india"` \| `"international"`), renders `ChatView`. |
| `src/app/layout.tsx` | Root HTML shell; mounts `FontScaleSync` once so every page respects a persisted text-size preference. |
| `src/components/IntakeScreen.tsx` | The landing screen — jurisdiction toggle (required) and formulation-category picker (optional, decorative theme). |
| `src/components/ChatView.tsx` | The chat state machine. Sends the user's raw question to `/query`, but folds a selected category in as a natural-language hint (`"... (regarding a {category} formulation)"`) on the *outgoing* request only — the backend has no dedicated category field (formulation is always triaged server-side from the question text), so this is what makes that UI control do something real instead of being silently dropped. |
| `src/components/ChatMessageBubble.tsx` | Renders one turn: the answer text, a confidence badge + formulation-category chip, a weak-grounding note, the abstention banner, a clarifying-question hint, then three card lists — Sources (citations), See also (related provisions), Forms you may need (actionable forms). |
| `src/components/CitationCard.tsx` | One citation — source file, page, section heading, and a "View source PDF" link (`sourceUrl()` → the real `/sources/{filename}` endpoint). |
| `src/components/RelatedProvisionCard.tsx` | One knowledge-graph cross-reference — labeled "International counterpart" or "Related provision" depending on `relation`. |
| `src/components/ActionableFormCard.tsx` | One government form match — agency, title, statutory mandate, deadline, required attachments, and a link to the real submission portal. |
| `src/components/ConfidenceBadge.tsx` | A 0–100% badge, colored by threshold; used once per assistant message against the real `confidence_score`. |
| `src/components/AbstentionBanner.tsx` | Shown when `flags.abstained` is true — explains this is an honest "not found," not a guess. |
| `src/components/SourceViewer.tsx` | The side panel (desktop) / full-screen overlay (mobile) that embeds the clicked citation's PDF via an `<iframe>`, deep-linked to the right page. |
| `src/components/Header.tsx` | Shows the active jurisdiction/category, a live backend-health indicator (polls `/health` every 20s), and "back to setup." |
| `src/components/LoadingState.tsx` | The waiting-for-answer bubble — rotates through real pipeline-stage phrases and shows elapsed seconds, since a genuinely grounded answer can take up to ~60–90s. |
| `src/components/FontScaleSync.tsx` / `src/hooks/useFontScale.ts` | Site-wide text-size control (A-/A/A+), persisted to `localStorage`. |
| `src/hooks/useElapsedSeconds.ts` | Ticks once a second while active; backs `LoadingState`'s elapsed-time display. |
| `src/lib/types.ts` | Every TypeScript type, kept in sync **by hand** with the backend's real Pydantic models — `Citation`, `Flags`, `RelatedProvision`, `FormCard`, `QueryRequest`/`QueryResponse` all mirror `docs/API_CONTRACT.md` exactly (this was the single biggest source of bugs before this integration pass: the original types had fields like `citation.confidence`/`citation.text` that don't exist on the real API, and `jurisdiction: "national"` instead of the real `"india"`). |
| `src/lib/api.ts` | The only file that talks to the backend. `query()` posts to `/query` with the real field names; `health()` polls `/health`; `sourceUrl()` builds a `/sources/{filename}` link. Client-side timeout (98s) set just above the backend's own 90s `REQUEST_TIMEOUT` so the server's informative 504 wins the race. |

**Known gap, not silently dropped**: voice input (the ASR endpoints) isn't wired into the chat UI yet — no recording/upload button exists. The backend endpoints (`/api/v1/voice/transcribe`, `/api/v1/voice/query`) are built, tested, and verified live; only the frontend control is missing. The language switcher in `IntakeScreen` is explicit UI-only decoration, marked "coming soon" by its own original author.

---

## 5. Data — `data/`

- 24 India-jurisdiction PDFs directly in `data/` (Patents Act 1970, GI Act, Trade Marks Act, Biological Diversity Act 2002 + 2023 amendment + 2024 Rules, Drugs & Cosmetics Act, FSSAI Ayurveda-Aahar notification, TKDL factsheet, Patent Office Manual, and more — the authoritative current list is always `ls data/*.pdf`, not hand-copied here since it drifts).
- 6 international-jurisdiction PDFs under `data/international/` — WIPO GRATK Treaty 2024, Nagoya Protocol, Budapest Treaty (both the full verbatim text and WIPO's own Secretariat summary note), PCT full text, and a WIPO IGC mandate-renewal decision. See `data/international/README.md` for exactly where each was sourced and verified.
- `data/eval_benchmark.json` — the 20-query ground-truth set `scripts/evaluate_pipeline.py` runs against: 5 domestic Section 3(p) non-patentability queries, 5 Biological Diversity Act/NBA compliance queries, 5 international-treaty queries, 5 out-of-scope hallucination traps.
- All PDFs are gitignored (`data/**/*.pdf`) — only the directory structure and this JSON/README content are tracked.

---

## 6. Everything a request touches, start to finish

1. **Frontend** sends `POST /query` with `{question, history, jurisdiction, language, synthesize_audio}`.
2. **`api/main.py::run_query()`**: `translation.py` converts a non-English question to English (no-op if already English).
3. The compiled **LangGraph DAG** (`graph/build_graph.py`) runs: rewrite → triage → retrieve (BM25 + dense, jurisdiction-scoped, fused by RRF) → rerank (cross-encoder, calibrated confidence) → \[bounded retry once, if weak\] → generate (Groq, multi-key rotation) → attach citations (from code, not the model) → expand related provisions (knowledge graph lookup).
4. `translation.py` converts the English answer back to the requested language; `tts.py` optionally synthesizes speech in that same language.
5. `compliance/form_navigator.py::match_forms()` matches the question/category/chunk-tags against the real NBA/IPO form catalog (skipped entirely on an abstention).
6. **`api/main.py`** assembles `QueryResponse` — answer, citations, flags, formulation_category, confidence_score, audio_base64, related_provisions, actionable_forms, needs_clarification/clarifying_questions — and returns it.
7. **Frontend** renders it; a citation's "View source PDF" link hits `GET /sources/{filename}`, serving the real PDF this claim was drawn from.

For the voice path: `POST /api/v1/voice/query` runs `asr.py::transcribe_audio()` first, then pipes the transcript into the exact same `run_query()` above — one implementation, not two.

---

## 7. How to run it locally, and how to test it

See [`README.md`](../README.md#setup) (backend + frontend setup) and
[`README.md`](../README.md#testing) (automated suite + evaluation
harness) — kept there, not duplicated here, so there's one place to look
for exact commands.
