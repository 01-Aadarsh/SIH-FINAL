"""
FastAPI layer for IP-SAKTI.

A thin HTTP wrapper over the LangGraph pipeline. /query runs the compiled
graph with .ainvoke() (async all the way down: Groq calls and pgvector
queries use native async clients, not blocking calls parked in a thread
pool) under a wall-clock timeout. /query/stream is the same pipeline,
answer tokens delivered as Server-Sent Events as they arrive from the LLM
instead of waiting for the full answer.

/query only (not /query/stream — translating a live token stream is a
separate, harder problem: sentence-boundary detection against a partial
buffer, not covered here) additionally wraps the graph in a translation
bridge (api/translation.py, Sarvam AI): the question translates to English
before retrieval, the answer translates back to QueryRequest.language after
generation — retrieval and the LLM prompt never see anything but English.
Optionally also returns a spoken reading of the *translated* answer via
Sarvam's Bulbul TTS (api/tts.py, QueryRequest.synthesize_audio), in
`language` itself when Bulbul supports it (11 of the languages
api/translation.py can translate into — see
api/tts.py::BULBUL_SUPPORTED_LANGUAGES) or skipped otherwise, never a
mismatched English voice over translated text.

Cancellation: because the graph is now driven by real async I/O (async
psycopg, AsyncGroq/ollama.AsyncClient) instead of a thread-pool future,
asyncio.wait_for's timeout can actually cancel the in-flight call — the
connection is released immediately instead of a zombie thread continuing
to hold a DB connection or wait on an LLM response nobody is listening for
anymore. That was a real, observed problem with the previous
run_in_threadpool design (a thread-pool future can't be killed, only
abandoned) and is fixed by this change, not just faster.

Usage:
    python -m uvicorn api.main:app --reload
    # or, for multiple worker processes (see Procfile):
    python -m uvicorn api.main:app --host 0.0.0.0 --port 8000 --workers 2
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from api.asr import UnsupportedAudioFormat, transcribe_audio
from api.translation import TARGET_LANGUAGE_CODES, translate_text
from api.tts import BULBUL_SUPPORTED_LANGUAGES, synthesize_speech
from compliance.form_navigator import match_forms
from generation.citation import attach_citations, is_abstention
from generation.llm_client import astream_generate
from generation.prompts import append_disclaimer
from graph.build_graph import build_graph
from graph.nodes import expand_related_provisions_node, rewrite_query, run_retrieval_stage
from graph.state import DEFAULT_FLAGS
from ingestion.indexer import run as run_ingestion

load_dotenv(override=True)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

# 90s, not 60s: a multi-turn query (chat history present) triggers its own
# rewrite LLM call before retrieval even starts, and the bounded retry can
# add a second rewrite call on top of that — up to 3 sequential LLM calls
# in the worst case (rewrite, retry-rewrite, generate). 90s covers that
# worst case with margin even against a slow backend.
REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "90"))
# /query/stream times the pre-generation stages (rewrite -> retrieve ->
# rerank -> bounded retry) separately from generation itself: generation is
# streamed token-by-token with its own per-chunk timeout (see
# generation/llm_client.py's STREAM_CHUNK_TIMEOUT), so only the retrieval
# side needs a wall-clock budget here.
RETRIEVAL_TIMEOUT = float(os.getenv("RETRIEVAL_TIMEOUT", "30"))
CORS_ORIGINS = [origin.strip() for origin in os.getenv("CORS_ORIGINS", "*").split(",")]
# If unset, /ingest is unauthenticated — fine for local dev, not for a public
# deployment. Set ADMIN_TOKEN before deploying anywhere reachable from the
# internet.
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN")

# Same base directory ingestion/loader.py::load_directory reads from — a
# citation's source_file is always a bare filename found directly under one
# of these two, never a path outside them (india PDFs live in DATA_DIR
# itself, international ones under DATA_DIR/international/, per that
# module's own docstring).
DATA_DIR = Path(os.getenv("DATA_DIR", "data")).resolve()
SOURCE_SEARCH_DIRS = [DATA_DIR, DATA_DIR / "international"]

app = FastAPI(
    title="IP-SAKTI Sahayak API",
    description=(
        "Source-cited AI assistant for Ayurveda-related IP and regulatory "
        "questions (SIH26045, Ministry of Ayush). Answers only from the "
        "indexed document corpus, with programmatic citations attached from "
        "retrieved chunks — never from the model. Supports both India-only "
        "and international-jurisdiction queries; international currently "
        "abstains gracefully rather than crashing or defaulting to Indian "
        "law, since no international documents are indexed yet (see "
        "data/international/README.md)."
    ),
    version="0.2.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

_graph = None


def _get_graph():
    """Build and cache the compiled graph once, reused across requests."""
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph


class ChatTurn(BaseModel):
    role: str = Field(description='"user" or "assistant".')
    content: str


class QueryRequest(BaseModel):
    question: str = Field(min_length=1, description="The user's question.")
    history: list[ChatTurn] = Field(
        default_factory=list,
        description=(
            "Prior turns, oldest first, NOT including the current question "
            "(that goes in `question`). Omit or pass [] for a single-turn query."
        ),
    )
    jurisdiction: Literal["india", "international"] = Field(
        default="india",
        description=(
            "Which corpus to search. An invalid value is rejected with 422 "
            "before it reaches retrieval — it never silently falls back to "
            "'india'. 'international' currently has no indexed documents "
            "(see data/international/README.md), so it abstains rather than "
            "erroring: this is the same abstention path a normal query takes "
            "when retrieval finds nothing relevant, not a special case."
        ),
    )
    language: Literal[*TARGET_LANGUAGE_CODES] = Field(
        default="en-IN",
        description=(
            "Language of `question`, and the language `answer` is translated "
            "back into before this responds. Retrieval/generation always run "
            "in English regardless of this value — see api/translation.py. "
            "'en-IN' (the default) skips translation entirely: zero added "
            "latency, same behavior as before this field existed. Any other "
            "value that fails to translate (Sarvam outage, bad key, timeout) "
            "silently falls back to an English `answer` rather than erroring "
            "— translation failures never turn into a failed request."
        ),
    )
    synthesize_audio: bool = Field(
        default=False,
        description=(
            "If true, also return `audio_base64` — a spoken-English reading "
            "of the answer, regardless of `language` (Groq's TTS has no "
            "Indian-language voice; see api/tts.py). Off by default: it adds "
            "real latency (one or more extra Groq calls after generation) "
            "that most callers won't want paid on every request."
        ),
    )


class Citation(BaseModel):
    chunk_id: str = Field(description="Internal id, not meant for display.")
    source_file: str = Field(description="The source PDF's filename — display this as the citation.")
    page_number: int = Field(description="1-indexed page number within source_file.")
    section_heading: str = Field(
        description=(
            "Best-effort detected heading. Heuristic, not guaranteed accurate — "
            "falls back to the literal string 'Unlabelled section' if nothing "
            "heading-shaped was found nearby."
        )
    )
    text: str = Field(
        description=(
            "The actual chunk text retrieved and given to the LLM — verbatim "
            "from the source PDF, not model output. Lets the frontend show "
            "exactly what was cited without re-fetching or rendering the "
            "whole PDF page."
        )
    )


class Flags(BaseModel):
    abstained: bool = Field(
        default=False,
        description=(
            "The authoritative way to detect an abstention. True means the "
            "retrieved context did not contain the answer, and `answer` is a "
            "refusal + clarifying question rather than a real answer. Do not "
            "detect this by string-matching `answer` instead."
        ),
    )
    retried: bool = Field(
        default=False,
        description="True if the bounded retry-once path fired (weak initial rerank score). Informational only.",
    )
    weak_grounding: bool = Field(
        default=False,
        description=(
            "True when BM25 found a strong lexical match but the cross-encoder's "
            "top confidence was still low — a structured signal that retrieval/"
            "reranking likely underperformed on this query specifically, distinct "
            "from the corpus genuinely lacking an answer. See retrieval/reranker.py. "
            "Informational only — does not affect whether the request abstains."
        ),
    )


class FormCard(BaseModel):
    """One official government form/registry match — see
    compliance/form_navigator.py. Every field here is what that module's
    catalog actually stores, not additional computed content."""

    form_id: str
    agency: str = Field(description='"NBA" (National Biodiversity Authority) or "IPO" (Indian Patent Office).')
    jurisdiction: str = Field(description='Always "india" — see compliance/form_navigator.py.')
    title: str
    statutory_mandate: str
    submission_portal: str
    required_attachments: list[str]
    deadline: str


class QueryResponse(BaseModel):
    answer: str = Field(
        description=(
            "In the request's `language` if translation succeeded, English "
            "if `language` was already English, or English as a silent "
            "fallback if translation failed — see QueryRequest.language."
        )
    )
    citations: list[Citation] = Field(
        description="Always [] when flags.abstained is true — nothing was actually used to answer."
    )
    flags: Flags
    formulation_category: str = Field(
        description=(
            "Deterministic keyword-based triage of the question into one of "
            "graph.formulation.FORMULATION_CATEGORIES (classical, proprietary, "
            "phytopharmaceutical, ayurveda_aahar, new_or_non_classical_drug, "
            "cosmetic) — see graph/formulation.py. A coarse heuristic used to "
            "frame the generation prompt, not a legal determination; defaults "
            "to 'classical' when no category-specific keyword matched."
        )
    )
    confidence_score: float = Field(
        description=(
            "The reranker's calibrated confidence (sigmoid of the cross-"
            "encoder's raw logit, see retrieval/reranker.py) in the single "
            "strongest retrieved chunk — the same number should_retry "
            "thresholds against internally, not a separately-invented metric. "
            "Range [0, 1]; 0.0 when nothing was retrieved. Reflects retrieval "
            "quality, not whether the final answer happens to be an "
            "abstention — a well-grounded retrieval can still end in "
            "abstention if the model judges the retrieved text doesn't "
            "actually answer the specific question asked, so don't treat a "
            "high score here as a guarantee flags.abstained is false."
        )
    )
    audio_base64: str | None = Field(
        default=None,
        description=(
            "Base64-encoded WAV of the answer read aloud in `language` "
            "itself (Sarvam's Bulbul TTS — see api/tts.py). Null if "
            "synthesize_audio was false, SARVAM_API_KEY isn't configured, "
            "`language` isn't one of the 11 Bulbul supports (see "
            "api/tts.py::BULBUL_SUPPORTED_LANGUAGES), or synthesis failed."
        ),
    )
    related_provisions: list[dict] = Field(
        default_factory=list,
        description=(
            "Knowledge-graph cross-references (graph_kg/kg.py) — each entry "
            "is `{tag, relation, source_file, page_number, section_heading, "
            "jurisdiction}` pointing at a real, separately-indexed chunk, "
            "never new legal text. `relation` is `cross_jurisdiction_counterpart` "
            "(e.g. a domestic Section 3(p) question surfacing the WIPO GRATK "
            "Treaty's disclosure obligation — deliberately visible across the "
            "jurisdiction switch, not conflated with `answer`/`citations`, "
            "which stay scoped to `jurisdiction`) or `co_occurs_with` (tags "
            "that repeatedly appear together in the real corpus). Always `[]` "
            "on an abstention, and always `[]` (never an error) if the graph "
            "hasn't been built yet — see graph_kg/build_kg.py."
        ),
    )
    needs_clarification: bool = Field(
        default=False,
        description=(
            "True when formulation triage matched 2+ categories in the "
            "question (see graph/formulation.py) — genuinely ambiguous, not "
            "just coarse keyword noise. Informational, not blocking: `answer` "
            "still answers, using the first-matched category, and "
            "`clarifying_questions` suggests what a follow-up turn (via "
            "`history`) could narrow down."
        ),
    )
    clarifying_questions: list[str] = Field(
        default_factory=list,
        description="Always [] when needs_clarification is false.",
    )
    actionable_forms: list[FormCard] = Field(
        default_factory=list,
        description=(
            "Official government forms this question likely needs next "
            "(compliance/form_navigator.py) — e.g. a patent question about "
            "an Ayurvedic formulation surfacing NBA Form 7 (prior approval "
            "before applying for IPR based on Indian biological resources) "
            "and IPO Form 1/2 (patent application/specification). Matched "
            "deterministically (keywords + formulation_category + "
            "statutory_tags), same no-LLM-call philosophy as "
            "formulation_category itself. Always `[]` for "
            "`jurisdiction: \"international\"` — every catalog entry is a "
            "domestic Indian registry; this abstains rather than guessing "
            "at an international equivalent, same rule international "
            "retrieval already follows. First-pass reference data, not "
            "verified against live government sources on every field — see "
            "compliance/form_navigator.py's module docstring."
        ),
    )


class IngestRequest(BaseModel):
    reset: bool = Field(
        default=False,
        description="True drops and rebuilds the chunks table + BM25 index from scratch. False upserts.",
    )


class HealthResponse(BaseModel):
    status: str


class IngestResponse(BaseModel):
    status: str


class TranscribeResponse(BaseModel):
    transcript: str
    detected_language: str = Field(
        description=(
            "The BCP-47 code Sarvam's Saaras model detected/used (see "
            "api/asr.py), or the literal 'unknown' if detection failed — "
            "never fabricated when detection genuinely couldn't resolve it."
        )
    )


class VoiceQueryResponse(QueryResponse):
    """Everything QueryResponse has, plus what ASR itself produced — a
    caller gets both what was heard and what was answered in one response,
    rather than needing to correlate two separate calls."""

    transcribed_text: str
    detected_language: str


@app.get(
    "/health",
    response_model=HealthResponse,
    summary="Health check",
    description="For hosting-platform health checks. No dependency checks (DB, LLM) — just confirms the process is up.",
)
def health():
    return HealthResponse(status="ok")


@app.get(
    "/sources/{filename}",
    summary="Serve a source PDF a citation points at",
    description=(
        "Backs the frontend's 'View source PDF' link on every citation — "
        "`source_file` in a Citation object is always a bare filename that "
        "actually exists under one of these two directories, never an "
        "arbitrary path. `filename` is resolved by basename only (rejects "
        "any '/' or '..' component) and only ever served from `DATA_DIR` or "
        "`DATA_DIR/international/` — the same two locations "
        "ingestion/loader.py indexes from, nothing else on disk is reachable "
        "through this endpoint."
    ),
    responses={
        400: {"description": "filename contains a path separator or '..' — rejected before any file lookup."},
        404: {"description": "No file by that exact name in data/ or data/international/."},
    },
)
def get_source(filename: str):
    safe_name = Path(filename).name
    if safe_name != filename or safe_name in ("", ".", ".."):
        raise HTTPException(status_code=400, detail="Invalid filename.")

    for directory in SOURCE_SEARCH_DIRS:
        candidate = directory / safe_name
        if candidate.is_file():
            # content_disposition_type="inline" (default is "attachment") —
            # otherwise the browser downloads the PDF instead of rendering
            # it in SourceViewer's iframe.
            return FileResponse(
                candidate,
                media_type="application/pdf",
                filename=safe_name,
                content_disposition_type="inline",
            )

    raise HTTPException(status_code=404, detail=f"No source file named {safe_name!r} found.")


async def _invoke_graph(
    app_graph, english_question: str, history: list[dict], jurisdiction: str
) -> dict:
    """The try/except around the graph's own bounded execution budget,
    factored out of run_query so its three failure modes (timeout / no LLM
    reachable / anything else) don't count against that function's own
    cognitive complexity budget."""
    try:
        return await asyncio.wait_for(
            app_graph.ainvoke(
                {
                    "query": english_question,
                    "history": history,
                    "jurisdiction": jurisdiction,
                    "flags": dict(DEFAULT_FLAGS),
                }
            ),
            timeout=REQUEST_TIMEOUT,
        )
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=504,
            detail=f"Request exceeded {REQUEST_TIMEOUT}s with no response from the LLM backend.",
        )
    except RuntimeError as exc:
        # Raised by generation.llm_client when no LLM backend is reachable.
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception:
        log.exception("Unhandled error in /query")
        raise HTTPException(status_code=500, detail="Internal error processing the query.")


async def _translate_and_maybe_speak(
    english_answer: str, language: str, synthesize_audio: bool
) -> tuple[str, str | None]:
    """Translate the answer back to `language` and, if requested, synthesize
    audio for it — factored out of run_query purely to keep that function's
    cognitive complexity down; the concurrency behavior described below is
    unchanged from when this was inline.

    TTS speaks the *translated* answer, in that same language (Sarvam's
    Bulbul TTS supports 11 of the languages translate_text() can target —
    see api/tts.py::BULBUL_SUPPORTED_LANGUAGES; unsupported languages skip
    audio there rather than guessing, so that check isn't duplicated here).
    For en-IN, translation is a no-op, so audio can still start immediately
    from english_answer without waiting on it — the same concurrency this
    had before. Every other language must wait for the real translated text
    first, since that's what gets voiced; synthesizing from English while
    displaying Hindi would be worse than the small added latency.
    """
    translate_out = translate_text(english_answer, "en-IN", language)

    if not synthesize_audio:
        return await translate_out, None

    if language == "en-IN":
        audio_task = asyncio.ensure_future(synthesize_speech(english_answer, "en-IN"))
        translated_answer = await translate_out
    else:
        translated_answer = await translate_out
        audio_task = asyncio.ensure_future(synthesize_speech(translated_answer, language))

    return translated_answer, await audio_task


async def run_query(
    question: str,
    history: list[dict],
    jurisdiction: str,
    language: str,
    synthesize_audio: bool,
) -> QueryResponse:
    """
    The actual /query pipeline, factored out so /query itself and
    /api/v1/voice/query (api/asr.py's transcript piped straight in) share
    one implementation instead of two copies drifting apart. Takes plain
    values, not QueryRequest, so a caller that got its `question` from
    ASR rather than JSON doesn't need to construct a fake request object.
    """
    app_graph = _get_graph()

    # STEP A: translate the question into English. A no-op call (returns
    # immediately, no HTTP request) when language is already English —
    # translate_text's own fallback rule, not special-cased here.
    english_question = await translate_text(question, language, "en-IN")

    result = await _invoke_graph(app_graph, english_question, history, jurisdiction)
    english_answer = result["answer"]

    translated_answer, audio_base64 = await _translate_and_maybe_speak(
        english_answer, language, synthesize_audio
    )

    # Deterministic, no LLM call — same keyword+category+tag matcher the
    # standalone /api/v1/compliance/forms endpoint uses. Every catalog
    # entry is a domestic Indian registry, so international queries get []
    # here (match_forms's own jurisdiction check), not a guess. No forms on
    # an abstention, same rule as citations/related_provisions: a real bug
    # caught by scripts/evaluate_pipeline.py's out-of-scope trap
    # queries — formulation_category defaults to "classical" (see
    # graph/formulation.py::triage_formulation, "zero matches still
    # defaults to classical") even for something like "what is the capital
    # of France?", which was silently attaching an NBA form to completely
    # unrelated abstained answers before this guard existed.
    flags = result.get("flags") or {}
    # statutory_tags here is the tags actually carried by the chunks that
    # grounded this specific answer (same source expand_related_provisions_node
    # uses for the knowledge graph) — not result["statutory_tags"], which is
    # graph/formulation.py's coarse per-CATEGORY tag list. Using the
    # category list was a second false-positive source alongside the
    # abstention bug above: "classical" already includes
    # BDA_Sec7_SBB_Exemption as one of its five typical tags, so every
    # classical-defaulted answer looked like a tag hit for NBA_FORM_8
    # regardless of what the retrieved chunks actually said.
    chunk_tags = sorted({
        tag for chunk in (result.get("reranked") or []) for tag in (chunk.get("statutory_tags") or [])
    })
    actionable_forms = (
        []
        if flags.get("abstained")
        else match_forms(
            intent=english_question,
            jurisdiction=jurisdiction,
            formulation_category=result.get("formulation_category"),
            statutory_tags=chunk_tags,
        )
    )

    return QueryResponse(
        answer=translated_answer,
        citations=result.get("citations") or [],
        flags=result.get("flags") or {},
        formulation_category=result.get("formulation_category") or "classical",
        confidence_score=result.get("confidence_score") or 0.0,
        needs_clarification=result.get("needs_clarification") or False,
        clarifying_questions=result.get("clarifying_questions") or [],
        audio_base64=audio_base64,
        related_provisions=result.get("related_provisions") or [],
        actionable_forms=actionable_forms,
    )


@app.post(
    "/query",
    response_model=QueryResponse,
    summary="Answer a question from the indexed documents",
    description=(
        "Runs the full pipeline: translate question to English (if needed) "
        "-> query rewrite -> hybrid retrieval -> cross-encoder reranking -> "
        "grounded generation -> citation attachment -> translate answer back "
        "to `language` (if needed) -> optional TTS -> actionable-forms "
        "match. Answers only from retrieved context; abstains "
        "(flags.abstained=true) if the context doesn't contain the answer "
        "— including when jurisdiction='international' finds no indexed "
        "documents. Single JSON response; see /query/stream for token "
        "streaming, or /api/v1/voice/query for audio-in."
    ),
    responses={
        422: {"description": "Invalid request body (e.g. jurisdiction not 'india' or 'international')."},
        503: {"description": "Groq (or Ollama, under OFFLINE_MODE) could not be reached."},
        504: {"description": "Request exceeded REQUEST_TIMEOUT with no LLM response."},
        500: {"description": "Unhandled internal error."},
    },
)
async def query(req: QueryRequest):
    history = [turn.model_dump() for turn in req.history]
    return await run_query(
        question=req.question,
        history=history,
        jurisdiction=req.jurisdiction,
        language=req.language,
        synthesize_audio=req.synthesize_audio,
    )


def _sse(event: str, data: dict) -> str:
    """Format one Server-Sent Events frame. `event:` names the event type
    (token / done / error) so a client can dispatch without inspecting
    payload shape; `data:` is one JSON line, per the SSE spec (no embedded
    newlines allowed in a single data field)."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@app.post(
    "/query/stream",
    summary="Answer a question, streaming the answer as Server-Sent Events",
    description=(
        "Same pipeline as /query, but streams the answer token-by-token as "
        "it comes off the LLM instead of waiting for the full response. "
        "Response is `text/event-stream`: a series of `token` events "
        "(`{\"text\": \"...\"}`) as the answer is generated, followed by "
        "exactly one `done` event carrying the full assembled `answer`, "
        "`citations`, and `flags` — identical shape to /query's response "
        "body — or one `error` event (`{\"detail\": \"...\"}`) on failure. "
        "Retrieval + rerank happen before the first token (not streamed — "
        "there's nothing token-shaped about a rerank score), so time-to-"
        "first-token is retrieval latency plus the LLM's own time-to-first-"
        "token, not zero, but it does not wait for the full answer."
    ),
)
async def query_stream(req: QueryRequest):
    history = [turn.model_dump() for turn in req.history]

    async def event_generator():
        try:
            rewrite_state = await rewrite_query({"query": req.question, "history": history})
            rewritten = rewrite_state["rewritten_query"]

            retrieval_state = await asyncio.wait_for(
                run_retrieval_stage(rewritten, req.jurisdiction),
                timeout=RETRIEVAL_TIMEOUT,
            )
            reranked = retrieval_state["reranked"]
            flags = retrieval_state["flags"]
            formulation_category = retrieval_state["formulation_category"]
            statutory_tags = retrieval_state["statutory_tags"]

            parts: list[str] = []
            async for token in astream_generate(rewritten, reranked, formulation_category, statutory_tags):
                parts.append(token)
                yield _sse("token", {"text": token})

            raw_answer = "".join(parts)
            # is_abstention() runs on the model's raw output, same reasoning
            # as generate_answer() in graph/nodes.py — the disclaimer doesn't
            # start with ABSTENTION_MARKER regardless, but checking before
            # appending is the obviously-correct order.
            abstained = is_abstention(raw_answer)
            citations = [] if abstained else attach_citations(reranked)
            flags["abstained"] = abstained
            # No I/O, no LLM call — cheap enough to run on every stream too,
            # unlike translation/TTS (excluded from /query/stream for a real
            # technical reason: sentence-boundary detection against a
            # partial token buffer). No such reason applies here.
            related_provisions = expand_related_provisions_node(
                {"reranked": reranked, "flags": flags}
            )["related_provisions"]
            # No forms on an abstention, and per-CHUNK tags rather than
            # statutory_tags (the category-level list) -- same two fixes
            # as run_query() above, see its comments for the concrete
            # false-positive each one caught.
            chunk_tags = sorted({
                tag for chunk in reranked for tag in (chunk.get("statutory_tags") or [])
            })
            actionable_forms = (
                []
                if abstained
                else match_forms(
                    intent=rewritten,
                    jurisdiction=req.jurisdiction,
                    formulation_category=formulation_category,
                    statutory_tags=chunk_tags,
                )
            )

            # The disclaimer streams as one more real token event — not
            # silently spliced into the `done` payload only — so a client
            # rendering tokens as they arrive sees it appear the same way
            # the rest of the answer did, instead of a jump at the end.
            disclaimer_suffix = append_disclaimer(raw_answer)[len(raw_answer):]
            yield _sse("token", {"text": disclaimer_suffix})
            answer = raw_answer + disclaimer_suffix

            yield _sse(
                "done",
                {
                    "answer": answer,
                    "citations": citations,
                    "flags": flags,
                    "formulation_category": formulation_category,
                    "confidence_score": retrieval_state.get("confidence_score") or 0.0,
                    "needs_clarification": retrieval_state["needs_clarification"],
                    "clarifying_questions": retrieval_state["clarifying_questions"],
                    "related_provisions": related_provisions,
                    "actionable_forms": actionable_forms,
                },
            )
        except asyncio.TimeoutError:
            yield _sse("error", {"detail": f"Retrieval exceeded {RETRIEVAL_TIMEOUT}s."})
        except RuntimeError as exc:
            yield _sse("error", {"detail": str(exc)})
        except Exception:
            log.exception("Unhandled error in /query/stream")
            yield _sse("error", {"detail": "Internal error processing the query."})

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            # Stop intermediary buffering (nginx in particular) that would
            # otherwise hold the whole response until it's complete,
            # defeating the point of streaming.
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.post(
    "/ingest",
    response_model=IngestResponse,
    summary="Re-run ingestion (admin/dev)",
    description=(
        "Re-indexes every PDF in DATA_DIR (both data/ and data/international/): "
        "chunks, embeds into pgvector, rebuilds the BM25 index. Not a "
        "frontend-facing endpoint. Takes 1-2 minutes on the current corpus "
        "size. Requires header X-Admin-Token if ADMIN_TOKEN is set in the "
        "backend's .env; open if unset (local-dev default). Stays on "
        "run_in_threadpool deliberately — this is an offline batch job "
        "(embeds the whole corpus, not a single query), unlike /query and "
        "/query/stream which are genuinely async end to end."
    ),
    responses={
        401: {"description": "Missing or invalid X-Admin-Token (only when ADMIN_TOKEN is set)."},
        500: {"description": "Ingestion failed (e.g. bad PDF, DB unreachable)."},
    },
)
async def ingest(req: IngestRequest, x_admin_token: str | None = Header(default=None)):
    if ADMIN_TOKEN and x_admin_token != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid or missing admin token.")

    try:
        await run_in_threadpool(run_ingestion, req.reset)
    except Exception as exc:
        log.exception("Ingestion failed")
        raise HTTPException(status_code=500, detail=f"Ingestion failed: {exc}")

    return IngestResponse(status="ok")


def _raise_for_asr_error(exc: Exception) -> None:
    """Shared error mapping for both voice endpoints below — a caller-input
    problem (bad format, bad language_code) is a 4xx; Sarvam itself being
    unreachable or erroring is a 502 (this server's upstream failed, not
    the request itself)."""
    if isinstance(exc, UnsupportedAudioFormat):
        raise HTTPException(status_code=415, detail=str(exc))
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=422, detail=str(exc))
    if isinstance(exc, RuntimeError):
        raise HTTPException(status_code=502, detail=str(exc))
    raise exc


