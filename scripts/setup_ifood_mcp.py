#!/usr/bin/env python3
"""Conecta o Claude Code ao MCP do iFood usando as chaves do .env.

Uso, a partir da raiz do projeto:
    python3 scripts/setup_ifood_mcp.py

Lê IFOOD_MCP_URL, IFOOD_CLIENT_ID, IFOOD_CLIENT_SECRET e IFOOD_CALLBACK_PORT
do arquivo .env (ou do ambiente), registra o servidor "ifood" no escopo local
do Claude Code e abre o login OAuth no navegador. O secret é repassado ao
Claude Code por variável de ambiente e guardado no keychain do sistema.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVER_NAME = "ifood"
REQUIRED = ("IFOOD_MCP_URL", "IFOOD_CLIENT_ID", "IFOOD_CLIENT_SECRET")


def load_env(path: Path) -> dict[str, str]:
    """Lê um .env simples (CHAVE=valor), sem depender de python-dotenv."""
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


def find_claude() -> str | None:
    """Acha o binário do Claude Code, mesmo que ~/.local/bin não esteja no PATH."""
    found = shutil.which("claude")
    if found:
        return found
    for candidate in (Path.home() / ".local/bin/claude", Path("/opt/homebrew/bin/claude"), Path("/usr/local/bin/claude")):
        if candidate.exists():
            return str(candidate)
    return None


def run(cmd: list[str], env: dict[str, str] | None = None, check: bool = True) -> int:
    print("$", " ".join(cmd))
    result = subprocess.run(cmd, cwd=ROOT, env=env)
    if check and result.returncode != 0:
        sys.exit(f"Falhou: {' '.join(cmd)} (código {result.returncode})")
    return result.returncode


def main() -> None:
    config = {**load_env(ROOT / ".env"), **{k: v for k, v in os.environ.items() if k.startswith("IFOOD_")}}

    missing = [key for key in REQUIRED if not config.get(key)]
    if missing:
        sys.exit(f"Faltando no .env: {', '.join(missing)}. Use .env.example como modelo.")

    claude = find_claude()
    if claude is None:
        sys.exit(
            "Claude Code não encontrado. Instale com:\n"
            "    curl -fsSL https://claude.ai/install.sh | bash\n"
            "Depois abra um terminal novo, rode `claude` uma vez para fazer login e rode este script de novo."
        )

    port = int(config.get("IFOOD_CALLBACK_PORT") or 8080)
    print(f"Redirect URL que precisa estar cadastrada no agente: http://localhost:{port}/callback\n")

    server_config = {
        "type": "http",
        "url": config["IFOOD_MCP_URL"],
        "oauth": {"clientId": config["IFOOD_CLIENT_ID"], "callbackPort": port},
    }

    # Remove uma configuração anterior para poder trocar chaves sem conflito.
    subprocess.run(
        [claude, "mcp", "remove", SERVER_NAME, "--scope", "local"],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    env = {**os.environ, "MCP_CLIENT_SECRET": config["IFOOD_CLIENT_SECRET"]}
    run(
        [claude, "mcp", "add-json", SERVER_NAME, json.dumps(server_config), "--client-secret", "--scope", "local"],
        env=env,
    )

    print("\nAbrindo o login do iFood. Use o e-mail de um usuário de teste do portal")
    print("e os códigos gerados em 'Usuários de teste' (o de SMS pode ser pedido depois).\n")
    run([claude, "mcp", "login", SERVER_NAME])

    print()
    run([claude, "mcp", "get", SERVER_NAME], check=False)


if __name__ == "__main__":
    main()
