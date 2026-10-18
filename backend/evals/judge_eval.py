"""Acerto do juiz (LLM) contra as regras antigas, em pares (pedido, produto) marcados à mão.

    uv run python evals/judge_eval.py            # LLM do .env + regras
    uv run python evals/judge_eval.py --rules    # só as regras, sem gastar LLM

Os produtos são nomes reais que o iFood devolveu (backend/fixtures/searchOffers). Erro grave é o
que muda o carrinho: um produto que não serve aceito como exato ou substituto, ou um exato descartado.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ai import llm  # noqa: E402
from app.core import metrics  # noqa: E402

HERE = Path(__file__).parent
CLASSES = ("exato", "substituto", "nao_serve")


def score(cases: list[dict], predicted: list[str]) -> dict:
    confusion = Counter((c["expected"], p) for c, p in zip(cases, predicted))
    hits = sum(n for (e, p), n in confusion.items() if e == p)
    accepted_bad = sum(n for (e, p), n in confusion.items() if e == "nao_serve" and p != "nao_serve")
    lost_exact = sum(n for (e, p), n in confusion.items() if e == "exato" and p == "nao_serve")
    return {"accuracy": round(hits / len(cases), 3), "hits": hits, "total": len(cases),
            "accepted_bad": accepted_bad, "lost_exact": lost_exact,
            "confusion": {f"{e}->{p}": n for (e, p), n in sorted(confusion.items())}}


async def run_llm(cases: list[dict]) -> tuple[list[str], dict]:
    groups: dict[tuple, list[int]] = defaultdict(list)
    for i, c in enumerate(cases):
        groups[(c["request"], c["category"], tuple(c["must_have"]))].append(i)
    m = metrics.Metrics()
    metrics.current.set(m)
    out: list[str] = [""] * len(cases)

    async def judge(key, idx):
        request, category, must = key
        verdicts = await llm.judge_products(request, category, list(must), [cases[i]["product"] for i in idx])
        for i, v in zip(idx, verdicts):
            out[i] = v["verdict"]

    started = time.perf_counter()
    await asyncio.gather(*(judge(k, idx) for k, idx in groups.items()))
    summary = m.summary()["llm"]
    return out, {"seconds": round(time.perf_counter() - started, 1), "calls": summary["calls"],
                 "cost_usd": summary["cost_usd"], "fallbacks": summary["fallbacks"]}


def run_rules(cases: list[dict]) -> list[str]:
    return [llm._heuristic_verdict(c["request"], c["category"], c["must_have"], c["product"])["verdict"]
            for c in cases]


async def main() -> None:
    data = json.loads((HERE / "judge_cases.json").read_text())
    cases = data["cases"]
    report: dict = {"cases": len(cases), "by_class": Counter(c["expected"] for c in cases)}
    rules = run_rules(cases)
    report["rules"] = score(cases, rules)
    if "--rules" not in sys.argv:
        if not llm.enabled():
            sys.exit("sem LLM configurado; use --rules")
        predicted, cost = await run_llm(cases)
        report["llm"] = {**score(cases, predicted), **cost, "model": llm.model_for(False)}
        report["llm_errors"] = [{"request": c["request"], "product": c["product"], "expected": c["expected"],
                                 "got": p} for c, p in zip(cases, predicted) if p != c["expected"]]
    report["rules_errors"] = [{"request": c["request"], "product": c["product"], "expected": c["expected"],
                               "got": p} for c, p in zip(cases, rules) if p != c["expected"]]
    (HERE / "results").mkdir(exist_ok=True)
    (HERE / "results" / "judge.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))
    for who in ("llm", "rules"):
        if who in report:
            r = report[who]
            print(f"{who:6} acerto {r['accuracy']:.1%} ({r['hits']}/{r['total']}) | aceitou o que não serve: "
                  f"{r['accepted_bad']} | descartou exato: {r['lost_exact']}")
    if "llm" in report:
        print(f"LLM: {report['llm']['calls']} chamadas, {report['llm']['seconds']} s, US$ {report['llm']['cost_usd']}")


if __name__ == "__main__":
    asyncio.run(main())
