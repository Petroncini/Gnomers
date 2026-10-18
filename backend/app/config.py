"""Leitura do .env da raiz do projeto, sem depender de python-dotenv."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"


def _load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[key.strip()] = value
    return values


_FILE = _load_env(ROOT / ".env")


def _get(key: str, default: str = "") -> str:
    return os.environ.get(key, _FILE.get(key, default))


@dataclass(frozen=True)
class Settings:
    mcp_url: str = field(default_factory=lambda: _get("IFOOD_MCP_URL", "https://consumer-mcp.ifood.com.br/marketplace"))
    client_id: str = field(default_factory=lambda: _get("IFOOD_CLIENT_ID"))
    client_secret: str = field(default_factory=lambda: _get("IFOOD_CLIENT_SECRET"))
    callback_port: int = field(default_factory=lambda: int(_get("IFOOD_CALLBACK_PORT", "8080")))
    # live: chama o MCP; record: chama e grava em fixtures/; replay: só lê fixtures/;
    # fake: simulador local com catálogo gravado (testes e demo sem rede)
    mcp_mode: str = field(default_factory=lambda: _get("GNOMERS_MCP_MODE", "live"))
    fixtures_dir: Path = field(default_factory=lambda: Path(_get("GNOMERS_FIXTURES", str(BACKEND / "fixtures"))))
    database_url: str = field(default_factory=lambda: _get("GNOMERS_DB", f"sqlite:///{BACKEND / 'gnomers.db'}"))
    anthropic_api_key: str = field(default_factory=lambda: _get("ANTHROPIC_API_KEY"))
    openrouter_api_key: str = field(default_factory=lambda: _get("OPENROUTER_API_KEY"))
    # "openrouter", "anthropic" ou vazio (escolhe pela chave presente, OpenRouter primeiro)
    llm_provider: str = field(default_factory=lambda: _get("GNOMERS_LLM_PROVIDER"))
    # tarefas em lote usam o modelo rápido; extração de regra e explicação usam o forte.
    # Vazio = padrão do provedor (ver app/llm.py)
    llm_fast_model: str = field(default_factory=lambda: _get("GNOMERS_LLM_FAST"))
    llm_strong_model: str = field(default_factory=lambda: _get("GNOMERS_LLM_STRONG"))
    llm_enabled: bool = field(default_factory=lambda: _get("GNOMERS_LLM", "auto") != "off")
    mcp_concurrency: int = field(default_factory=lambda: int(_get("GNOMERS_MCP_CONCURRENCY", "6")))
    # o sandbox bloqueia o token (com erro INTERNAL) depois de ~60 chamadas em 1-2 min
    mcp_calls_per_min: int = field(default_factory=lambda: int(_get("GNOMERS_MCP_CALLS_PER_MIN", "30")))
    mcp_timeout_s: float = field(default_factory=lambda: float(_get("GNOMERS_MCP_TIMEOUT", "30")))

    @property
    def redirect_uri(self) -> str:
        return f"http://localhost:{self.callback_port}/callback"


settings = Settings()
