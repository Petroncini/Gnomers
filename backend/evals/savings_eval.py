"""Quanto o Cesta economiza contra o que uma pessoa faria, sobre preços reais gravados do iFood.

    uv run python evals/savings_eval.py

Usa as buscas gravadas em backend/fixtures/searchOffers (São Carlos, 17 e 18/10) e o mesmo código
do app (juiz, filtros e solver), sem chamar o iFood. Listas: todas as combinações de 3 a 6 dos
itens abaixo. Os totais são estimados (produtos + frete), iguais para todas as estratégias: cupom e
taxa só existem no carrinho real.

As pessoas de referência já sabem o que serve (usam os mesmos produtos aprovados pelo juiz, exatos
antes de substitutos) e escolhem o mais barato. Se a compra num mercado não chega ao pedido mínimo,
a pessoa completa até ele (paga o mínimo). A diferença medida é só a da escolha de mercados.
"""

from __future__ import annotations

import asyncio
import glob
import itertools
import json
import os
import statistics
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

# banco temporário: o juiz e a normalização guardam cache, e o banco do app fica intocado
os.environ["GNOMERS_DB"] = f"sqlite:///{tempfile.mkdtemp(prefix='gnomers-eval-')}/eval.db"
os.environ.setdefault("GNOMERS_MCP_MODE", "fake")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlmodel import Session  # noqa: E402

from app.ai import llm  # noqa: E402
from app.core import metrics  # noqa: E402
from app.core.db import engine, init_db  # noqa: E402
from app.pipeline import discovery, normalize, optimize  # noqa: E402
from app.preferences.rules import Resolved  # noqa: E402

HERE = Path(__file__).parent
FIXTURES = HERE.parent / "fixtures" / "searchOffers"
MAX_MERCHANTS = 2
SPLIT_MIN = 10.0     # padrão do app: dividir só se economizar mais de R$ 10

# (pedido, categoria, buscas gravadas, embalagem, unidade, quantidade, exigências)
ITEMS = [
    ("1 kilo de arroz branco", "arroz", ["arroz branco", "arroz"], 1000, "g", 1, []),
    ("1 kilo de feijão", "feijao", ["feijão", "feijão cru"], 1000, "g", 1, []),
    ("500g café moído", "cafe", ["café moído", "café", "café torrado e moído"], 500, "g", 1, []),
    ("1 granola sem açúcar 500g", "granola", ["granola sem açúcar", "granola", "granola zero açúcar"], 500, "g", 1,
     ["sem açúcar"]),
    ("2 leite integral 1L", "leite", ["leite integral", "leite italac integral", "leite italac"], 1000, "ml", 2,
     ["integral"]),
    ("1 leite em pó integral 400g", "leite em po", ["leite em pó", "leite ninho"], 400, "g", 1, ["integral"]),
]


def load_offers() -> tuple[list[discovery.Offer], dict[str, discovery.MerchantInfo]]:
    by_query: dict[str, list[dict]] = defaultdict(list)
    for f in sorted(glob.glob(str(FIXTURES / "*.json"))):
        d = json.loads(Path(f).read_text())
        by_query[d["args"].get("query")].append(d.get("response") or {})
    offers: list[discovery.Offer] = []
    merchants: dict[str, discovery.MerchantInfo] = {}
    for item_id, (_, _, queries, *_rest) in enumerate(ITEMS, start=1):
        seen = set()
        for q in queries:
            for body in by_query.get(q, []):
                found, ms = discovery._offers_from(body, item_id, q)
                for o in found:
                    if o.sku_id not in seen:
                        seen.add(o.sku_id)
                        offers.append(o)
                for mid, info in ms.items():
                    # mercado fechado vem com o frete zerado; vale o primeiro registro com método de entrega
                    if mid not in merchants or (merchants[mid].delivery_fee == 0 and info.delivery_fee > 0):
                        merchants[mid] = info
    return offers, merchants


def basket(cands: list[optimize.Candidate], verdicts: dict, item: optimize.Item) -> optimize.Candidate | None:
    """O que a pessoa leva de um item: exato antes de substituto, depois o menor custo para a quantidade."""
    if not cands:
        return None
    return min(cands, key=lambda c: (verdicts.get((c.item_id, c.sku_id)) != "exato",
                                     c.price * optimize.packs_for(item.required, c.size)))


def person(items, cands, merchants, verdicts, choose_merchant) -> dict | None:
    """Uma pessoa que compra tudo num mercado só, escolhido por `choose_merchant`."""
    by_m = defaultdict(lambda: defaultdict(list))
    for c in cands:
        by_m[c.merchant_id][c.item_id].append(c)
    mid = choose_merchant(by_m)
    if mid is None:
        return None
    picks = {i.id: basket(by_m[mid][i.id], verdicts, i) for i in items}
    if any(p is None for p in picks.values()):
        return None  # o mercado não tem todos os itens
    cost = sum(p.price * optimize.packs_for(next(i.required for i in items if i.id == k), p.size)
               for k, p in picks.items())
    minimum = merchants[mid].minimum_order
    return {"total": round(max(cost, minimum) + merchants[mid].delivery_fee, 2), "merchants": 1,
            "topped_up": cost < minimum}