@app.post(
    "/api/v1/voice/transcribe",
    response_model=TranscribeResponse,
    summary="Transcribe spoken audio to text",
    description=(
        "Speech-to-text via Sarvam AI's Saaras model (api/asr.py). Accepts "
        "`.wav`, `.mp3`, `.m4a`, or `.webm`. `language_code` defaults to "
        "'unknown' (auto-detect); explicit codes include 'hi-IN', 'ta-IN', "
        "'en-IN' and Sarvam's other supported Indian languages. Does NOT "
        "fail open — a Sarvam failure is a real error response, not a "
        "silently empty transcript, since there's no reasonable fallback "
        "text to substitute (unlike api/translation.py and api/tts.py)."
    ),
    responses={
        415: {"description": "Audio file extension not one of .wav/.mp3/.m4a/.webm."},
        422: {"description": "Invalid language_code."},
        502: {"description": "Sarvam's speech-to-text API failed or was unreachable."},
    },
)
async def voice_transcribe(
    file: UploadFile = File(..., description="Audio file: .wav, .mp3, .m4a, or .webm."),
    language_code: str = Form("unknown"),
):
    audio_bytes = await file.read()
    try:
        transcript, detected_language = await transcribe_audio(
            audio_bytes, file.filename or "", language_code
        )
    # UnsupportedAudioFormat IS a ValueError (see api/asr.py) — catching it
    # separately alongside ValueError was redundant.
    except (ValueError, RuntimeError) as exc:
        _raise_for_asr_error(exc)

    return TranscribeResponse(transcript=transcript, detected_language=detected_language)


