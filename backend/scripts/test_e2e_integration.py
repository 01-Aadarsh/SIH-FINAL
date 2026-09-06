"""
End-to-end integration checks for IP-SAKTI Sahayak — run in-process
against the real FastAPI app (httpx.AsyncClient over an ASGI transport, no
separate `python run.py` needed) but exercising the REAL pipeline
underneath: real Postgres/pgvector, real embeddings, real Groq calls, and
one real Sarvam ASR call. Not a pytest suite (kept separate, run directly)
— this is a one-shot go/no-go read on the live system, printing a clear
PASS/FAIL per check and a summary, with a non-zero exit code if anything
failed.

A note on two checks that are deliberately NOT pinned to exact LLM output:
  - The India/Triphala check verifies Section 3(p) is cited (reliable —
    verified repeatedly across this project's own test suite and eval
    runs) but does NOT assert a specific actionable_form_id, because which
    form (if any) a bare "is this patentable" question surfaces depends on
    which chunks retrieval happens to fuse in for that exact phrasing —
    real LLM/retrieval variance, not something this script should paper
    over with a brittle assertion. The actual "does the system attach NBA
    Form 7 for a patent-related intent" capability is instead checked
    directly and deterministically against the same match_forms() logic
    /query calls, via the compliance/forms endpoint with wording already
    proven (backend/tests/test_forms.py) to trigger it — a real assertion
    that doesn't depend on LLM output, which is what "verify the wiring
    works" should actually mean here.

Usage (from backend/, venv active):
    python scripts/test_e2e_integration.py
"""

from __future__ import annotations

import asyncio
import json
import math
import struct
import sys
import wave
from io import BytesIO
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

import httpx
from dotenv import load_dotenv

load_dotenv(override=True)

from api.main import app  # noqa: E402 -- must follow the sys.path insert above

RESULTS: list[tuple[str, bool, str]] = []


def record(name: str, passed: bool, detail: str = "") -> bool:
    RESULTS.append((name, passed, detail))
    status = "PASS" if passed else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail else ""))
    return passed


