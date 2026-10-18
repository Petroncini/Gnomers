"""Renovação do token do iFood e erros de login (sem rede)."""

import time

import httpx2
import pytest
from sqlmodel import Session

from app.core.db import engine, init_db
from app.core.models import User
from app.mcp import client as mcp_client
from app.mcp.client import (
    DbTokenStorage,
    NotAuthenticated,
    _auth_error_in,
    refresh_tokens,
)


@pytest.fixture
def storage():
    init_db()
    st = DbTokenStorage(engine)
    with Session(engine) as s:
        user = s.get(User, 1)
        user.ifood_token = {"tokens": {"access_token": "velho", "token_type": "Bearer", "expires_in": 7199,
                                       "refresh_token": "r1"}, "saved_at": time.time() - 7500}
        s.add(user)
        s.commit()
    yield st
    st.clear()


def mock_token_endpoint(monkeypatch, response: httpx2.Response, seen: list):
    def handler(request):
        seen.append(request.content.decode())
        return response

    real = httpx2.AsyncClient
    monkeypatch.setattr(mcp_client, "_token_endpoint", "https://id.ifood.com.br/token")
    monkeypatch.setattr(mcp_client.httpx2, "AsyncClient", lambda **kw: real(transport=httpx2.MockTransport(handler)))


def test_expired_token_is_detected(storage):
    assert storage.expires_in() < 0
    assert storage.has_tokens()


async def test_refresh_saves_new_token_and_keeps_refresh_token(storage, monkeypatch):
    seen: list = []
    mock_token_endpoint(monkeypatch, httpx2.Response(200, json={"access_token": "novo", "token_type": "Bearer",
                                                                "expires_in": 7199}), seen)
    await refresh_tokens(storage)
    assert "grant_type=refresh_token" in seen[0] and "refresh_token=r1" in seen[0]
    tokens = (await storage.get_tokens())
    assert tokens.access_token == "novo" and tokens.refresh_token == "r1"  # o iFood não rodou o refresh_token
    assert storage.expires_in() > 7000


async def test_refused_refresh_asks_for_login(storage, monkeypatch):
    mock_token_endpoint(monkeypatch, httpx2.Response(400, json={"error": "invalid_grant"}), [])
    with pytest.raises(NotAuthenticated):
        await refresh_tokens(storage)
    assert not storage.has_tokens()  # /auth/status passa a mostrar "Entrar no iFood"


def test_auth_error_inside_exception_group():
    group = BaseExceptionGroup("unhandled errors in a TaskGroup", [NotAuthenticated("login no iFood necessário")])
    assert _auth_error_in(group)
    assert not _auth_error_in(BaseExceptionGroup("x", [RuntimeError("rede")]))
