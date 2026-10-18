"""Modos live, record e replay das chamadas ao MCP.

Cada chamada fica em `fixtures/<tool>/<hash>.json`, onde o hash é tirado dos
argumentos em JSON canônico. As fixtures servem para testar sem rede, rodar a demo
se o sandbox cair e documentar o comportamento real das tools no feedback.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

from app.mcp.client import McpToolError, unwrap

Caller = Callable[[str, dict], Awaitable[Any]]


def fixture_key(tool: str, args: dict) -> str:
    canonical = json.dumps(args, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(f"{tool}|{canonical}".encode()).hexdigest()[:20]


class Recorder:
    def __init__(self, mode: str, directory: Path, caller: Caller | None):
        if mode not in ("live", "record", "replay"):
            raise ValueError(f"modo do recorder inválido: {mode}")
        self.mode = mode
        self.directory = directory
        self.caller = caller
        self.log: list[dict] = []  # chamadas desta sessão, para o painel de debug

    def _path(self, tool: str, args: dict) -> Path:
        return self.directory / tool / f"{fixture_key(tool, args)}.json"

    async def call(self, tool: str, args: dict) -> Any:
        started = time.perf_counter()
        entry: dict = {"tool": tool, "args": args, "mode": self.mode}
        try:
            if self.mode == "replay":
                result = self._replay(tool, args)
            else:
                assert self.caller is not None
                try:
                    result = await self.caller(tool, args)
                except McpToolError as exc:
                    if self.mode == "record":
                        self._save(tool, args, {"error": exc.to_dict(), "raw": exc.raw})
                    raise
                if self.mode == "record":
                    self._save(tool, args, {"response": result})
            entry["ok"] = True
            return result
        except McpToolError as exc:
            entry["ok"] = False
            entry["error"] = exc.to_dict()
            raise
        finally:
            entry["ms"] = round((time.perf_counter() - started) * 1000)
            self.log.append(entry)
            del self.log[:-500]

    def _save(self, tool: str, args: dict, payload: dict) -> None:
        path = self._path(tool, args)
        path.parent.mkdir(parents=True, exist_ok=True)
        body = {"tool": tool, "args": args, "recordedAt": time.strftime("%Y-%m-%dT%H:%M:%S"), **payload}
        path.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")

    def _replay(self, tool: str, args: dict) -> Any:
        path = self._path(tool, args)
        if not path.exists():
            raise McpToolError(tool, "FIXTURE_MISSING", f"sem gravação para {tool} com estes argumentos ({path.name})")
        data = json.loads(path.read_text(encoding="utf-8"))
        if "error" in data:
            e = data["error"]
            raise McpToolError(tool, e["code"], e["message"], retryable=e.get("retryable", False),
                               user_message=e.get("userMessage"), raw=data.get("raw"))
        return unwrap(tool, data["response"])