def _tiny_wav_bytes(seconds: float = 1.2, freq: int = 220, sample_rate: int = 16000) -> bytes:
    """A short, real, decodable sine-tone WAV — enough to exercise a real
    Sarvam STT call's request/response plumbing. Not real speech, so an
    empty transcript back is a normal, correct result, not a failure."""
    buf = BytesIO()
    with wave.open(buf, "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(sample_rate)
        frames = bytearray()
        for i in range(int(sample_rate * seconds)):
            val = int(3000 * math.sin(2 * math.pi * freq * i / sample_rate))
            frames += struct.pack("<h", val)
        f.writeframes(bytes(frames))
    return buf.getvalue()


async def check_india_triphala(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/query",
        json={
            "question": "Can I obtain a patent in India for a standard classical Triphala churna formulation?",
            "jurisdiction": "india",
        },
    )
    if resp.status_code != 200:
        record("(a) India/Triphala: 200 OK", False, f"status {resp.status_code}: {resp.text[:200]}")
        return
    record("(a) India/Triphala: 200 OK", True)

    body = resp.json()
    haystack = (
        " ".join(f"{c['source_file']} {c['section_heading']}" for c in body["citations"])
        + " "
        + body["answer"]
    ).lower()
    cites_3p = "3(p)" in haystack
    record(
        "(a) India/Triphala: cites Section 3(p)",
        cites_3p,
        f"formulation_category={body.get('formulation_category')}, "
        f"confidence={body.get('confidence_score'):.3f}, abstained={body['flags']['abstained']}",
    )
    # actionable_forms is a real, structurally-typed field regardless of
    # which forms (if any) this specific phrasing happens to surface --
    # see module docstring for why a specific form_id isn't asserted here.
    record(
        "(a) India/Triphala: actionable_forms is a well-formed list",
        isinstance(body.get("actionable_forms"), list),
        f"actionable_forms={[f['form_id'] for f in body.get('actionable_forms', [])]}",
    )


async def check_form_navigator_nba_form_7(client: httpx.AsyncClient) -> None:
    """The deterministic half of the NBA-Form-7 check (see module
    docstring) — same match_forms() logic /query uses, with wording
    backend/tests/test_forms.py already proves triggers it, so this is a
    real assertion, not a coin flip on retrieval variance."""
    resp = await client.get(
        "/api/v1/compliance/forms",
        params={"intent": "I want to patent my Ayurvedic formulation", "jurisdiction": "india"},
    )
    if resp.status_code != 200:
        record("(a) Form Navigator: NBA Form 7 for patent intent", False, f"status {resp.status_code}")
        return
    form_ids = {f["form_id"] for f in resp.json().get("forms", [])}
    record(
        "(a) Form Navigator: NBA Form 7 for patent intent",
        "NBA_FORM_7" in form_ids,
        f"forms={sorted(form_ids)}",
    )


async def check_international_wipo(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/query",
        json={
            "question": "What are the mandatory disclosure of origin obligations for patent applicants under the WIPO GRATK Treaty 2024?",
            "jurisdiction": "international",
        },
    )
    if resp.status_code != 200:
        record("(b) International/WIPO: 200 OK", False, f"status {resp.status_code}: {resp.text[:200]}")
        return
    record("(b) International/WIPO: 200 OK", True)

    body = resp.json()
    citations = body["citations"]
    haystack = (" ".join(f"{c['source_file']} {c['section_heading']}" for c in citations) + " " + body["answer"]).lower()
    cites_gratk = "gratk" in haystack or "article 3" in haystack or "genetic resources" in haystack
    record(
        "(b) International/WIPO: cites WIPO GRATK Treaty",
        cites_gratk,
        f"citations={[c['source_file'] for c in citations]}",
    )

    # Jurisdiction partitioning is a hard filter at the retrieval layer
    # (ingestion/indexer.py builds one BM25 index per jurisdiction, and
    # dense retrieval filters at the SQL level) -- not LLM-dependent, so
    # this is a real, reliable assertion: every citation for an
    # international-jurisdiction query must come from data/international/,
    # never a domestic Indian Act.
    domestic_markers = ("patents_act", "biological_diversity_act", "trade_marks_act", "gi_act", "drugs_and_cosmetics")
    no_domestic_mix = not any(
        marker in c["source_file"].lower() for c in citations for marker in domestic_markers
    )
    record(
        "(b) International/WIPO: no domestic Indian Acts in citations",
        no_domestic_mix,
        f"citations={[c['source_file'] for c in citations]}",
    )


async def check_streaming(client: httpx.AsyncClient) -> None:
    token_count = 0
    done_payload: dict | None = None
    event = None
    data_lines: list[str] = []

    try:
        async with client.stream(
            "POST",
            "/query/stream",
            json={"question": "What does Section 3(p) say about traditional knowledge?", "jurisdiction": "india"},
        ) as resp:
            if resp.status_code != 200:
                record("(c) Streaming: connects (200 OK)", False, f"status {resp.status_code}")
                return
            record("(c) Streaming: connects (200 OK)", True)

            async for line in resp.aiter_lines():
                if line.startswith("event:"):
                    event = line[len("event:"):].strip()
                    data_lines = []
                elif line.startswith("data:"):
                    data_lines.append(line[len("data:"):].strip())
                elif line == "" and event:
                    payload = "\n".join(data_lines)
                    if event == "token":
                        token_count += 1
                    elif event == "done":
                        done_payload = json.loads(payload)
                    elif event == "error":
                        record("(c) Streaming: no error event", False, payload)
                        return
                    event = None
    except httpx.HTTPError as exc:
        record("(c) Streaming: connects (200 OK)", False, str(exc))
        return

    record("(c) Streaming: received at least one token event", token_count > 0, f"{token_count} token events")

    if done_payload is None:
        record("(c) Streaming: done event received", False)
        return
    expected_keys = {
        "answer", "citations", "flags", "formulation_category", "confidence_score",
        "needs_clarification", "clarifying_questions", "related_provisions", "actionable_forms",
    }
    missing = expected_keys - done_payload.keys()
    record(
        "(c) Streaming: done event has the expected shape",
        not missing,
        f"missing keys: {sorted(missing)}" if missing else f"answer length={len(done_payload.get('answer', ''))}",
    )


async def check_abstention(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/query",
        json={"question": "What is the capital of France?", "jurisdiction": "india"},
    )
    if resp.status_code != 200:
        record("(d) Abstention: 200 OK", False, f"status {resp.status_code}")
        return
    body = resp.json()
    record("(d) Abstention: flags.abstained is true", body["flags"]["abstained"] is True, f"abstained={body['flags']['abstained']}")
    record(
        "(d) Abstention: zero actionable_forms attached",
        body.get("actionable_forms") == [],
        f"actionable_forms={body.get('actionable_forms')}",
    )
    record(
        "(d) Abstention: citations are empty",
        body.get("citations") == [],
        f"citations={body.get('citations')}",
    )


async def check_sources_endpoint(client: httpx.AsyncClient) -> None:
    resp = await client.get("/sources/Biological_Diversity_Act_2002.pdf")
    record("(e) /sources: 200 OK", resp.status_code == 200, f"status {resp.status_code}")
    record(
        "(e) /sources: content-type is application/pdf",
        resp.headers.get("content-type") == "application/pdf",
        f"content-type={resp.headers.get('content-type')}",
    )
    record(
        "(e) /sources: body is a real PDF",
        resp.content[:4] == b"%PDF",
        f"first bytes={resp.content[:8]!r}",
    )

    # Path-traversal must be rejected before ever touching the filesystem
    # (backend/api/main.py::get_source) -- a real security check, not
    # incidental to this feature.
    traversal_resp = await client.get("/sources/..%2F.env")
    record(
        "(e) /sources: path traversal rejected",
        traversal_resp.status_code in (400, 404),
        f"status {traversal_resp.status_code}",
    )


async def check_voice_transcribe_contract(client: httpx.AsyncClient) -> None:
    # Malformed: no file at all -- FastAPI's own required-File validation should 422.
    resp = await client.post("/api/v1/voice/transcribe", data={"language_code": "unknown"})
    record(
        "(f) /voice/transcribe: missing file rejected (422)",
        resp.status_code == 422,
        f"status {resp.status_code}",
    )

    # Malformed: wrong extension -- api/asr.py::_validate_extension should 415.
    resp = await client.post(
        "/api/v1/voice/transcribe",
        files={"file": ("notes.txt", b"not audio", "text/plain")},
    )
    record(
        "(f) /voice/transcribe: wrong file extension rejected (415)",
        resp.status_code == 415,
        f"status {resp.status_code}",
    )

    # Malformed: invalid language_code -- should 422, not silently proceed.
    resp = await client.post(
        "/api/v1/voice/transcribe",
        files={"file": ("voice.wav", _tiny_wav_bytes(), "audio/wav")},
        data={"language_code": "xx-YY"},
    )
    record(
        "(f) /voice/transcribe: invalid language_code rejected (422)",
        resp.status_code == 422,
        f"status {resp.status_code}",
    )

    # Real call: a genuine (non-speech) WAV should still get a well-formed
    # 200 response -- this is the one real Sarvam API call in this script.
    resp = await client.post(
        "/api/v1/voice/transcribe",
        files={"file": ("voice.wav", _tiny_wav_bytes(), "audio/wav")},
        data={"language_code": "en-IN"},
    )
    if resp.status_code != 200:
        record("(f) /voice/transcribe: real call returns 200", False, f"status {resp.status_code}: {resp.text[:200]}")
        return
    record("(f) /voice/transcribe: real call returns 200", True)
    body = resp.json()
    record(
        "(f) /voice/transcribe: response has the documented schema",
        isinstance(body.get("transcript"), str) and isinstance(body.get("detected_language"), str),
        f"keys={sorted(body.keys())}",
    )


async def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("Running end-to-end integration checks against the real live pipeline")
    print("(in-process ASGI transport, real DB + real Groq + one real Sarvam call)\n")

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver", timeout=120.0) as client:
        await check_india_triphala(client)
        await check_form_navigator_nba_form_7(client)
        await check_international_wipo(client)
        await check_streaming(client)
        await check_abstention(client)
        await check_sources_endpoint(client)
        await check_voice_transcribe_contract(client)

    print()
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print(f"{passed}/{total} checks passed")

    failures = [(name, detail) for name, ok, detail in RESULTS if not ok]
    if failures:
        print("\nFailed checks:")
        for name, detail in failures:
            print(f"  - {name}" + (f" ({detail})" if detail else ""))
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
