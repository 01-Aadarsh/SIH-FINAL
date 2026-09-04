# IP-SAKTI Sahayak — Backend API Contract

For whoever is building the frontend. Every example response in this document
was copied verbatim from real `curl`/SSE output against a running server on
this machine (`python run.py`, Windows) — none of it is hand-written. If the
backend changes in a way that breaks these examples, that's a bug in this
doc, please flag it.

**This is a full rewrite, not an edit.** The previous version of this doc
predated: `/query/stream`, `jurisdiction`, `language`, `synthesize_audio`,
`formulation_category`, `needs_clarification`, `clarifying_questions`,
`audio_base64`, `flags.weak_grounding`, and a from-the-ground-up change to
which LLM backend answers by default (Groq direct, not Ollama-first) — which
also means the old timing table was describing a since-fixed slow path. If
you built anything against the old version, re-read this one; more changed
than any single diff would suggest.

**Since that rewrite, two more additions** (both present in every response
from `/query` and `/query/stream`'s `done` event, not optional): a
`confidence_score` float, and every `answer` now ends with a fixed
disclaimer sentence appended by the backend itself (not the LLM) — see
their dedicated sections below. `formulation_category` also gained a 6th
value, `new_or_non_classical_drug`.

## Running the backend locally

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate          # Windows; `source .venv/bin/activate` on Mac/Linux
pip install -r requirements.txt
cp env.example.txt .env         # then fill in DATABASE_URL, GROQ_API_KEY, SARVAM_API_KEY
python run.py                   # NOT `uvicorn api.main:app` directly on Windows — see below
```

**On Windows, use `python run.py`, not a bare `uvicorn api.main:app`.**
`uvicorn` creates its asyncio event loop before it imports the app, and
psycopg's async mode (used throughout retrieval) needs a selector-based
loop that Windows' default (Proactor) isn't — every `/query` would fail
with `psycopg.OperationalError`. `run.py` sets the correct event loop
policy before anything else is imported. `uvicorn api.main:app --reload`
still works fine for hot-reload dev on Linux/macOS, where this constraint
doesn't exist.

The corpus must already be indexed (`python -m ingestion.indexer --reset`) —
if the `chunks` table is empty, `/query` will still respond, but retrieval
will return nothing and the system will abstain on everything.

## Base URL

Local dev: `http://127.0.0.1:8000` (or whatever `PORT` you set — the examples
below were captured against a local run on port 8123, adjust accordingly).

No `/api` or `/v1` prefix — endpoints are mounted directly at the paths
below. There is no production URL yet (not deployed).

## CORS

Controlled by `CORS_ORIGINS` in `backend/.env`, comma-separated. Currently set
to `*` (all origins allowed) for local dev — nothing to configure on the
frontend side right now. Before deploying, this should be narrowed to the
actual frontend origin(s); if that happens, this doc will be updated with the
real value.

---

## Endpoints

### `GET /health`

No parameters. Used for hosting-platform health checks — confirms the
process is up, does not check DB/LLM connectivity.

**Response `200`**, real captured `time_total: 0.007s`:
```json
{"status": "ok"}
```

---

### `POST /query`

Single JSON response — the whole pipeline runs, then you get one answer.
See `/query/stream` below if you want tokens as they're generated instead
(recommended for a live chat UI — much better perceived latency).

**Request body** (`QueryRequest`):
```json
{
  "question": "string, required, min length 1",
  "history": [
    {"role": "user", "content": "string"},
    {"role": "assistant", "content": "string"}
  ],
  "jurisdiction": "india",
  "language": "en-IN",
  "synthesize_audio": false
}
```

