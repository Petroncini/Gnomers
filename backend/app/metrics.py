"""Custo e tempo de cada carrinho: chamadas ao MCP, ao LLM e duração das etapas.

O runner abre um `Metrics` no contexto do plano; as chamadas feitas dentro dele (inclusive
nas tasks do asyncio, que herdam o contexto) se registram sozinhas. O resumo fica em
`plan.result["metrics"]` e em GET /plans/{id}/metrics.
"""

from __future__ import annotations

import time
from contextvars import ContextVar
from dataclasses import dataclass, field

# preço por milhão de tokens (entrada, saída) em USD, para quando o provedor não devolve o custo
PRICES = {
    "haiku": (0.10, 0.50),
    "sonnet": (2.0, 10.0),
    "opus": (4.0, 20.0),
}


@dataclass
class Metrics:
    started: float = field(default_factory=time.perf_counter)
    mcp: list[dict] = field(default_factory=list)
    llm: list[dict] = field(default_factory=list)
    fallbacks: list[str] = field(default_factory=list)
    stages: list[tuple[str, float]] = field(default_factory=list)

    def stage(self, name: str) -> None:
        self.stages.append((name, time.perf_counter() - self.started))

    def summary(self) -> dict:
        total = time.perf_counter() - self.started
        by_tool: dict[str, dict] = {}
        for c in self.mcp:
            t = by_tool.setdefault(c["tool"], {"calls": 0, "ms": 0, "max_ms": 0, "errors": 0})
            t["calls"] += 1
            t["ms"] += c["ms"]
            t["max_ms"] = max(t["max_ms"], c["ms"])
            t["errors"] += 0 if c["ok"] else 1
        by_task: dict[str, dict] = {}
        for c in self.llm:
            t = by_task.setdefault(c["task"], {"calls": 0, "ms": 0, "input_tokens": 0, "output_tokens": 0,
                                               "cost_usd": 0.0, "model": c["model"]})
            t["calls"] += 1
            t["ms"] += c["ms"]
            t["input_tokens"] += c["input_tokens"]
            t["output_tokens"] += c["output_tokens"]
            t["cost_usd"] += c["cost_usd"]
        # duração de cada etapa: do primeiro evento dela até o primeiro evento da seguinte
        firsts: list[tuple[str, float]] = []
        for name, t in self.stages:
            if not firsts or firsts[-1][0] != name:
                firsts.append((name, t))
        durations = {}
        for (name, t), nxt in zip(firsts, firsts[1:] + [("fim", total)]):
            durations[name] = round(durations.get(name, 0) + nxt[1] - t, 2)
        return {
            "total_s": round(total, 2),
            "stages_s": durations,
            "mcp": {"calls": len(self.mcp), "sum_ms": sum(c["ms"] for c in self.mcp),
                    "by_tool": dict(sorted(by_tool.items(), key=lambda kv: -kv[1]["ms"]))},
            "llm": {"calls": len(self.llm), "sum_ms": sum(c["ms"] for c in self.llm),
                    "input_tokens": sum(c["input_tokens"] for c in self.llm),
                    "output_tokens": sum(c["output_tokens"] for c in self.llm),
                    "cost_usd": round(sum(c["cost_usd"] for c in self.llm), 5),
                    "by_task": by_task, "fallbacks": self.fallbacks},
        }


current: ContextVar[Metrics | None] = ContextVar("gnomers_metrics", default=None)
current_task: ContextVar[str] = ContextVar("gnomers_llm_task", default="?")


def record_mcp(tool: str, ms: float, ok: bool, code: str | None = None) -> None:
    m = current.get()
    if m is not None:
        m.mcp.append({"tool": tool, "ms": round(ms), "ok": ok, "code": code})


def record_llm(model: str, ms: float, input_tokens: int, output_tokens: int, cost_usd: float | None) -> None:
    m = current.get()
    if m is None:
        return
    if cost_usd is None:
        tier = next((k for k in PRICES if k in model), "opus")
        price_in, price_out = PRICES[tier]
        cost_usd = (input_tokens * price_in + output_tokens * price_out) / 1_000_000
    m.llm.append({"task": current_task.get(), "model": model, "ms": round(ms), "input_tokens": input_tokens,
                  "output_tokens": output_tokens, "cost_usd": cost_usd})


def record_fallback(task: str) -> None:
    m = current.get()
    if m is not None:
        m.fallbacks.append(task)
