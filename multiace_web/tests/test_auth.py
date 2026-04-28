import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from multiace_web.auth import TokenAuth


def make_app(token: str | None) -> FastAPI:
    app = FastAPI()
    app.add_middleware(TokenAuth, token=token)

    @app.get("/api/foo")
    async def foo():
        return {"ok": True}

    @app.get("/health")
    async def health():
        return {"ok": True}

    return app


def test_no_token_configured_allows_all_traffic():
    client = TestClient(make_app(token=None))
    assert client.get("/api/foo").status_code == 200


def test_token_configured_requires_authorization():
    client = TestClient(make_app(token="secret"))
    assert client.get("/api/foo").status_code == 401


def test_token_configured_accepts_correct_bearer():
    client = TestClient(make_app(token="secret"))
    resp = client.get("/api/foo", headers={"Authorization": "Bearer secret"})
    assert resp.status_code == 200


def test_token_configured_rejects_wrong_bearer():
    client = TestClient(make_app(token="secret"))
    resp = client.get("/api/foo", headers={"Authorization": "Bearer wrong"})
    assert resp.status_code == 401


def test_token_does_not_block_non_api_routes():
    """The /health and /static/* paths shouldn't require auth — only /api/* and /ws."""
    client = TestClient(make_app(token="secret"))
    assert client.get("/health").status_code == 200
