"""
Tests for speech-to-text (api/asr.py) and the two voice endpoints in
api/main.py, mocking Sarvam's API — unlike most of this project's test
suite (test_retrieval_determinism.py, test_translation_term_protection.py,
etc.), which deliberately runs against the real live pipeline. Mocked here
because a true test would need real audio fixtures and network access on
every CI run just to exercise this module's own request/response handling
and error mapping — logic this project owns, not Sarvam's transcription
accuracy, which mocking correctly does not attempt to verify. (A real,
live round-trip smoke test — synthesize speech with api/tts.py, feed it
back through transcribe_audio(), confirm the transcript matches — was run
manually during development; see git history / idea.md for that
verification. It is not repeated here as an automated test to keep this
suite offline-runnable.)

Usage:
    python -m pytest tests/test_asr.py -v
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from api import asr
from api.asr import UnsupportedAudioFormat, transcribe_audio
from api.main import app

FAKE_AUDIO = b"RIFF....WAVEfmt fake bytes, content irrelevant to a mocked call"


def _fake_response(json_body: dict, status_code: int = 200) -> httpx.Response:
    return httpx.Response(
        status_code=status_code,
        json=json_body,
        request=httpx.Request("POST", "https://api.sarvam.ai/speech-to-text"),
    )


# --------------------------------------------------------------------------
# transcribe_audio() unit tests
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unsupported_extension_raises_before_any_network_call():
    with patch.object(asr, "_get_client") as mock_get_client:
        with pytest.raises(UnsupportedAudioFormat):
            await transcribe_audio(FAKE_AUDIO, "voice.txt", "unknown")
        mock_get_client.assert_not_called()


@pytest.mark.asyncio
async def test_invalid_language_code_raises_before_any_network_call():
    with patch.object(asr, "_get_client") as mock_get_client:
        with pytest.raises(ValueError, match="language_code"):
            await transcribe_audio(FAKE_AUDIO, "voice.wav", "xx-YY")
        mock_get_client.assert_not_called()


@pytest.mark.asyncio
async def test_missing_api_key_raises_runtime_error():
    with patch.object(asr, "SARVAM_API_KEY", None):
        with pytest.raises(RuntimeError, match="SARVAM_API_KEY"):
            await transcribe_audio(FAKE_AUDIO, "voice.wav", "unknown")


@pytest.mark.asyncio
async def test_successful_transcription_parses_transcript_and_language():
    mock_client = AsyncMock()
    mock_client.post.return_value = _fake_response(
        {"request_id": "abc123", "transcript": "traditional knowledge is not patentable",
         "language_code": "en-IN", "language_probability": 0.98}
    )
    with patch.object(asr, "SARVAM_API_KEY", "fake-key"), \
         patch.object(asr, "_get_client", return_value=mock_client):
        transcript, detected_language = await transcribe_audio(FAKE_AUDIO, "voice.wav", "unknown")

    assert transcript == "traditional knowledge is not patentable"
    assert detected_language == "en-IN"

    # Verify the actual request shape sent to Sarvam — endpoint, auth
    # header, and multipart/form fields, not just that *a* call happened.
    _, kwargs = mock_client.post.call_args
    assert mock_client.post.call_args[0][0] == "/speech-to-text"
    assert kwargs["headers"] == {"api-subscription-key": "fake-key"}
    assert kwargs["files"]["file"][0] == "voice.wav"
    assert kwargs["data"]["language_code"] == "unknown"
    assert kwargs["data"]["mode"] == "transcribe"


@pytest.mark.asyncio
async def test_hindi_language_code_is_passed_through():
    mock_client = AsyncMock()
    mock_client.post.return_value = _fake_response(
        {"transcript": "पारंपरिक ज्ञान पेटेंट योग्य नहीं है", "language_code": "hi-IN"}
    )
    with patch.object(asr, "SARVAM_API_KEY", "fake-key"), \
         patch.object(asr, "_get_client", return_value=mock_client):
        transcript, detected_language = await transcribe_audio(FAKE_AUDIO, "voice.wav", "hi-IN")

    assert detected_language == "hi-IN"
    assert transcript
    assert mock_client.post.call_args.kwargs["data"]["language_code"] == "hi-IN"


@pytest.mark.asyncio
async def test_sarvam_http_error_raises_runtime_error_with_detail():
    mock_client = AsyncMock()
    error_response = _fake_response({"error": "invalid model"}, status_code=400)
    mock_client.post.return_value = error_response
    # httpx.Response.raise_for_status() needs a matching Request bound;
    # _fake_response already supplies one.
    with patch.object(asr, "SARVAM_API_KEY", "fake-key"), \
         patch.object(asr, "_get_client", return_value=mock_client):
        with pytest.raises(RuntimeError, match="400"):
            await transcribe_audio(FAKE_AUDIO, "voice.wav", "unknown")


@pytest.mark.asyncio
async def test_response_missing_transcript_field_raises_runtime_error():
    mock_client = AsyncMock()
    mock_client.post.return_value = _fake_response({"request_id": "abc123"})
    with patch.object(asr, "SARVAM_API_KEY", "fake-key"), \
         patch.object(asr, "_get_client", return_value=mock_client):
        with pytest.raises(RuntimeError, match="no transcript"):
            await transcribe_audio(FAKE_AUDIO, "voice.wav", "unknown")


# --------------------------------------------------------------------------
# /api/v1/voice/transcribe and /api/v1/voice/query endpoint tests —
# transcribe_audio() and run_query() mocked at the api.main import site, so
# these never touch the real Sarvam API, database, or LLM.
# --------------------------------------------------------------------------

client = TestClient(app)


def test_voice_transcribe_endpoint_returns_mocked_transcript():
    with patch("api.main.transcribe_audio", new=AsyncMock(return_value=("hello world", "en-IN"))):
        response = client.post(
            "/api/v1/voice/transcribe",
            files={"file": ("voice.wav", FAKE_AUDIO, "audio/wav")},
            data={"language_code": "unknown"},
        )
    assert response.status_code == 200
    body = response.json()
    assert body == {"transcript": "hello world", "detected_language": "en-IN"}


def test_voice_transcribe_endpoint_maps_unsupported_format_to_415():
    with patch(
        "api.main.transcribe_audio",
        new=AsyncMock(side_effect=UnsupportedAudioFormat("bad format")),
    ):
        response = client.post(
            "/api/v1/voice/transcribe",
            files={"file": ("voice.txt", FAKE_AUDIO, "text/plain")},
        )
    assert response.status_code == 415


def test_voice_transcribe_endpoint_maps_sarvam_failure_to_502():
    with patch(
        "api.main.transcribe_audio",
        new=AsyncMock(side_effect=RuntimeError("Sarvam STT request failed: 500 ...")),
    ):
        response = client.post(
            "/api/v1/voice/transcribe",
            files={"file": ("voice.wav", FAKE_AUDIO, "audio/wav")},
        )
    assert response.status_code == 502


def test_voice_query_endpoint_pipes_transcript_into_run_query_and_merges_response():
    from api.main import QueryResponse

    fake_query_response = QueryResponse(
        answer="Section 3(p) excludes traditional knowledge from patentability.",
        citations=[],
        flags={"abstained": False, "retried": False, "weak_grounding": False},
        formulation_category="classical",
        confidence_score=0.99,
    )

    with patch(
        "api.main.transcribe_audio",
        new=AsyncMock(return_value=("what does section 3p say", "en-IN")),
    ) as mock_transcribe, patch(
        "api.main.run_query", new=AsyncMock(return_value=fake_query_response)
    ) as mock_run_query:
        response = client.post(
            "/api/v1/voice/query",
            files={"file": ("voice.wav", FAKE_AUDIO, "audio/wav")},
            data={"jurisdiction": "india"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["transcribed_text"] == "what does section 3p say"
    assert body["detected_language"] == "en-IN"
    assert body["answer"] == fake_query_response.answer
    assert body["formulation_category"] == "classical"

    mock_transcribe.assert_awaited_once()
    mock_run_query.assert_awaited_once()
    call_kwargs = mock_run_query.call_args.kwargs
    assert call_kwargs["question"] == "what does section 3p say"
    assert call_kwargs["jurisdiction"] == "india"
    assert call_kwargs["language"] == "en-IN"


def test_voice_query_endpoint_falls_back_to_english_for_undetected_language():
    """detected_language='unknown' (Sarvam couldn't detect it) must not be
    passed through as QueryRequest.language, which only accepts real BCP-47
    codes — falls back to en-IN rather than guessing or erroring."""
    from api.main import QueryResponse

    fake_query_response = QueryResponse(
        answer="...", citations=[], flags={"abstained": False, "retried": False, "weak_grounding": False},
        formulation_category="classical", confidence_score=0.5,
    )
    with patch(
        "api.main.transcribe_audio", new=AsyncMock(return_value=("hello", "unknown"))
    ), patch(
        "api.main.run_query", new=AsyncMock(return_value=fake_query_response)
    ) as mock_run_query:
        response = client.post(
            "/api/v1/voice/query",
            files={"file": ("voice.wav", FAKE_AUDIO, "audio/wav")},
        )

    assert response.status_code == 200
    assert mock_run_query.call_args.kwargs["language"] == "en-IN"
