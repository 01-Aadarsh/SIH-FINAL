# IP-SAKTI Sahayak — Project Context

This file gives you (Claude Code) the standing context for this repo. Read it before writing anything.

## What this project is

Smart India Hackathon 2026, problem statement **SIH26045**, Ministry of Ayush.

**IP-SAKTI Sahayak** — a multilingual, RAG-based, source-cited AI assistant for Intellectual Property and regulatory guidance in Ayurveda.

A user asks a question about Ayurveda-related IP or regulatory rules. The system retrieves relevant text from official Indian government documents and answers using only that text, showing exactly which document, page and section each answer came from. If the answer isn't in the documents, it says so instead of guessing.

**Scope:** National (Indian) framework is the primary corpus. International jurisdiction routing (API field, DB column, per-jurisdiction BM25 index, dense-retrieval SQL filter) is now backed by real documents too — WIPO GRATK Treaty 2024, Nagoya Protocol, Budapest Treaty (full text + WIPO's own secretariat note), PCT full text, and a WIPO IGC mandate decision — see `data/international/README.md` for what's indexed and why nothing here is ever fabricated. It still correctly abstains, not crashes or defaults to Indian law, for anything genuinely outside that set (e.g. the IGC's deeper negotiating-history documents).

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

`jurisdiction=international` now answers for real, from 6 real documents
in `data/international/` (see its README) — abstention still fires
correctly for anything genuinely outside that set, rather than silently
falling back to Indian law or crashing. BM25 is genuinely partitioned per
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
`backend/api/tts.py`, Sarvam AI's Bulbul model). Originally built on Groq
(`canopylabs/orpheus-v1-english`) — **replaced**, not just left broken,
for two compounding reasons: (1) Groq's TTS models are English-only, so
even working it would read the pre-translation English answer regardless
of `language`, wrong for exactly the multilingual case this project cares
about; (2) this Groq account never accepted
`canopylabs/orpheus-v1-english`'s model terms, which blocks even
discovering a valid voice name, and that's a manual step requiring org
console access, not something fixable in code. Bulbul solves both:
`SARVAM_API_KEY` (already required for translation) is sufficient — no
separate key or manual approval — and it natively voices 11 of the
languages `api/translation.py` already translates into (see
`BULBUL_SUPPORTED_LANGUAGES`), so TTS now speaks the *translated* answer
in the user's actual selected language, not a fallback to English.
Languages Sarvam translates but Bulbul cannot voice (e.g. Sanskrit,
Manipuri) skip audio rather than mis-voicing — same fail-open contract as
translation. Verified against the live API in both English and Hindi
(real, decodable WAV audio, ~3s and ~2s respectively for short test
strings) — not assumed from docs alone. `/query` still degrades
gracefully in every failure case (`audio_base64: null`, never a failed
request).

**Hierarchical statutory chunking** (`backend/ingestion/chunker.py::
HierarchicalStatutoryChunker`) replaces the plain sliding-window chunker
for documents where it applies (10 of 22, gated by
`MIN_SECTIONS_TO_TRUST_HIERARCHICAL_PARSE` — a document without real
Section/clause structure falls back to the plain chunker rather than being
mis-chunked by a pattern that doesn't fit it). Each lettered/numbered
clause within a section becomes its own citable chunk — Trade_Marks_Act_
1999's Section 2(1)(zb) ("trade mark" means...) and Patents_Act_1970's
Section 3(p) (the TK exclusion) are now clean, complete, individually-
tagged chunks instead of buried inside a 2000-character window alongside a
dozen unrelated clauses. `graph/nodes.py::FUSED_TOP_K` is 40, not 20 — see
its comment for a real regression this exact change caused and fixed: a
previously-reliable flagship query ("What does Section 3(p) say about
traditional knowledge?") started abstaining right after switching to
clause-level chunking, because a short, isolated clause can rank decently
on BM25 or dense individually while still missing RRF's narrower top-20
cutoff. Caught by testing, not shipped on the strength of the chunker fix
alone — see `tests/test_retrieval_determinism.py::
test_section_3p_query_does_not_regress`. A separate, harder case
("What is a trademark?", already unreliable before this chunker existed —
see the retrieval-determinism section above) needs ~top-500 pooling to
reach its own definition clause; raising FUSED_TOP_K that far to chase one
already-marginal query wasn't judged worth doubling-plus every query's
reranking cost for, and is left as an honestly-documented open gap rather
than force-fixed.

Statutory tags (`ingestion/chunker.py::tag_statutory_metadata`) now also
match against the hierarchical chunker's exact "Section N. Title, clause
(x)" heading, not just body text — far more precise (a heading match means
"this chunk IS clause 3(p)", not "the text happens to mention 3(p)
somewhere"). Canonical tag taxonomy: `Patents_Act_Sec3p`,
`Patents_Act_Sec3d`, `Patents_Act_Sec3e`, `D&C_First_Schedule`,
`D&C_Rule_158B`, `BDA_Sec6_NBA_Approval`, `BDA_Sec7_SBB_Exemption`,
`TKDL`, `FSSAI_Ayurveda_Aahar_2022`, `No_Therapeutic_Claim`,
`Clinical_Validation`, plus the reserved-not-yet-firing
`WIPO_GRATK_Art3_Disclosure` (see `data/international/README.md`). Still a
best-effort keyword/heading heuristic, not authoritative legal
categorization — said directly in the module's own docstring.

**Formulation triage** (`graph/formulation.py::triage_formulation`,
wired into the graph as `triage_formulation_node`) now checks every
category pattern, not just the first match, so a question whose keywords
genuinely span two categories (e.g. "nutraceutical" + "proprietary
formulation") sets `needs_clarification=True` and a `clarifying_questions`
list, surfaced in the API response — informational, not blocking:
generation still answers, using the first-matched category. Deliberately
still not LLM-based, for the same reason as before: an LLM classifier here
would reintroduce non-determinism one step earlier than the retry does.

**Knowledge graph — first real slice of the PS's "stage 2"**
(`backend/graph_kg/build_kg.py`, `kg.py`, wired into the graph as
`expand_related_provisions_node`). A small graph over the corpus's own
statutory tags (the same tag vocabulary `chunker.py::tag_statutory_metadata`
already produces), with three edge types, each labeled honestly by how it
was produced:

1. `co_occurrence` — DATA-DERIVED. Two tags get an edge if they ever
   appear together on the same real indexed chunk, counted directly from
   the database. Nothing authored, nothing guessed.
2. `category_tags` — REUSED, not new. `graph/formulation.py`'s already-
   reviewed `CATEGORY_STATUTORY_TAGS` mapping, stored for the graph
   lookup rather than duplicated.
3. `cross_jurisdiction` — the one genuinely new piece of content: a small,
   explicitly authored table (`CROSS_JURISDICTION_PAIRS`) pairing a
   domestic tag with an international tag that covers the same real-world
   regulatory concern — e.g. the domestic NBA-approval pathway
   (`BDA_Sec6_NBA_Approval`) and the Nagoya Protocol's ABS Clearing-House
   (`Nagoya_ABS_Clearing_House`); Section 3(p)'s domestic TK patent bar
   and the WIPO GRATK Treaty's mandatory-disclosure and TK/genetic-
   resources coverage. This is a STRUCTURAL claim ("these two already-
   indexed, already-citable provisions are about the same topic"), never
   new legal text, and — same as every other heuristic tag rule in this
   codebase — a first pass a domain expert should review, not authoritative
   cross-referencing. `build_kg.py` fails loudly if any pair references a
   tag that doesn't actually exist in the live index, the same philosophy
   as `indexer.py`'s chunk-metadata validation.

Every tag resolves to one real example chunk from the live index, so a
"related provision" is always a pointer to something actually retrievable
and citable — never a bare tag name. Surfaced as `related_provisions` on
both `/query` and `/query/stream` (cheap enough — no I/O, no LLM call — to
include on the stream too, unlike translation/TTS which have a real
technical reason to be `/query`-only). Verified end to end against the
live corpus: a `jurisdiction: "india"` Section 3(p) question's
`related_provisions` surfaces the WIPO GRATK Treaty's Article 1 and
Article 3 as international counterparts — the PS's "jurisdiction switch
keeps the two answer-sets visibly separate" requirement holds (`answer`/
`citations` stay India-only), while the system can still *point* across
jurisdictions where a real structural counterpart exists.

**What this is not**: a full knowledge graph (no entity/relation
extraction from free text, no reasoning over multi-hop paths beyond one
lookup) or agentic orchestration (no LLM decides what to query next —
this is a deterministic lookup, same "no LLM call where determinism
matters more" reasoning as `graph/formulation.py`'s triage). Treat this as
the first real increment toward the PS's stage-2 ask, not the finished
thing.

**Declined, not attempted**: fabricating the text of the Biological
Diversity (Amendment) Act 2023, Biological Diversity Rules 2024, or the
WIPO GRATK Treaty 2024 from memory to seed as corpus content. This system's
core design is "answer only from real retrieved text, abstain rather than
guess" — writing out specific section numbers and provisos from training-
data recollection and indexing them as if they were the authoritative
statute text would be exactly the failure mode that design exists to
prevent, in a tool meant to inform real legal/regulatory decisions. If
real source PDFs for these become available, the ingestion pipeline
already handles them (`data/` for India, `data/international/` for
treaties) with no code changes needed. Note: the currently-indexed
`Biological_Diversity_Act_2002.pdf` already reflects some 2023 amendments
inline (e.g. "Ins. by Act 10 of 2023, s. 3, (w.e.f. 1-4-2024)" footnotes on
amended clauses) — it is not entirely absent from the corpus, just not
indexed as its own separate amendment-act document.

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
| TTS | Sarvam AI (`bulbul:v3`), opt-in on `/query`, speaks 11 languages (not English-only) — see below. |
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
│   ├── graph_kg/         build_kg.py, kg.py — knowledge-graph enrichment (related_provisions)
│   ├── api/              main.py, translation.py, tts.py, text_chunking.py
│   ├── tests/            test_retrieval_determinism.py, test_knowledge_graph.py, etc. (pytest)
│   ├── pytest.ini
│   ├── Procfile          multi-worker launch command (Render/Railway)
│   ├── requirements.txt
│   └── .env.example
├── frontend/
├── data/                 India-jurisdiction source PDFs (gitignored)
│   └── international/    international-jurisdiction PDFs (gitignored — see its README for what's indexed)
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
- Test each module standalone (`python -m ingestion.chunker <pdf>`) before wiring the next one.
- Prefer failing loudly over silently returning empty results.
- Every retrieval/generation function that does I/O (LLM calls, pgvector queries) is `async def`. If you add a new one, make it async too — a sync blocking call anywhere in this chain stalls the whole event loop, not just its own request.
- `jurisdiction` ("india" or "international") is the single source of truth in `ingestion/indexer.py::JURISDICTIONS` — the DB `CHECK` constraint, `QueryRequest.jurisdiction`'s pydantic `Literal`, and `loader.py`'s folder tagging must all agree with it.
- Never commit `.env`, PDFs, the BM25 pickle, or `indexes/knowledge_graph.json`.
- Re-run `python -m graph_kg.build_kg` after any `ingestion.indexer` run that changes tagging (a new `chunker.py` rule, a new document) — it reads the live `statutory_tags` column, so it drifts from reality otherwise. Not run automatically as part of the indexer, by choice: ingestion should stay focused on what it already does; a separate command matches this repo's existing "test each module standalone" convention.
- Adding a pair to `graph_kg/build_kg.py::CROSS_JURISDICTION_PAIRS` requires both tags to already exist in the live index — `build()` fails loudly otherwise. Don't add a pair speculatively before its source document is actually indexed.

## Demo requirements

- 5–6 verified questions that answer well
- One deliberately out-of-scope question, to show abstention as a feature
- The fully offline path (local Ollama, no internet) must work
- A backup demo video
