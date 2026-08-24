"""CORS is driven by ALLOWED_ORIGINS, read at app-construction time.

Each test reloads app.main with the env set as needed; conftest captured its own
`app` reference at import, so these reloads don't affect the other test modules.
"""

import importlib

from fastapi.testclient import TestClient


def test_cors_allows_configured_origin(monkeypatch):
    monkeypatch.setenv("ALLOWED_ORIGINS", "http://localhost:5173")
    import app.main as main

    importlib.reload(main)
    try:
        resp = TestClient(main.app).get("/health", headers={"Origin": "http://localhost:5173"})
        assert resp.headers.get("access-control-allow-origin") == "http://localhost:5173"
    finally:
        monkeypatch.delenv("ALLOWED_ORIGINS", raising=False)
        importlib.reload(main)


def test_cors_exposes_the_request_id_header(monkeypatch):
    # Without `expose_headers`, a browser hides X-Request-ID from cross-origin JS
    # and `headers.get()` silently returns null — the same mechanism as SPEC.md
    # BUG1, which capped the deployed catalog at 25 units via X-Total-Count.
    monkeypatch.setenv("ALLOWED_ORIGINS", "http://localhost:5173")
    import app.main as main

    importlib.reload(main)
    try:
        resp = TestClient(main.app).get("/health", headers={"Origin": "http://localhost:5173"})
        exposed = resp.headers.get("access-control-expose-headers", "")
        assert main.REQUEST_ID_HEADER.lower() in exposed.lower()
    finally:
        monkeypatch.delenv("ALLOWED_ORIGINS", raising=False)
        importlib.reload(main)


def test_cors_absent_when_unset(monkeypatch):
    monkeypatch.delenv("ALLOWED_ORIGINS", raising=False)
    import app.main as main

    importlib.reload(main)
    resp = TestClient(main.app).get("/health", headers={"Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in resp.headers