| Field | Required? | Default | Notes |
|---|---|---|---|
| `question` | Yes | — | The current question. Min length 1 — empty string is a `422`. |
| `history` | No | `[]` | Prior turns, **oldest first**, current question NOT included (that's `question`). Used to rewrite `question` into a standalone query before retrieval (e.g. resolving "what about that section?" against the prior turn). Omit entirely or pass `[]` for a single-turn question. |
| `jurisdiction` | No | `"india"` | `"india"` or `"international"` only — anything else is a `422`, never silently coerced. `"international"` currently has zero indexed documents (see repo's `data/international/README.md`), so it will abstain on every question — that's correct behavior, not a bug, until real international documents are indexed. |
| `language` | No | `"en-IN"` | One of the 23 codes below. Language of `question`; also what `answer` gets translated back into. `"en-IN"` skips translation entirely (zero added latency) — that's the same behavior as before this field existed, so a frontend that never sets it needs no changes. |
| `synthesize_audio` | No | `false` | If `true`, also attempt to return `audio_base64` (see below). Adds real latency (an extra Groq call after generation) — leave `false` unless the UI actually has a play button visible. |

**Valid `language` codes** (Sarvam AI's supported set, English + 22 Indian languages):
`en-IN`, `hi-IN`, `bn-IN`, `gu-IN`, `kn-IN`, `ml-IN`, `mr-IN`, `od-IN`,
`pa-IN`, `ta-IN`, `te-IN`, `as-IN`, `brx-IN`, `doi-IN`, `kok-IN`, `ks-IN`,
`mai-IN`, `mni-IN`, `ne-IN`, `sa-IN`, `sat-IN`, `sd-IN`, `ur-IN`.

**Response `200`** (`QueryResponse`), real captured output for `{"question":
"What does Section 3(p) say about traditional knowledge?"}`, `time_total:
7.7s`:
```json
{
  "answer": "Section 3(p) provides that an invention which, in effect, is traditional knowledge—or merely an aggregation or duplication of the known properties of traditionally known component(s)—is not considered an invention and therefore is not patentable.\n\n*Disclaimer: This assistant provides source-grounded regulatory information for guidance and does not constitute formal legal advice.*",
  "citations": [ /* array of Citation objects, see below — can be empty */ ],
  "flags": {
    "abstained": false,
    "retried": false,
    "weak_grounding": false
  },
  "formulation_category": "classical",
  "confidence_score": 0.9971599613006763,
  "needs_clarification": false,
  "clarifying_questions": [],
  "audio_base64": null
}
```

| Field | Type | Notes |
|---|---|---|
| `answer` | string | In `language` if translation succeeded, English if `language` was `"en-IN"` or translation failed (silent fallback — never a `500` because a translation call failed). **Always ends with the disclaimer sentence** (`"\n\n*Disclaimer: ...*"`) — appended by the backend after generation, not written by the LLM, and appended even on an abstention. See dedicated section below; don't strip it client-side unless you have your own reason to. |
| `citations` | array | See Citation object below. Always `[]` when `flags.abstained` is `true`. |
| `flags.abstained` | boolean | **The authoritative way to detect an abstention — do not string-match `answer`.** See dedicated section below. |
| `flags.retried` | boolean | `true` if the backend internally retried retrieval once with a reworded query before answering/abstaining. Informational only. |
| `flags.weak_grounding` | boolean | **New.** `true` when the lexical search found what looks like a strong keyword match but the semantic reranker's confidence was still low — a signal that retrieval/reranking may have underperformed for this specific question, distinct from the corpus genuinely lacking an answer. Informational only, doesn't change `abstained`. **Can be `true` even on a genuinely out-of-scope question** (real captured example below) — don't build UI logic that treats this as a strong "was this actually answerable" signal on its own. |
| `formulation_category` | string | One of `classical`, `proprietary`, `phytopharmaceutical`, `ayurveda_aahar`, `cosmetic`, `new_or_non_classical_drug` — a coarse, deterministic keyword classification of which Ayurvedic-product regulatory category the question is probably about. Always present. Not a legal determination — a heuristic used to frame the answer, defaults to `"classical"` when nothing else matched. `new_or_non_classical_drug` covers questions about new-drug/clinical-trial-permission pathways under the NDCT Rules 2019 — see its dedicated example below. |
| `confidence_score` | float | **New.** The cross-encoder reranker's calibrated confidence (0-1) in the single strongest retrieved chunk — the same number the backend's internal retry logic thresholds against. High (e.g. the `0.997` above) means retrieval found something clearly on-topic. **Not a guarantee `flags.abstained` is `false`** — a well-grounded retrieval can still end in abstention if the model judges the specific retrieved text doesn't actually answer the question asked (see the abstention example below, where `confidence_score` is near zero for a different, expected reason: nothing relevant exists in this corpus at all). Reflects retrieval quality, not answer correctness. |
| `needs_clarification` | boolean | `true` when the question's keywords genuinely span 2+ formulation categories (e.g. mentions both "nutraceutical" and "proprietary formulation"). **Informational, not blocking** — `answer` still answers normally, using the first-matched category. |
| `clarifying_questions` | array of strings | Always `[]` when `needs_clarification` is `false`. When non-empty, currently always exactly one string — a suggested follow-up question. Nothing stops you from sending it as the next `history` turn if the user picks it. |
| `audio_base64` | string \| null | Base64-encoded WAV, **always English speech regardless of `language`** — there is no Indian-language TTS voice available, so this reads the pre-translation English answer, never the translated one. Do not present this as being "in the user's language." **Currently always `null`** — the Groq account backing this hasn't accepted the TTS model's usage terms yet; this is a real, not-yet-resolved gap, not a per-request failure. Safe to send `synthesize_audio: true` regardless — it degrades to `null`, never errors. |

**Error responses** — see "Error handling" below.

---

### The disclaimer — always appended, never generated by the LLM

Every `answer` (from both `/query` and `/query/stream`) ends with, verbatim:
```
\n\n*Disclaimer: This assistant provides source-grounded regulatory information for guidance and does not constitute formal legal advice.*
```
This satisfies SIH26045's problem statement requirement to "clearly state
that it provides information and not legal advice." It's appended in code
(`generation/prompts.py::append_disclaimer`) after the model finishes
generating, the same way citations are attached — never requested from the
LLM and hoped for, so it can't be silently dropped by a truncated
generation. It's present **even on an abstention** (see the abstention
example below) — there's no code path that skips it. If your UI renders
Markdown, the `*...*` is intentional italic emphasis; if not, you'll see the
literal asterisks — strip them client-side if that matters for your design,
but don't strip the sentence itself.

---

### `new_or_non_classical_drug` — real captured example

For a question about a genuinely new drug entity (not an established
classical/proprietary/phytopharmaceutical category), `formulation_category`
classifies as `new_or_non_classical_drug` and citations come from
`NDCT_Rules_2019.pdf` (New Drugs and Clinical Trials Rules, 2019). Real
captured output for `{"question": "What safety dossier is required for
clinical trial permission of a new drug under the NDCT Rules?"}`,
`time_total: 8.7s`:
```json
{
  "answer": "The safety dossier that must be submitted with a clinical‑trial application for a new drug under the NDCT Rules includes:\n\n* **Non‑clinical toxicology data** – GLP‑compliant animal toxicology studies covering the standard safety endpoints required by the schedule...\n\n* **Clinical safety data** – tabulated safety information from the trial, with all adverse events classified by seriousness and assessed for causal relationship to the investigational drug.\n\nThese components together constitute the safety dossier required for permission to conduct the clinical trial.\n\n*Disclaimer: This assistant provides source-grounded regulatory information for guidance and does not constitute formal legal advice.*",
  "citations": [
    {"chunk_id": "NDCT_Rules_2019::p92::c159", "source_file": "NDCT_Rules_2019.pdf", "page_number": 92, "section_heading": "SECOND SCHEDULE"},
    {"chunk_id": "NDCT_Rules_2019::p21::c28", "source_file": "NDCT_Rules_2019.pdf", "page_number": 21, "section_heading": "CHAPTER V"}
  ],
  "flags": {"abstained": false, "retried": false, "weak_grounding": false},
  "formulation_category": "new_or_non_classical_drug",
  "confidence_score": 0.9806081633976108,
  "needs_clarification": false,
  "clarifying_questions": [],
  "audio_base64": null
}
```
(citations array truncated above to 2 of 5 for brevity — the real response has 5)

---

### `POST /query/stream`

Same request body as `/query` (`QueryRequest`, identical fields/validation).
Streams the answer as **Server-Sent Events** (`Content-Type:
text/event-stream`) instead of waiting for the full response — use this for
a live chat UI.

**Not translated.** `language` in the request body is silently ignored by
this endpoint right now — translating a live token stream needs
sentence-boundary detection against a partial buffer, which isn't built.
If you need multilingual, use `/query` (single response, slower to first
byte, but translated). This is a real, current limitation, not an oversight
to work around client-side.

**Event types**, real captured output (`curl -N`, `time_total: 12.1s` for
the full stream):

**`token`** — one per token, as the LLM emits it:
```
event: token
data: {"text": "A"}

event: token
data: {"text": " geographical"}

event: token
data: {"text": " indication"}
```
(`data:` is a single JSON line per the SSE spec — no embedded newlines.
Concatenate `text` fields in arrival order to reconstruct the answer as
it's typed, or just wait for `done` if you don't need a typing effect.)

**`done`** — exactly one, always last on success. Real captured output for
`{"question": "What is a geographical indication?"}`, `time_total: 8.1s`:
```json
{
  "answer": "A geographical indication is a sign that designates goods as originating from a particular geographical environment—including its natural and human factors—and to the production, processing or preparation that takes place in that area. It may be applied to goods by being woven in, impressed on, worked into, annexed to or affixed to the goods or their packaging.\n\n*Disclaimer: This assistant provides source-grounded regulatory information for guidance and does not constitute formal legal advice.*",
  "citations": [
    {"chunk_id": "GI_Rules_2002::p9::c46", "source_file": "GI_Rules_2002.pdf", "page_number": 9, "section_heading": "Section 31. Deficiencies, clause (1)"},
    {"chunk_id": "GI_Rules_2002::p7::c30", "source_file": "GI_Rules_2002.pdf", "page_number": 7, "section_heading": "Section 23. Form and signing of application, clause (9)"},
    {"chunk_id": "GI_Act_1999::p18::c35", "source_file": "GI_Act_1999.pdf", "page_number": 18, "section_heading": "THE GAZETTE OF INDIA EXTRAORDINARY"}
  ],
  "flags": {"abstained": false, "retried": false, "weak_grounding": false},
  "formulation_category": "classical",
  "confidence_score": 0.9994600051404637,
  "needs_clarification": false,
  "clarifying_questions": []
}
```
Same shape as `/query`'s response body, minus `audio_base64` (streaming
never synthesizes audio — there's no `synthesize_audio` support on this
endpoint yet). **The disclaimer streams as one additional real `token`
event** after the answer's own tokens end (not spliced into `done` only) —
a client rendering tokens as they arrive sees it appear the same way the
rest of the answer did, then `done.answer` already includes it, so you
don't need to append it yourself either way.

**`error`** — sent instead of `done` on failure, connection then closes:
```
event: error
data: {"detail": "Internal error processing the query."}
```

**Time-to-first-token is retrieval latency plus the LLM's own
time-to-first-token, not zero.** Retrieval + reranking happen before
streaming starts (there's nothing token-shaped about a rerank score) —
budget for a real, if short, pause before the first `token` event, then
fast incremental delivery after that.

**Response headers** already set server-side to defeat proxy buffering
(`Cache-Control: no-cache`, `X-Accel-Buffering: no`) — you shouldn't need
to configure anything extra for tokens to arrive incrementally through a
typical fetch/EventSource client, but if you're behind your own proxy
layer, make sure it isn't buffering `text/event-stream` responses.

---

### `POST /ingest`

Admin/dev endpoint — re-runs the full ingestion pipeline (loads PDFs from
`data/` and `data/international/`, chunks, re-embeds, rebuilds the BM25
index). **Not something the frontend should ever call in normal
operation** — documented here for completeness.

**Request body:**
```json
{"reset": false}
```
`reset: true` drops and rebuilds the `chunks` table and BM25 index from
scratch; `false` (default) upserts. Real measured time on the current
corpus (4399 chunks): ~2 minutes.

**Auth:** if `ADMIN_TOKEN` is set in the backend's `.env`, this endpoint
requires header `X-Admin-Token: <token>` or returns `401`. If `ADMIN_TOKEN`
is unset (the current local-dev default), the endpoint is open.

**Response `200`:**
```json
{"status": "ok"}
```

---

## The Citation object

```json
{
  "chunk_id": "Patent_Office_Manual_Practice_Procedure_2011::p99::c99",
  "source_file": "Patent_Office_Manual_Practice_Procedure_2011.pdf",
  "page_number": 99,
  "section_heading": "08.03.05.15 An invention which in effect, is traditional knowledge or Section 3(p)"
}
```

| Field | Type | Meaning | Can it be empty? |
|---|---|---|---|
| `chunk_id` | string | Internal id (`{source_file_stem}::p{page}::c{n}`). Not meant for display — useful for debugging/logs, or as a React `key`. | No, always present. |
| `source_file` | string | The PDF's filename, exactly as it sits in `data/`. **This is what the user should see as "the source"** — display it directly, don't reformat it (filenames are deliberately citation-ready). | No — the ingestion pipeline hard-fails if any chunk is missing this. |
| `page_number` | integer | 1-indexed PDF page number. | No. |
| `section_heading` | string | Section/clause heading for that part of the document. For 10 of the 22 indexed documents (the Acts/Rules with real numbered-section structure — Trade Marks Act, Patents Act, Biological Diversity Act, Copyright Act, and others), this is now precise: `"Section 3. What are not inventions, clause (p)"` means the chunk **is** exactly that clause, not just text that mentions it. For the rest, it's a best-effort heuristic (e.g. `"5 Ibid"`, `"KNOWLEDGE AND BIOLOGICAL MATERIAL"`) — still useful, not guaranteed precise. | Never actually empty (falls back to the literal string `"Unlabelled section"`), but quality varies by document as described above. |

`citations` is an array of these, already deduplicated by `chunk_id`, in the
order the reranker ranked them (most relevant first — index 0 is the
strongest source). **`citations` is `[]` on abstention.**

---

## Detecting abstention — use `flags.abstained`, not string-matching

**Use `response.flags.abstained` (boolean). Do not parse `answer` text.**
It's a real boolean, always present in every response from both `/query`
and `/query/stream`'s `done` event (not just abstentions — it's `false` on
a normal answer).

Real captured abstention example, `{"question": "What is the capital of
France?"}`, `time_total: 15.3s`:
```json
{
  "answer": "I could not find this in my sources.  \nWhich specific source or document should I refer to for information about the capital of the French Republic?\n\n*Disclaimer: This assistant provides source-grounded regulatory information for guidance and does not constitute formal legal advice.*",
  "citations": [],
  "flags": {
    "abstained": true,
    "retried": true,
    "weak_grounding": true
  },
  "formulation_category": "classical",
  "confidence_score": 0.00005446697379103936,
  "needs_clarification": false,
  "clarifying_questions": [],
  "audio_base64": null
}
```
Note `weak_grounding: true` here too, on a genuinely out-of-scope question
("What is the capital of France?") — the lexical search found *some*
keyword overlap somewhere in the corpus even though nothing relevant
actually exists, which is exactly the caveat in the field's description
above. Check `abstained` for whether to show a "no answer" state;
`weak_grounding` isn't a substitute for it. `confidence_score` is near zero
here — a genuinely out-of-scope question is exactly the case it's meant to
flag — but note the disclaimer is still appended even though the answer is
an abstention.

Secondary signal, if you want belt-and-suspenders: `citations` is always
`[]` when `flags.abstained` is `true`. Don't rely on this alone — an
extremely obscure question could theoretically retrieve zero chunks
without the model technically "abstaining" in the guardrail sense.
`flags.abstained` is the one guaranteed-correct signal.

All three `flags` keys are **always present** (never missing, never
`null`) — safe to read `response.flags.abstained` directly without an
existence check.

---

## Multilingual — `language` field (`/query` only)

Real captured example, Hindi question and answer, `time_total: 24.1s`:

Request:
```bash
curl -X POST http://127.0.0.1:8123/query -H "Content-Type: application/json; charset=utf-8" \
  -d '{"question": "भौगोलिक संकेत क्या है?", "language": "hi-IN"}'
```

Response `200`:
```json
{
  "answer": "भौगोलिक संकेत एक ऐसा चिन्ह है जो वस्तुओं को देश के एक विशिष्ट क्षेत्र, क्षेत्र या स्थान से उत्पन्न होने के रूप में निर्दिष्ट करता है, जो इंगित करता है कि वस्तुओं में विशेष गुणवत्ता है। प्रतिष्ठा या अन्य विशेषताएँ जो विशुद्ध रूप से या अनिवार्य रूप से भौगोलिक वातावरण-इसके प्राकृतिक और मानव कारकों सहित-के कारण होती हैं और जिनका उत्पादन, प्रसंस्करण या तैयारी उस परिभाषित क्षेत्र में होती है। अस्वीकरणः यह सहायक मार्गदर्शन हेतु स्रोत-आधारित नियामक जानकारी प्रदान करता है और औपचारिक कानूनी सलाह नहीं देता है।*",
  "citations": [
    {"chunk_id": "GI_Rules_2002::p9::c46", "source_file": "GI_Rules_2002.pdf", "page_number": 9, "section_heading": "Section 31. Deficiencies, clause (1)"}
  ],
  "flags": {"abstained": false, "retried": false, "weak_grounding": false},
  "formulation_category": "classical",
  "confidence_score": 0.9994600051404637,
  "needs_clarification": false,
  "clarifying_questions": [],
  "audio_base64": null
}
```
Note the disclaimer sentence at the end of `answer` is translated into the
request's `language` along with the rest of the answer — it goes through
the same translation call, not appended after translation, so you'll never
see it in English inside an otherwise-translated response.

Retrieval and generation always run in English internally regardless of
`language` — the question is translated to English before retrieval, the
answer translated back after generation. `citations`' `section_heading`
values stay in whatever language the source PDF is in (usually English,
sometimes bilingual Hindi/English gazette text) — those are **not**
translated, since they're direct quotes of document structure, not
generated text.

A fixed set of Ayurvedic technical terms (Churna, Bhasma, Taila, Kwatha,
Rasa Shastra, Asava, Arishta) are protected from mistranslation — they'll
appear in Latin script inside an otherwise-Hindi (or other language)
answer, by design, rather than being transliterated or glossed into an
approximate translation that loses the specific regulatory meaning. This
was a real bug (fixed since): an earlier internal placeholder scheme could
leak a garbled string into the answer instead of the term. If you ever see
something like a stray number sequence or obviously-broken text in place of
where a technical term should be, that's this mechanism failing — flag it,
it shouldn't happen anymore, but there's no way to guarantee every possible
input is covered by the current fixed term list.

Translation failures (Sarvam outage, rate limit, timeout) fail silently to
English — you may occasionally get an English `answer` back despite
requesting `language: "hi-IN"`. There's currently no field telling you this
happened; if that matters for your UI, ask backend to add one rather than
guessing from `answer`'s script.

---

## Jurisdiction — India vs. international

Real captured example, `jurisdiction: "international"`, `time_total: 6.7s`:
```json
{
  "answer": "I could not find this in my sources.  \nWhich specific stage or aspect of the PCT filing process (e.g., international application, publication, examination, or national phase entry) would you like detailed information about?\n\n*Disclaimer: This assistant provides source-grounded regulatory information for guidance and does not constitute formal legal advice.*",
  "citations": [],
  "flags": {"abstained": true, "retried": true, "weak_grounding": false},
  "formulation_category": "classical",
  "confidence_score": 0.0,
  "needs_clarification": false,
  "clarifying_questions": [],
  "audio_base64": null
}
```
`confidence_score: 0.0` here specifically means the reranker never even ran
— BM25/dense both returned zero candidates for `jurisdiction:
"international"` (nothing indexed yet), not "the reranker looked and found
nothing relevant" (which would still be a small positive float, as in the
"What is the capital of France?" example above).
This is **expected, current behavior** — `data/international/` has zero
indexed documents right now (see that folder's README for what's planned:
WIPO GRATK Treaty, Nagoya Protocol, Budapest Treaty, PCT). Every
`jurisdiction: "international"` question will abstain until real documents
are added there. Don't build UI that treats this as an error state — it's
the same honest "not found" path as any other unanswerable question,
correctly distinguishing "we don't have this yet" from crashing or
silently answering from Indian law instead.

