"""Aplicação FastAPI: ciclo de vida, tratamento de erros e as rotas de `app/api/` (seção 12 do plano)."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import addresses, admin, auth, lists, plans, profile, rules, suggestions
from app.cart.changes import ActionError
from app.core.config import FRONTEND
from app.core.db import fail_interrupted_plans, init_db
from app.mcp.client import McpToolError, NotAuthenticated
from app.mcp.ifood import ifood
from app.proactive import scheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    if (n := fail_interrupted_plans()):
        logging.getLogger(__name__).warning("%d plano(s) interrompido(s) marcados como falha", n)
    scheduler.start()
    yield
    scheduler.stop()
    if ifood().connection:
        await ifood().connection.close()


app = FastAPI(title="Gnomers", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=[FRONTEND], allow_methods=["*"], allow_headers=["*"],
                   allow_credentials=True)


@app.exception_handler(McpToolError)
async def _mcp_error(_: Request, exc: McpToolError):
    return JSONResponse(status_code=502, content={"detail": exc.user_message, "error": exc.to_dict()})


@app.exception_handler(NotAuthenticated)
async def _auth_error(_: Request, exc: NotAuthenticated):
    return JSONResponse(status_code=401, content={"detail": str(exc), "login": "/auth/login"})


@app.exception_handler(ActionError)
async def _action_error(_: Request, exc: ActionError):
    return JSONResponse(status_code=exc.status, content={"detail": str(exc)})


@app.exception_handler(ValueError)
async def _value_error(_: Request, exc: ValueError):
    return JSONResponse(status_code=422, content={"detail": str(exc)})


# a ordem dos routers é a ordem das rotas no contract/openapi.json
for module in (auth, profile, lists, rules, addresses, plans, suggestions, admin):
    app.include_router(module.router)
