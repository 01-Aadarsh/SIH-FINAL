"""
Tests for GET /sources/{filename} (api/main.py) — serves the source PDFs
citations point at, for the frontend's "View source PDF" link. Pure/
deterministic against the filesystem, no DB/LLM needed.

Usage:
    python -m pytest tests/test_sources_endpoint.py -v
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from api.main import app

client = TestClient(app)


def test_real_india_source_file_is_served():
    response = client.get("/sources/Patents_Act_1970.pdf")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content.startswith(b"%PDF")


def test_real_international_source_file_is_served():
    """International PDFs live under data/international/ — the endpoint
    must check both locations, not just DATA_DIR itself."""
    response = client.get("/sources/WIPO_GRATK_Treaty_2024.pdf")
    assert response.status_code == 200
    assert response.content.startswith(b"%PDF")


def test_unknown_filename_returns_404():
    response = client.get("/sources/NotARealFile.pdf")
    assert response.status_code == 404


def test_path_traversal_is_rejected_not_served():
    """A citation's source_file is always a bare filename in practice, but
    this must not trust that -- reject any attempt to escape DATA_DIR via
    a path separator or '..' component before ever touching the
    filesystem."""
    for attempt in ["../.env", "..%2F.env", "sub/dir.pdf"]:
        response = client.get(f"/sources/{attempt}")
        assert response.status_code in (400, 404), (attempt, response.status_code)
        assert response.status_code != 200
