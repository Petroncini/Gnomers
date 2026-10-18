"""Sessão MCP com o iFood: OAuth, chamada genérica e normalização de erros.

O back-end é dono das chamadas ao MCP (seção 1 do plano). Uma única sessão
streamable HTTP fica aberta numa task própria e atende chamadas concorrentes.
O OAuth usa o provedor do SDK `mcp`, com o client ID e o secret do agente
cadastrado no portal e o token guardado em `User.ifood_token`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any

import httpx2
import mcp.client.auth.oauth2 as _oauth2
from mcp import ClientSession
from mcp.client.auth import OAuthClientProvider
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.auth import (
    AuthorizationCodeResult,
    OAuthClientInformationFull,
    OAuthClientMetadata,
    OAuthToken,
)
from sqlmodel import Session

from app.core.config import settings

log = logging.getLogger(__name__)

# Códigos que indicam falha passageira; vale repetir a mesma chamada uma vez.
# Códigos que indicam falha passageira; vale repetir a mesma chamada uma vez. TOOL_ERROR é o
# isError sem mensagem que o sandbox devolveu em 18/10 para buscas que funcionavam minutos depois.
TRANSIENT_CODES = {"TIMEOUT", "INTERNAL_ERROR", "SERVICE_UNAVAILABLE", "RATE_LIMITED", "UPSTREAM_ERROR", "TRANSPORT",
                   "AUTH_RETRY", "TOOL_ERROR"}


# O servidor de autorização do iFood anuncia `issuer: https://id.ifood.com.br/3p`, mas os
# metadados do recurso protegido apontam para `https://id.ifood.com.br`. O SDK segue a
# RFC 8414 e recusa. Aceitamos só o caso de mesmo host com caminho a mais, e só para o
# iFood; qualquer outra divergência continua sendo erro. Registrado em docs/mcp-feedback.md.
_strict_issuer_check = _oauth2.validate_metadata_issuer
TOLERATED_ISSUER_HOSTS = {"id.ifood.com.br"}


def _tolerant_issuer_check(oauth_metadata, expected_issuer: str) -> None:
    from urllib.parse import urlparse

    actual = str(oauth_metadata.issuer).rstrip("/")
    expected = expected_issuer.rstrip("/")
    a, e = urlparse(actual), urlparse(expected)
    if (a.scheme, a.netloc) == (e.scheme, e.netloc) and a.netloc in TOLERATED_ISSUER_HOSTS \
            and actual.startswith(expected):
        log.warning("issuer do OAuth diverge (%s != %s); aceito por ser o mesmo host do iFood", actual, expected)
        return
    _strict_issuer_check(oauth_metadata, expected_issuer)


_oauth2.validate_metadata_issuer = _tolerant_issuer_check


class McpToolError(Exception):
    """Erro de tool já normalizado, venha ele em `errors`, `error` ou na raiz."""

    def __init__(self, tool: str, code: str, message: str, *, retryable: bool = False,
                 user_message: str | None = None, raw: Any = None):
        super().__init__(f"{tool}: {code}: {message}")
        self.tool = tool
        self.code = code
        self.message = message
        self.retryable = retryable
        self.user_message = user_message or message
        self.raw = raw

    def to_dict(self) -> dict:
        return {"tool": self.tool, "code": self.code, "message": self.message,
                "retryable": self.retryable, "userMessage": self.user_message}


class NotAuthenticated(Exception):
    pass


def normalize_error(tool: str, body: Any) -> McpToolError | None:
    """Devolve o erro contido na resposta, ou None se a resposta é de sucesso."""
    if not isinstance(body, dict):
        return None
    if isinstance(body.get("errors"), dict):  # Offer Discovery e User
        e = body["errors"]
        return McpToolError(tool, e.get("code", "UNKNOWN"), e.get("message", ""),
                            retryable=bool(e.get("retryable")), user_message=e.get("suggestedAction"), raw=body)
    if isinstance(body.get("errors"), list) and body["errors"]:
        e = body["errors"][0]
        return McpToolError(tool, e.get("code", "UNKNOWN"), e.get("message", ""),
                            retryable=bool(e.get("retryable")), raw=body)
    if isinstance(body.get("error"), dict):  # Cart
        e = body["error"]
        return McpToolError(tool, e.get("code") or "UNKNOWN", e.get("message", ""),
                            retryable=e.get("code") in TRANSIENT_CODES,
                            user_message=e.get("userFriendlyMessage"), raw=body)
    if "code" in body and "message" in body and ("agent_instruction" in body or "errorType" not in body) \
            and not any(k in body for k in ("results", "cartId", "orderId", "status")):
        # falha de upstream do sandbox (18/10): {"code": "INTERNAL", "message": ..., "agent_instruction": ...}
        return McpToolError(tool, body["code"], body["message"], retryable=body["code"] in TRANSIENT_CODES,
                            user_message=body.get("agent_instruction") or body["message"], raw=body)
    if "errorType" in body and "code" in body:  # Order Tracking, campos na raiz
        return McpToolError(tool, body["code"], body.get("message", ""),
                            retryable=bool(body.get("retryable")), user_message=body.get("suggestion"), raw=body)
    return None


def unwrap(tool: str, body: Any) -> Any:
    """Tira o envelope `{"success", "data"}` que algumas tools usam e a doc não mostra (ex.: createAddress)."""
    if isinstance(body, dict) and "success" in body and "data" in body \
            and set(body) <= {"success", "data", "agentHints", "meta", "message"}:
        if body["success"] is False:
            message = body.get("message") or (body.get("data") or {}).get("message") or "falha sem mensagem"
            raise McpToolError(tool, "UNSUCCESSFUL", str(message), raw=body)
        data = body["data"]
        if isinstance(data, dict):
            hints = {**(data.get("agentHints") or {}), **(body.get("agentHints") or {})}
            return {**data, "agentHints": hints} if hints else data
        return data
    return body


def parse_result(tool: str, result: Any) -> Any:
    """CallToolResult para o JSON da resposta, levantando McpToolError se for erro."""
    body: Any = result.structured_content
    if body is None:
        texts = [c.text for c in result.content if getattr(c, "type", None) == "text"]
        joined = "".join(texts)
        try:
            body = json.loads(joined) if joined else {}
        except json.JSONDecodeError:
            body = {"text": joined}
    error = normalize_error(tool, body)
    if error:
        raise error
    if result.is_error:
        message = body.get("text", "") if isinstance(body, dict) else str(body)
        raise McpToolError(tool, "TOOL_ERROR", message or "erro sem mensagem", raw=body)
    return unwrap(tool, body)


class DbTokenStorage:
    """Guarda os tokens do OAuth no usuário dono do app."""

    def __init__(self, engine, user_id: int = 1):
        self.engine = engine
        self.user_id = user_id

    async def get_tokens(self) -> OAuthToken | None:
        from app.core.models import User

        with Session(self.engine) as s:
            user = s.get(User, self.user_id)
            data = (user.ifood_token or {}) if user else {}
        tokens = data.get("tokens")
        return OAuthToken.model_validate(tokens) if tokens else None

    async def set_tokens(self, tokens: OAuthToken) -> None:
        from app.core.models import User

        with Session(self.engine) as s:
            user = s.get(User, self.user_id)
            data = dict(user.ifood_token or {})
            previous = data.get("tokens") or {}
            data["tokens"] = tokens.model_dump(mode="json")
            # o servidor pode não rodar o refresh_token na renovação: mantém o anterior
            if not data["tokens"].get("refresh_token") and previous.get("refresh_token"):
                data["tokens"]["refresh_token"] = previous["refresh_token"]
            data["saved_at"] = time.time()
            data.pop("needs_login", None)
            user.ifood_token = data
            s.add(user)
            s.commit()

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        # o agente é pré-cadastrado no portal; não há registro dinâmico
        if not settings.client_id:
            return None
        return OAuthClientInformationFull(
            client_id=settings.client_id,
            client_secret=settings.client_secret or None,
            redirect_uris=[settings.redirect_uri],
            token_endpoint_auth_method="client_secret_post" if settings.client_secret else "none",
            grant_types=["authorization_code", "refresh_token"],
        )

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        return None

    def _data(self) -> dict:
        from app.core.models import User

        with Session(self.engine) as s:
            user = s.get(User, self.user_id)
            return dict(user.ifood_token or {}) if user else {}

    def has_tokens(self) -> bool:
        data = self._data()
        return bool(data.get("tokens")) and not data.get("needs_login")

    def expires_in(self) -> float | None:
        """Segundos até o access token vencer, calculados pela hora em que foi salvo."""
        data = self._data()
        tokens = data.get("tokens") or {}
        if not tokens.get("expires_in") or not data.get("saved_at"):
            return None
        return data["saved_at"] + float(tokens["expires_in"]) - time.time()

    def refresh_token(self) -> str | None:
        return (self._data().get("tokens") or {}).get("refresh_token")

    def mark_needs_login(self) -> None:
        from app.core.models import User

        with Session(self.engine) as s:
            user = s.get(User, self.user_id)
            if user and user.ifood_token:
                user.ifood_token = {**user.ifood_token, "needs_login": True}
                s.add(user)
                s.commit()

    def clear(self) -> None:
        from app.core.models import User

        with Session(self.engine) as s:
            user = s.get(User, self.user_id)
            user.ifood_token = None
            s.add(user)
            s.commit()


REFRESH_MARGIN_S = 300
_token_endpoint: str | None = None


async def _discover_token_endpoint() -> str:
    """Endpoint de token do servidor de autorização do MCP (https://id.ifood.com.br/token no sandbox)."""
    global _token_endpoint
    if _token_endpoint:
        return _token_endpoint
    from urllib.parse import urlparse

    url = urlparse(settings.mcp_url)
    async with httpx2.AsyncClient(timeout=15) as http:
        prm = (await http.get(f"{url.scheme}://{url.netloc}/.well-known/oauth-protected-resource{url.path}")).json()
        auth_server = (prm.get("authorization_servers") or ["https://id.ifood.com.br"])[0].rstrip("/")
        meta = (await http.get(f"{auth_server}/.well-known/openid-configuration")).json()
    _token_endpoint = meta.get("token_endpoint") or f"{auth_server}/token"
    return _token_endpoint


async def refresh_tokens(storage: "DbTokenStorage") -> None:
    """Renova o access token com o refresh_token salvo.

    O SDK não sabe quando um token carregado do banco vence (só guarda `expires_in`
    relativo) e, logo depois de reiniciar, ainda não descobriu o endpoint de token: ele
    mandaria o token vencido, levaria 401 e iria direto para o login completo. Por isso o
    back-end renova antes de abrir a sessão.
    """
    refresh = storage.refresh_token()
    if not refresh:
        storage.mark_needs_login()
        raise NotAuthenticated("o token do iFood expirou; faça login de novo em /auth/login")
    data = {"grant_type": "refresh_token", "refresh_token": refresh, "client_id": settings.client_id,
            "resource": settings.mcp_url}
    if settings.client_secret:
        data["client_secret"] = settings.client_secret
    try:
        async with httpx2.AsyncClient(timeout=20) as http:
            res = await http.post(await _discover_token_endpoint(), data=data)
    except Exception as exc:  # noqa: BLE001 - rede fora: tenta de novo na próxima chamada
        raise McpToolError("connect", "TRANSPORT", f"renovação do token falhou: {exc!r}", retryable=True) from exc
    if res.status_code != 200:
        log.warning("renovação do token recusada: %s %s", res.status_code, res.text[:200])
        storage.mark_needs_login()
        raise NotAuthenticated("o token do iFood expirou e não pôde ser renovado; faça login de novo em /auth/login")
    await storage.set_tokens(OAuthToken.model_validate_json(res.content))
    log.info("token do iFood renovado")


def _auth_error_in(exc: BaseException | None) -> bool:
    """Acha um NotAuthenticated mesmo dentro de ExceptionGroup (o transporte do SDK embrulha os erros)."""
    if exc is None:
        return False
    if isinstance(exc, NotAuthenticated):
        return True
    if isinstance(exc, BaseExceptionGroup):
        return any(_auth_error_in(e) for e in exc.exceptions)
    return _auth_error_in(exc.__cause__) if exc.__cause__ else False


class IfoodConnection:
    """Uma sessão MCP viva, aberta numa task dona dos context managers."""

    def __init__(self, storage: DbTokenStorage):
        self.storage = storage
        self._session: ClientSession | None = None
        self._task: asyncio.Task | None = None
        self._ready = asyncio.Event()
        self._stop = asyncio.Event()
        self._error: BaseException | None = None
        self._auth_url: asyncio.Future[str] | None = None
        self._code: asyncio.Future[AuthorizationCodeResult] | None = None
        self._interactive = False
        self._lock = asyncio.Lock()
        # a sessão caiu por autenticação: na próxima chamada, renova antes de reabrir. Só a recusa
        # da renovação marca "precisa logar"; um 401 passageiro não pode travar o app
        self._force_refresh = False

    # ---------------------------------------------------------------- OAuth

    async def _redirect_handler(self, url: str) -> None:
        if not self._interactive:
            # o token expirou e o refresh falhou; a tela precisa pedir login de novo
            raise NotAuthenticated("login no iFood necessário")
        if self._auth_url and not self._auth_url.done():
            self._auth_url.set_result(url)

    async def _callback_handler(self) -> AuthorizationCodeResult:
        assert self._code is not None
        return await self._code

    def deliver_code(self, code: str, state: str | None, iss: str | None = None) -> None:
        if self._code and not self._code.done():
            self._code.set_result(AuthorizationCodeResult(code=code, state=state, iss=iss))

    def _provider(self) -> OAuthClientProvider:
        return OAuthClientProvider(
            server_url=settings.mcp_url,
            client_metadata=OAuthClientMetadata(
                client_name="Gnomers",
                redirect_uris=[settings.redirect_uri],
                grant_types=["authorization_code", "refresh_token"],
                response_types=["code"],
                token_endpoint_auth_method="client_secret_post" if settings.client_secret else "none",
            ),
            storage=self.storage,
            redirect_handler=self._redirect_handler,
            callback_handler=self._callback_handler,
        )

    # ------------------------------------------------------------- conexão

    async def _runner(self) -> None:
        try:
            timeout = httpx2.Timeout(settings.mcp_timeout_s, read=max(settings.mcp_timeout_s, 60))
            async with httpx2.AsyncClient(auth=self._provider(), timeout=timeout) as http:
                async with streamable_http_client(settings.mcp_url, http_client=http) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        self._session = session
                        self._ready.set()
                        await self._stop.wait()
        except BaseException as exc:  # noqa: BLE001 - qualquer falha precisa chegar a quem espera
            self._error = exc
            if _auth_error_in(exc):
                log.warning("sessão MCP encerrada: login no iFood necessário")
            else:
                log.warning("sessão MCP encerrada: %r", exc)
            if self._auth_url and not self._auth_url.done():
                if self._interactive:
                    self._auth_url.set_exception(exc if isinstance(exc, Exception) else RuntimeError(str(exc)))
                else:
                    self._auth_url.cancel()
        finally:
            self._session = None
            self._ready.set()

    def _start(self, interactive: bool) -> None:
        self._interactive = interactive
        self._ready = asyncio.Event()
        self._stop = asyncio.Event()
        self._error = None
        loop = asyncio.get_running_loop()
        self._auth_url = loop.create_future()
        self._code = loop.create_future()
        self._task = asyncio.create_task(self._runner())

    @property
    def connected(self) -> bool:
        return self._session is not None

    async def login(self) -> str | None:
        """Inicia o OAuth. Devolve a URL de autorização, ou None se já está conectado."""
        async with self._lock:
            if self.connected:
                return None
            await self.close()
            self._start(interactive=True)
        assert self._auth_url is not None
        ready = asyncio.create_task(self._ready.wait())
        done, _ = await asyncio.wait({ready, self._auth_url}, return_when=asyncio.FIRST_COMPLETED, timeout=30)
        if self._auth_url in done:
            ready.cancel()
            return self._auth_url.result()
        if self.connected:
            return None
        raise RuntimeError(f"não foi possível iniciar o login: {self._error!r}")

    async def wait_connected(self, timeout: float = 300) -> bool:
        await asyncio.wait_for(self._ready.wait(), timeout)
        return self.connected

    async def ensure(self) -> ClientSession:
        async with self._lock:
            remaining = self.storage.expires_in()
            stale = (remaining is not None and remaining < REFRESH_MARGIN_S) or self._force_refresh
            if self._session is not None and not (stale and not self._interactive):
                return self._session
            starting = self._task is not None and not self._task.done() and self._session is None
            if not starting:
                if not self.storage.has_tokens():
                    raise NotAuthenticated("faça login no iFood em /auth/login")
                if stale:
                    # a sessão aberta usa o token vencido: renova e reabre
                    await self.close()
                    await refresh_tokens(self.storage)
                    self._force_refresh = False
                self._start(interactive=False)
            # captura o evento desta tentativa; outra chamada não pode trocá-lo no meio da espera
            ready = self._ready
        try:
            await asyncio.wait_for(ready.wait(), timeout=60)
        except asyncio.TimeoutError as exc:
            await self.close()
            raise McpToolError("connect", "TIMEOUT", "o MCP do iFood não respondeu ao conectar", retryable=True) from exc
        if self._session is None:
            if _auth_error_in(self._error) or _is_auth_failure(self._error):
                self._force_refresh = True
                raise McpToolError("connect", "AUTH_RETRY", "o iFood recusou o token; renovando", retryable=True)
            raise McpToolError("connect", "TRANSPORT", repr(self._error), retryable=True)
        return self._session

    async def close(self) -> None:
        if self._task and not self._task.done():
            self._stop.set()
            try:
                await asyncio.wait_for(self._task, 10)
            except (asyncio.TimeoutError, Exception):  # noqa: BLE001
                self._task.cancel()
        self._task = None
        self._session = None

    async def call(self, tool: str, args: dict) -> Any:
        session = await self.ensure()
        try:
            result = await asyncio.wait_for(
                session.call_tool(tool, args, read_timeout_seconds=settings.mcp_timeout_s),
                timeout=settings.mcp_timeout_s + 5,
            )
        except asyncio.TimeoutError as exc:
            raise McpToolError(tool, "TIMEOUT", "o MCP não respondeu a tempo", retryable=True) from exc
        except McpToolError:
            raise
        except Exception as exc:  # noqa: BLE001 - erro de transporte; a sessão é refeita na próxima
            await self.close()
            if _auth_error_in(exc) or _auth_error_in(self._error):
                self._force_refresh = True
                raise McpToolError(tool, "AUTH_RETRY", "o iFood recusou o token; renovando", retryable=True) from exc
            raise McpToolError(tool, "TRANSPORT", repr(exc), retryable=True) from exc
        return parse_result(tool, result)


def _is_auth_failure(exc: BaseException | None) -> bool:
    if exc is None:
        return False
    text = repr(exc)
    return bool(re.search(r"401|unauthori[sz]ed|invalid_grant|OAuth", text, re.I))