@app.post(
    "/api/v1/voice/query",
    response_model=VoiceQueryResponse,
    summary="Transcribe spoken audio, then answer it through the full /query pipeline",
    description=(
        "Composite of /api/v1/voice/transcribe -> /query: runs ASR on the "
        "uploaded audio, then pipes the transcript straight into the same "
        "run_query() pipeline /query itself uses (translate -> retrieve -> "
        "rerank -> generate -> cite -> translate back -> TTS -> actionable-"
        "forms match). The response is /query's full response shape plus "
        "`transcribed_text` and `detected_language`. If Saaras's detected "
        "language isn't one of the languages api/translation.py/api/tts.py "
        "support, the answer is generated in English (`en-IN`) rather than "
        "guessing at an unsupported target language."
    ),
    responses={
        415: {"description": "Audio file extension not one of .wav/.mp3/.m4a/.webm."},
        422: {"description": "Invalid language_code or jurisdiction."},
        502: {"description": "Sarvam's speech-to-text API failed or was unreachable."},
        503: {"description": "Groq (or Ollama, under OFFLINE_MODE) could not be reached."},
        504: {"description": "Request exceeded REQUEST_TIMEOUT with no LLM response."},
        500: {"description": "Unhandled internal error."},
    },
)
async def voice_query(
    file: UploadFile = File(..., description="Audio file: .wav, .mp3, .m4a, or .webm."),
    language_code: str = Form("unknown"),
    jurisdiction: Literal["india", "international"] = Form("india"),
    synthesize_audio: bool = Form(False),
):
    audio_bytes = await file.read()
    try:
        transcript, detected_language = await transcribe_audio(
            audio_bytes, file.filename or "", language_code
        )
    # UnsupportedAudioFormat IS a ValueError (see api/asr.py) — catching it
    # separately alongside ValueError was redundant.
    except (ValueError, RuntimeError) as exc:
        _raise_for_asr_error(exc)

    # detected_language may be "unknown" (detection failed) or a code
    # Saaras supports but translate_text()/synthesize_speech() don't (the
    # two Sarvam APIs have independently documented language coverage —
    # see api/asr.py's module docstring) — fall back to en-IN rather than
    # pass an unsupported value into the graph as `language`.
    response_language = detected_language if detected_language in TARGET_LANGUAGE_CODES else "en-IN"

    query_response = await run_query(
        question=transcript,
        history=[],
        jurisdiction=jurisdiction,
        language=response_language,
        synthesize_audio=synthesize_audio,
    )

    return VoiceQueryResponse(
        transcribed_text=transcript,
        detected_language=detected_language,
        **query_response.model_dump(),
    )


@app.get(
    "/api/v1/compliance/forms",
    summary="Look up official government forms matching an intent",
    description=(
        "Deterministic keyword match (compliance/form_navigator.py, no LLM "
        "call) against the NBA/IPO form catalog — e.g. `intent=patent my "
        "Ayurvedic formulation` surfaces NBA Form 7 (prior approval before "
        "applying for IPR based on Indian biological resources) and IPO "
        "Form 1/2. Every catalog entry is a domestic Indian registry, so "
        "`jurisdiction=international` always returns an empty list rather "
        "than guessing at an international equivalent. First-pass "
        "reference data — see that module's docstring for exactly which "
        "form numbers were corrected against the real, verified statute "
        "text already indexed in this project, and why."
    ),
)
def compliance_forms(
    intent: str,
    jurisdiction: Literal["india", "international"] = "india",
):
    return {"forms": match_forms(intent=intent, jurisdiction=jurisdiction)}
