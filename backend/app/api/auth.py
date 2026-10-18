"""Login no iFood pelo OAuth do back-end e estado da conexão."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session

from app.ai import llm
from app.api import schemas as S
from app.core.config import FRONTEND
from app.core.db import current_user, get_session
from app.mcp.ifood import ifood

router = APIRouter()


@router.get("/auth/status", response_model=S.AuthStatus)
async def auth_status(s: Session = Depends(get_session)):
    api = ifood()
    user = current_user(s)
    connected = api.connection.connected if api.connection else True
    return {"mode": api.mode, "connected": connected, "has_token": (api.connection.storage.has_tokens() if api.connection else True),
            "llm": llm.enabled(), "imported_history": user.imported_history}


@router.get("/auth/login")
async def auth_login():
    api = ifood()
    if api.connection is None:
        return RedirectResponse(FRONTEND)
    url = await api.connection.login()
    return RedirectResponse(url or FRONTEND)


@router.get("/callback")
async def auth_callback(code: str | None = None, state: str | None = None, iss: str | None = None,
                        error: str | None = None):
    api = ifood()
    ok = False
    if not error and code and api.connection is not None:
        api.connection.deliver_code(code, state, iss)
        try:
            ok = await api.connection.wait_connected(timeout=60)
        except asyncio.TimeoutError:
            ok = False
    # página própria em vez de redirecionar: o front-end pode não estar rodando
    title = "Conectado ao iFood" if ok else "O login no iFood falhou"
    detail = "Pode fechar esta aba e voltar ao app." if ok else f"Tente de novo em /auth/login. {error or ''}"
    return HTMLResponse(f"""<!doctype html><meta charset="utf-8"><title>{title}</title>
<body style="font-family:system-ui;max-width:32rem;margin:4rem auto;padding:0 1rem">
<h1>{title}</h1><p>{detail}</p><p><a href="{FRONTEND}/?login={'ok' if ok else 'erro'}">Abrir o Gnomers</a></p></body>""",
                        status_code=200 if ok else 400)


@router.post("/auth/logout", response_model=S.Ok)
async def auth_logout():
    api = ifood()
    if api.connection:
        await api.connection.close()
        api.connection.storage.clear()
    return {"ok": True}
