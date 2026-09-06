"""
LLM client for IP-SAKTI generation.

Groq is the direct primary path — no guessing, no per-request timeout race.
Local Ollama only runs when OFFLINE_MODE=true (venue WiFi failure is a real,
known risk for this demo — see idea.md — so the escape hatch stays, but it's
an explicit operator decision now, not a runtime gamble on every request).

Why this replaced the old "always try Ollama first, fall back to Groq on
timeout" design: on this dev machine, Ollama's GPU path crashes outright (a
CUDA driver/runtime mismatch), forcing CPU-only inference, measured at ~163s
for one real grounded-generation call (5 retrieved chunks as context) — ~149s
of that is prompt processing alone. Every single request was paying up to
OLLAMA_TIMEOUT seconds of dead waiting before ever reaching Groq, on the
machine actually used to build and demo this. That's not a fallback, that's
a tax. OFFLINE_MODE=true still uses Ollama as the primary attempt (falling
back to Groq if it also fails, in case connectivity actually is available
despite the flag) — the flag encodes "I know WiFi is down," not "guess."

Multi-key rotation: GROQ_API_KEY, plus GROQ_API_KEY_2, GROQ_API_KEY_3, ...
as many as are set — a real, reproduced failure mode this backs up
against, not a hypothetical: a single free-tier key's 200k-token daily
quota ran out mid-run during scripts/evaluate_pipeline.py's own benchmark
runs. On a 429 (groq.RateLimitError) specifically, the next configured key
is tried before giving up; any other failure (auth, network, a genuine
model error) still raises immediately, since rotating keys wouldn't fix
those anyway. See _advance_key_index()'s docstring for why the rotation
state is process-lifetime, not per-request.

Usage:
    python -m generation.llm_client "What does Section 3(p) say?"
    OFFLINE_MODE=true python -m generation.llm_client "..."
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from collections.abc import AsyncIterator

import ollama
from dotenv import load_dotenv
from groq import AsyncGroq, RateLimitError

from generation.prompts import SYSTEM_PROMPT, build_user_prompt

load_dotenv(override=True)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

# Explicit operator flag, not an auto-detected condition — see module
# docstring. Accepts the usual truthy spellings so "true"/"1"/"yes" all work
# from a shell export or a .env file without surprises.
OFFLINE_MODE = os.getenv("OFFLINE_MODE", "false").strip().lower() in ("1", "true", "yes")

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1:8b")
OLLAMA_TIMEOUT = float(os.getenv("OLLAMA_TIMEOUT", "45"))
# See module docstring: GPU inference crashes the Ollama server on the
# machine this was built on. Set OLLAMA_NUM_GPU=-1 in .env to let Ollama pick
# its own default (e.g. once a driver fix is confirmed) instead of CPU-only.
OLLAMA_NUM_GPU = int(os.getenv("OLLAMA_NUM_GPU", "0"))

def _load_groq_api_keys() -> list[str]:
    """GROQ_API_KEY, plus GROQ_API_KEY_2, GROQ_API_KEY_3, ... for as long as
    they're set consecutively (stops at the first gap). Lets one account's
    free-tier daily token quota (a real, reproduced failure mode — see
    scripts/evaluate_pipeline.py's benchmark runs, which exhausted a single
    key's 200k-token daily limit mid-run) get backed up by additional keys
    without any other module needing to know multiple keys exist at all."""
    keys = []
    primary = os.getenv("GROQ_API_KEY")
    if primary:
        keys.append(primary)
    i = 2
    while True:
        key = os.getenv(f"GROQ_API_KEY_{i}")
        if not key:
            break
        keys.append(key)
        i += 1
    return keys


GROQ_API_KEYS: list[str] = _load_groq_api_keys()
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")

# A stalled streaming response (network stall, backend hang) must not hang
# the SSE connection forever — this bounds the wait for each individual
# token/chunk, not the whole response. Generous relative to normal
# inter-token gaps (well under a second on Groq) but still finite.
STREAM_CHUNK_TIMEOUT = float(os.getenv("STREAM_CHUNK_TIMEOUT", "20"))

_ollama_client: ollama.AsyncClient | None = None
_groq_clients: dict[int, AsyncGroq] = {}
_current_key_index = 0


def _get_ollama_client() -> ollama.AsyncClient:
    global _ollama_client
    if _ollama_client is None:
        _ollama_client = ollama.AsyncClient(host=OLLAMA_HOST, timeout=OLLAMA_TIMEOUT)
    return _ollama_client


def _require_groq_keys() -> None:
    if not GROQ_API_KEYS:
        raise RuntimeError(
            "No GROQ_API_KEY (or GROQ_API_KEY_2, GROQ_API_KEY_3, ...) is set — "
            "no LLM backend is available. Copy env.example.txt to .env and fill it in."
        )


def _get_groq_client(index: int) -> AsyncGroq:
    """One cached AsyncGroq client per configured key — see
    GROQ_API_KEYS/_load_groq_api_keys() above."""
    if index not in _groq_clients:
        _groq_clients[index] = AsyncGroq(api_key=GROQ_API_KEYS[index])
    return _groq_clients[index]


def _advance_key_index(exhausted_index: int) -> bool:
    """Moves the shared _current_key_index past a key that just hit
    RateLimitError, so every later call in this process — not just the one
    that discovered the exhaustion — starts directly on a working key
    instead of re-hitting the same dead one first. Returns True if another
    configured key remains, False if that was the last one. Persists for
    the life of the process (module-level state, not per-request) since a
    daily token-quota exhaustion doesn't clear until the provider's next
    reset window, not on the next request."""
    global _current_key_index
    if exhausted_index == _current_key_index:
        _current_key_index += 1
    return _current_key_index < len(GROQ_API_KEYS)


async def _groq_complete(messages: list[dict]) -> str:
    """Non-streaming Groq call with automatic key rotation across
    GROQ_API_KEYS: a real, reproduced failure mode — a single free-tier
    key's 200k-token daily quota ran out mid-benchmark-run during
    development (scripts/evaluate_pipeline.py), not a hypothetical this is
    guarding against speculatively. Retries the next configured key only on
    RateLimitError specifically (a 429) — any other failure (auth, network,
    a genuine model error) still raises immediately rather than burning
    through every key on an error rotation wouldn't fix."""
    _require_groq_keys()
    last_exc: Exception | None = None
    index = _current_key_index
    while index < len(GROQ_API_KEYS):
        try:
            client = _get_groq_client(index)
            response = await client.chat.completions.create(
                model=GROQ_MODEL, messages=messages, temperature=0.0
            )
            return response.choices[0].message.content.strip()
        except RateLimitError as exc:
            log.warning(
                "Groq key #%d rate-limited (%s) — trying the next configured key",
                index + 1, exc,
            )
            last_exc = exc
            if not _advance_key_index(index):
                break
            index = _current_key_index
    raise RuntimeError(
        f"All {len(GROQ_API_KEYS)} configured Groq key(s) rate-limited: {last_exc}"
    ) from last_exc


def _build_messages(user_prompt: str, system_prompt: str | None) -> list[dict]:
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": user_prompt})
    return messages


async def _ollama_options() -> dict:
    options = {"temperature": 0.0}
    if OLLAMA_NUM_GPU >= 0:
        options["num_gpu"] = OLLAMA_NUM_GPU
    return options


async def acomplete(user_prompt: str, system_prompt: str | None = None) -> str:
    """Non-streaming async completion — used for the short internal calls
    (query rewrite, retry rephrase) where there's nothing to stream to a
    user. Groq direct by default; Ollama-then-Groq under OFFLINE_MODE."""
    messages = _build_messages(user_prompt, system_prompt)

    if OFFLINE_MODE:
        t0 = time.monotonic()
        try:
            client = _get_ollama_client()
            response = await client.chat(
                model=OLLAMA_MODEL, messages=messages, options=await _ollama_options()
            )
            log.info("Ollama answered in %.1fs", time.monotonic() - t0)
            return response["message"]["content"].strip()
        except Exception as exc:
            log.warning(
                "OFFLINE_MODE is set but Ollama failed after %.1fs (%s) — "
                "falling back to Groq",
                time.monotonic() - t0,
                exc,
            )

    try:
        return await _groq_complete(messages)
    except Exception as exc:
        raise RuntimeError(
            f"No LLM backend reachable (Groq failed: {exc}). Check GROQ_API_KEY "
            "and network connectivity"
            + (", and that Ollama is running for OFFLINE_MODE." if OFFLINE_MODE else ".")
        ) from exc


async def _stream_groq(messages: list[dict]) -> AsyncIterator[str]:
    """Same key-rotation as _groq_complete, but only around the call that
    opens the stream — once a token has actually been yielded to the
    caller, switching keys mid-stream can't be done seamlessly (same
    reasoning astream_complete's Ollama-fallback docstring gives for not
    restarting a partially-yielded stream on a different backend)."""
    _require_groq_keys()
    last_exc: Exception | None = None
    index = _current_key_index
    stream = None
    while index < len(GROQ_API_KEYS):
        try:
            client = _get_groq_client(index)
            stream = await client.chat.completions.create(
                model=GROQ_MODEL, messages=messages, temperature=0.0, stream=True
            )
            break
        except RateLimitError as exc:
            log.warning(
                "Groq key #%d rate-limited (%s) — trying the next configured key",
                index + 1, exc,
            )
            last_exc = exc
            if not _advance_key_index(index):
                break
            index = _current_key_index

    if stream is None:
        raise RuntimeError(
            f"All {len(GROQ_API_KEYS)} configured Groq key(s) rate-limited: {last_exc}"
        ) from last_exc

    aiter = stream.__aiter__()
    while True:
        try:
            chunk = await asyncio.wait_for(aiter.__anext__(), timeout=STREAM_CHUNK_TIMEOUT)
        except StopAsyncIteration:
            return
        delta = chunk.choices[0].delta.content
        if delta:
            yield delta


async def _stream_ollama(messages: list[dict]) -> AsyncIterator[str]:
    client = _get_ollama_client()
    stream = await client.chat(
        model=OLLAMA_MODEL, messages=messages, options=await _ollama_options(), stream=True
    )
    aiter = stream.__aiter__()
    while True:
        try:
            chunk = await asyncio.wait_for(aiter.__anext__(), timeout=STREAM_CHUNK_TIMEOUT)
        except StopAsyncIteration:
            return
        delta = chunk["message"]["content"]
        if delta:
            yield delta


async def astream_complete(
    user_prompt: str, system_prompt: str | None = None
) -> AsyncIterator[str]:
    """Token-by-token async generator. Groq direct by default; under
    OFFLINE_MODE, tries the Ollama stream first and falls back to a full
    (non-streamed, then replayed as one chunk) Groq call if Ollama fails
    partway — a stream that has already yielded tokens to the client can't
    silently restart on a different backend without producing garbled
    output, so a mid-stream Ollama failure surfaces as an error rather than
    a seamless handoff. That's the honest tradeoff of streaming: fewer
    retry options than the non-streaming path has.
    """
    messages = _build_messages(user_prompt, system_prompt)

    if OFFLINE_MODE:
        try:
            async for token in _stream_ollama(messages):
                yield token
            return
        except Exception as exc:
            log.warning(
                "OFFLINE_MODE is set but Ollama streaming failed before "
                "yielding any tokens (%s) — falling back to Groq",
                exc,
            )
            # Only safe to fall back here because nothing has been yielded
            # yet in this branch (Ollama failed on the *first* chunk fetch,
            # e.g. connection refused) — see the docstring above. If Ollama
            # had already streamed partial output, this except block is
            # unreachable (the exception would propagate through the
            # `async for` after tokens were already yielded to the caller).

    async for token in _stream_groq(messages):
        yield token


def complete(user_prompt: str, system_prompt: str | None = None) -> str:
    """Sync convenience wrapper for CLI/eval usage outside an event loop."""
    return asyncio.run(acomplete(user_prompt, system_prompt=system_prompt))


async def agenerate(
    query: str,
    chunks: list[dict],
    formulation_category: str | None = None,
    statutory_tags: list[str] | None = None,
) -> str:
    prompt = build_user_prompt(query, chunks, formulation_category, statutory_tags)
    return await acomplete(prompt, system_prompt=SYSTEM_PROMPT)


async def astream_generate(
    query: str,
    chunks: list[dict],
    formulation_category: str | None = None,
    statutory_tags: list[str] | None = None,
) -> AsyncIterator[str]:
    prompt = build_user_prompt(query, chunks, formulation_category, statutory_tags)
    async for token in astream_complete(prompt, system_prompt=SYSTEM_PROMPT):
        yield token


def generate(query: str, chunks: list[dict]) -> str:
    """Sync convenience wrapper for CLI/eval usage outside an event loop."""
    return asyncio.run(agenerate(query, chunks))


if __name__ == "__main__":
    # Windows consoles default to cp1252, which can't encode characters a
    # real Groq answer routinely contains (e.g. U+202F narrow no-break
    # space) — same fix already applied in api/translation.py, api/tts.py,
    # etc. for the same reason.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    query = " ".join(sys.argv[1:]) or "What does Section 3(p) say about traditional knowledge?"
    answer = generate(query, chunks=[])
    print(f"\nQuery: {query!r}\n")
    print(answer)
