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
Optionally also returns a spoken-English reading of the answer via Groq TTS
(api/tts.py, QueryRequest.synthesize_audio) — English-only regardless of
`language`, since Groq has no Indian-language voice to speak in.

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
from typing import Literal

from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from api.translation import TARGET_LANGUAGE_CODES, translate_text
from api.tts import synthesize_speech
from generation.citation import attach_citations, is_abstention
from generation.llm_client import astream_generate
from graph.build_graph import build_graph
from graph.nodes import rewrite_query, run_retrieval_stage
from graph.state import DEFAULT_FLAGS
from ingestion.indexer import run as run_ingestion

load_dotenv()

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
    audio_base64: str | None = Field(
        default=None,
        description=(
            "Base64-encoded WAV, always a reading of the ENGLISH answer "
            "regardless of `language`/`answer` (Groq has no Indian-language "
            "voice — see api/tts.py) — do not present this as being in the "
            "user's selected language. Null if synthesize_audio was false, "
            "GROQ_TTS_VOICE isn't configured, or synthesis failed."
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


@app.get(
    "/health",
    response_model=HealthResponse,
    summary="Health check",
    description="For hosting-platform health checks. No dependency checks (DB, LLM) — just confirms the process is up.",
)
def health():
    return HealthResponse(status="ok")


@app.post(
    "/query",
    response_model=QueryResponse,
    summary="Answer a question from the indexed documents",
    description=(
        "Runs the full pipeline: translate question to English (if needed) "
        "-> query rewrite -> hybrid retrieval -> cross-encoder reranking -> "
        "grounded generation -> citation attachment -> translate answer back "
        "to `language` (if needed) -> optional English TTS. Answers only "
        "from retrieved context; abstains (flags.abstained=true) if the "
        "context doesn't contain the answer — including when "
        "jurisdiction='international' finds no indexed documents. Single "
        "JSON response; see /query/stream for token streaming (English "
        "only — translation isn't wired into the streaming path)."
    ),
    responses={
        422: {"description": "Invalid request body (e.g. jurisdiction not 'india' or 'international')."},
        503: {"description": "Groq (or Ollama, under OFFLINE_MODE) could not be reached."},
        504: {"description": "Request exceeded REQUEST_TIMEOUT with no LLM response."},
        500: {"description": "Unhandled internal error."},
    },
)
async def query(req: QueryRequest):
    app_graph = _get_graph()
    history = [turn.model_dump() for turn in req.history]

    # STEP A: translate the question into English. A no-op call (returns
    # immediately, no HTTP request) when language is already English —
    # translate_text's own fallback rule, not special-cased here.
    english_question = await translate_text(req.question, req.language, "en-IN")

    try:
        result = await asyncio.wait_for(
            app_graph.ainvoke(
                {
                    "query": english_question,
                    "history": history,
                    "jurisdiction": req.jurisdiction,
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

    english_answer = result["answer"]

    # TTS reads the pre-translation English answer — Groq has no
    # Indian-language voice, so synthesizing from a translated answer would
    # either fail outright or mispronounce every non-English word. Runs
    # concurrently with STEP C's translation call: the two are independent
    # (different providers, different inputs) and neither depends on the
    # other's result, so there's no reason to pay their latencies serially.
    translate_out = translate_text(english_answer, "en-IN", req.language)
    audio_task = (
        asyncio.ensure_future(synthesize_speech(english_answer, "en-IN"))
        if req.synthesize_audio
        else None
    )

    translated_answer = await translate_out
    audio_base64 = await audio_task if audio_task is not None else None

    return QueryResponse(
        answer=translated_answer,
        citations=result.get("citations") or [],
        flags=result.get("flags") or {},
        audio_base64=audio_base64,
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

            reranked, retried = await asyncio.wait_for(
                run_retrieval_stage(rewritten, req.jurisdiction),
                timeout=RETRIEVAL_TIMEOUT,
            )

            parts: list[str] = []
            async for token in astream_generate(rewritten, reranked):
                parts.append(token)
                yield _sse("token", {"text": token})

            answer = "".join(parts)
            abstained = is_abstention(answer)
            citations = [] if abstained else attach_citations(reranked)
            yield _sse(
                "done",
                {
                    "answer": answer,
                    "citations": citations,
                    "flags": {"abstained": abstained, "retried": retried},
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