Invalid `jurisdiction` value — real captured `422`:
```bash
curl -X POST http://127.0.0.1:8123/query -H "Content-Type: application/json" \
  -d '{"question": "test", "jurisdiction": "mars"}'
```
```json
{"detail":[{"type":"literal_error","loc":["body","jurisdiction"],"msg":"Input should be 'india' or 'international'","input":"mars","ctx":{"expected":"'india' or 'international'"}}]}
```

---

## Formulation clarification — `needs_clarification`

Real captured example (`question` deliberately spans two categories),
`{"question": "Is a nutraceutical also a proprietary formulation
product?"}`, `time_total: 8.2s`:
```json
{
  "answer": "I could not find this in my sources.  \nCould you specify which regulation or definition you are referring to when you ask if a nutraceutical is considered a proprietary formulation product?\n\n*Disclaimer: This assistant provides source-grounded regulatory information for guidance and does not constitute formal legal advice.*",
  "citations": [],
  "flags": {"abstained": true, "retried": false, "weak_grounding": false},
  "formulation_category": "ayurveda_aahar",
  "confidence_score": 0.9146197193898428,
  "needs_clarification": true,
  "clarifying_questions": [
    "This question touches more than one formulation category — is it about an Ayurveda-Aahar / nutraceutical food product or a proprietary (P&P) medicine with a brand name and its own formulation? The answer below assumes Ayurveda-Aahar (Nutraceutical) unless you clarify."
  ],
  "audio_base64": null
}
```
Note this particular question also abstained (unrelated to the
clarification signal — the corpus just didn't have a strong match for it
either). `needs_clarification` and `flags.abstained` are independent;
check both.