def cheapest_each(items, cands, merchants, verdicts) -> dict | None:
    """Uma pessoa que leva cada item onde ele é mais barato, sem pensar no frete."""
    by_item = defaultdict(list)
    for c in cands:
        by_item[c.item_id].append(c)
    picks = [(i, basket(by_item[i.id], verdicts, i)) for i in items]
    if any(p is None for _, p in picks):
        return None
    spend = defaultdict(float)
    for i, p in picks:
        spend[p.merchant_id] += p.price * optimize.packs_for(i.required, p.size)
    paid = {m: max(v, merchants[m].minimum_order) for m, v in spend.items()}
    return {"total": round(sum(paid.values()) + sum(merchants[m].delivery_fee for m in spend), 2),
            "merchants": len(spend), "topped_up": any(paid[m] > spend[m] for m in spend)}


async def main() -> None:
    init_db()
    offers, merchants = load_offers()
    specs = [normalize.ItemSpec(id=k, raw_text=req, query=queries[0], category=cat, brand=None,
                                variety_terms=list(must), pack_size=size, pack_unit=unit, quantity=qty,
                                rules=Resolved(), request=req)
             for k, (req, cat, queries, size, unit, qty, must) in enumerate(ITEMS, start=1)]
    m = metrics.Metrics()
    metrics.current.set(m)
    with Session(engine) as s:
        norm_set = await normalize.build(s, specs, offers)
    verdicts = {(info.item_id, info.sku_id): info.verdict for info in norm_set.skus.values()}

    rows = []
    for size in range(3, len(ITEMS) + 1):
        for combo in itertools.combinations(range(1, len(ITEMS) + 1), size):
            items = [i for i in norm_set.items if i.id in combo]
            cands = [c for c in norm_set.candidates if c.item_id in combo]
            problem = optimize.Problem(items=items, candidates=cands, max_merchants=MAX_MERCHANTS,
                                       merchants=[optimize.Merchant(x.id, x.name, x.delivery_fee, x.minimum_order)
                                                  for x in merchants.values()])
            sol = optimize.solve_with_alternatives(problem, SPLIT_MIN)[0]
            cesta = None if sol.missing else {"total": round(sol.products_cost + sol.delivery_cost, 2),
                                              "merchants": len(sol.merchants)}
            nearest = person(items, cands, merchants, verdicts, lambda by_m: min(
                (mid for mid in by_m if len(by_m[mid]) == len(items)),
                key=lambda mid: merchants[mid].distance_km or 99, default=None))
            biggest = person(items, cands, merchants, verdicts, lambda by_m: min(
                by_m, key=lambda mid: (-len(by_m[mid]), merchants[mid].delivery_fee), default=None))
            rows.append({"list": [ITEMS[k - 1][0] for k in combo], "cesta": cesta, "nearest": nearest,
                         "biggest": biggest, "cheapest_each": cheapest_each(items, cands, merchants, verdicts)})

    def compare(key: str) -> dict:
        both = [r for r in rows if r["cesta"] and r[key]]
        saved = [r[key]["total"] - r["cesta"]["total"] for r in both]
        pct = [s / r[key]["total"] for s, r in zip(saved, both)]
        return {"lists": len(both), "mean_brl": round(statistics.mean(saved), 2) if saved else None,
                "mean_pct": round(statistics.mean(pct), 3) if pct else None,
                "max_brl": round(max(saved), 2) if saved else None,
                "cesta_cheaper_or_equal": sum(1 for x in saved if x >= -0.005),
                "person_topped_up": sum(1 for r in both if r[key].get("topped_up")),
                "person_mean_merchants": round(statistics.mean(r[key]["merchants"] for r in both), 2) if both else None,
                "no_store_with_everything": sum(1 for r in rows if r["cesta"] and not r[key])}

    report = {
        "lists": len(rows), "offers": len(offers), "merchants": len(merchants),
        "candidates": len(norm_set.candidates), "cesta_feasible": sum(1 for r in rows if r["cesta"]),
        "cesta_split": sum(1 for r in rows if r["cesta"] and r["cesta"]["merchants"] > 1),
        "vs_nearest": compare("nearest"), "vs_biggest": compare("biggest"), "vs_cheapest_each": compare("cheapest_each"),
        "llm": {k: v for k, v in m.summary()["llm"].items() if k in ("calls", "cost_usd", "fallbacks")},
        "llm_enabled": llm.enabled(), "rows": rows,
    }
    (HERE / "results").mkdir(exist_ok=True)
    (HERE / "results" / "savings.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))
    print(f"{report['lists']} listas, {report['offers']} ofertas, {report['merchants']} mercados, "
          f"Cesta resolveu {report['cesta_feasible']} (dividiu {report['cesta_split']})")
    for key, label in (("vs_nearest", "mercado mais perto"), ("vs_biggest", "mercado com mais itens"),
                       ("vs_cheapest_each", "mais barato de cada item")):
        r = report[key]
        print(f"vs {label:26} {r['lists']:2} listas | economia média R$ {r['mean_brl']} ({r['mean_pct']:.1%}) | "
              f"máx R$ {r['max_brl']} | Cesta ≤ pessoa em {r['cesta_cheaper_or_equal']} | "
              f"completou o mínimo em {r['person_topped_up']} | sem mercado com tudo: {r['no_store_with_everything']}")
    print("LLM:", report["llm"])


if __name__ == "__main__":
    asyncio.run(main())
