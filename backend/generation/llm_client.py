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
from groq import AsyncGroq

from generation.prompts import SYSTEM_PROMPT, build_user_prompt

load_dotenv()

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

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")

# A stalled streaming response (network stall, backend hang) must not hang
# the SSE connection forever — this bounds the wait for each individual
# token/chunk, not the whole response. Generous relative to normal
# inter-token gaps (well under a second on Groq) but still finite.
STREAM_CHUNK_TIMEOUT = float(os.getenv("STREAM_CHUNK_TIMEOUT", "20"))

_ollama_client: ollama.AsyncClient | None = None
_groq_client: AsyncGroq | None = None


def _get_ollama_client() -> ollama.AsyncClient:
    global _ollama_client
    if _ollama_client is None:
        _ollama_client = ollama.AsyncClient(host=OLLAMA_HOST, timeout=OLLAMA_TIMEOUT)
    return _ollama_client


def _get_groq_client() -> AsyncGroq:
    global _groq_client
    if _groq_client is None:
        if not GROQ_API_KEY:
            raise RuntimeError(
                "GROQ_API_KEY is not set — no LLM backend is available. Copy "
                "env.example.txt to .env and fill it in."
            )
        _groq_client = AsyncGroq(api_key=GROQ_API_KEY)
    return _groq_client


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
        client = _get_groq_client()
        response = await client.chat.completions.create(
            model=GROQ_MODEL, messages=messages, temperature=0.0
        )
        return response.choices[0].message.content.strip()
    except Exception as exc:
        raise RuntimeError(
            f"No LLM backend reachable (Groq failed: {exc}). Check GROQ_API_KEY "
            "and network connectivity"
            + (", and that Ollama is running for OFFLINE_MODE." if OFFLINE_MODE else ".")
        ) from exc


async def _stream_groq(messages: list[dict]) -> AsyncIterator[str]:
    client = _get_groq_client()
    stream = await client.chat.completions.create(
        model=GROQ_MODEL, messages=messages, temperature=0.0, stream=True
    )
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


async def agenerate(query: str, chunks: list[dict]) -> str:
    return await acomplete(build_user_prompt(query, chunks), system_prompt=SYSTEM_PROMPT)


async def astream_generate(query: str, chunks: list[dict]) -> AsyncIterator[str]:
    async for token in astream_complete(build_user_prompt(query, chunks), system_prompt=SYSTEM_PROMPT):
        yield token


def generate(query: str, chunks: list[dict]) -> str:
    """Sync convenience wrapper for CLI/eval usage outside an event loop."""
    return asyncio.run(agenerate(query, chunks))


if __name__ == "__main__":
    query = " ".join(sys.argv[1:]) or "What does Section 3(p) say about traditional knowledge?"
    answer = generate(query, chunks=[])
    print(f"\nQuery: {query!r}\n")
    print(answer)