---

## Timing expectations — read this before building the loading state

**Current measured reality, real numbers, not estimates** (Groq as the
direct primary backend — see "Why so much faster than before" below):

| Scenario | Measured wall time |
|---|---|
| Normal in-scope question, single-turn, English | ~12–15s |
| Question that triggers the internal retry-once path | ~16s |
| `jurisdiction: "international"` (always abstains, still retries once) | ~6s |
| Hindi (`language: "hi-IN"`) question, real answer | ~24s (translation adds real latency — 2 extra Sarvam calls, question in + answer out) |
| `/query/stream` time-to-first-token | a few seconds (retrieval + rerank), then fast incremental delivery |
| Validation error (bad request body) | <10ms |

**Why so much faster than before:** the backend used to try a local Ollama
model first on every single request (by design, for offline-at-a-venue
resilience) and fall back to Groq only on a 15s timeout — meaning most
requests paid that timeout before ever reaching Groq. That's gone: Groq is
now the direct primary path with zero guessing. Local Ollama is still
available for offline demo scenarios, but only when explicitly turned on
via a backend env flag (`OFFLINE_MODE=true`) — an operator decision, not
something that happens automatically per-request anymore. If you see
timing anywhere close to the old table (30-55s) on a request that
*doesn't* involve translation, that's a sign `OFFLINE_MODE` may be
accidentally on, not a frontend problem.

**What this means for your loading state:**
- Build for **~10-25s** typical, not sub-2-second — this is still an LLM
  call plus retrieval, not a cache lookup.
- If you're not using `/query/stream`, budget up to the backend's own
  `REQUEST_TIMEOUT` (90s by default) before giving up client-side — set
  your own fetch timeout comfortably above that (e.g. 95-100s) so the
  backend's own clean `504` fires first, not your fetch call timing out
  with a less informative error.
- **Prefer `/query/stream` for anything chat-shaped** — the perceived
  latency is dramatically better even though total time isn't that
  different, because the user sees the answer forming instead of a blank
  loading state for 12+ seconds.

---

## Error handling

| Status | When | Body shape |
|---|---|---|
| `422` | Request body fails validation (missing `question`, or an invalid `jurisdiction`/`language` value) | FastAPI's standard validation shape — see real examples above and below |
| `503` | Neither Groq (nor Ollama, if `OFFLINE_MODE=true`) could be reached | `{"detail": "<human-readable message>"}` |
| `504` | The whole request exceeded the server's `REQUEST_TIMEOUT` (90s default) — `/query` only, `/query/stream` sends an `error` SSE event instead | `{"detail": "Request exceeded 90.0s with no response from the LLM backend."}` |
| `500` | Anything else unhandled | `{"detail": "Internal error processing the query."}` |

All error bodies have a top-level `detail` key — for `422` it's an array of
field-level problems (Pydantic's default shape), for everything else it's a
plain string. Safe pattern: `if (!res.ok) { const { detail } = await res.json(); ... }`.

Real captured bad-request example (missing `question`):
```bash
curl -X POST http://127.0.0.1:8123/query -H "Content-Type: application/json" -d '{}'
```
Response — `422`, measured `time_total: 0.006s`:
```json
{"detail":[{"type":"missing","loc":["body","question"],"msg":"Field required","input":{}}]}
```
