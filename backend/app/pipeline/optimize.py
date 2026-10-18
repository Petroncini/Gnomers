"""Modelo de programação linear inteira da compra (seção 7.4 do plano).

Módulo puro: entra uma descrição do problema em dataclasses e sai a solução, sem
chamar MCP, banco ou LLM. Os valores monetários estão em reais; as penalidades
também, para que o objetivo some coisas comparáveis.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import pulp

N_MAX = 30
OVERBUY = 1.3


@dataclass(frozen=True)
class Merchant:
    id: str
    name: str
    delivery_fee: float
    minimum_order: float


@dataclass(frozen=True)
class Candidate:
    sku_id: str          # itemId do iFood (é por mercado)
    item_id: int         # ListItem.id
    merchant_id: str
    name: str
    brand: str | None
    price: float         # preço efetivo por embalagem
    size: float          # quanto uma embalagem cobre, na unidade do item
    quality_penalty: float = 0.0       # já em reais, antes do α
    substitution_penalty: float = 0.0  # marca ou variedade fora do pedido
    note_penalty: float = 0.0


@dataclass(frozen=True)
class Item:
    id: int
    required: float      # quantidade a cobrir, na unidade do item
    missing_penalty: float


@dataclass
class Problem:
    items: list[Item]
    merchants: list[Merchant]
    candidates: list[Candidate]
    alpha: float = 0.5   # peso da qualidade, vem do quality_profile
    max_merchants: int = 2


@dataclass
class Pick:
    item_id: int
    sku_id: str
    merchant_id: str
    packs: int
    price: float

    @property
    def cost(self) -> float:
        return round(self.price * self.packs, 2)


@dataclass
class Solution:
    merchants: list[str]
    picks: list[Pick]
    missing: list[int]
    products_cost: float
    delivery_cost: float
    objective: float
    status: str = "Optimal"
    extra: dict = field(default_factory=dict)

    @property
    def estimated_total(self) -> float:
        return round(self.products_cost + self.delivery_cost, 2)


def _solver():
    available = pulp.listSolvers(onlyAvailable=True)
    if "HiGHS" in available:
        return pulp.HiGHS(msg=False, timeLimit=20)
    if "PULP_CBC_CMD" in available:
        return pulp.PULP_CBC_CMD(msg=False, timeLimit=20)
    raise RuntimeError(f"nenhum solver MILP disponível (encontrados: {available})")


def solve(problem: Problem, *, max_merchants: int | None = None,
          exclude_sets: list[frozenset[str]] | None = None) -> Solution | None:
    """Resolve o MILP. `exclude_sets` proíbe repetir conjuntos de mercados já usados."""
    k = problem.max_merchants if max_merchants is None else max_merchants
    merchants = {m.id: m for m in problem.merchants}
    cands = [c for c in problem.candidates if c.merchant_id in merchants and c.size > 0]
    items = {i.id: i for i in problem.items}
    by_item: dict[int, list[int]] = {i: [] for i in items}
    for idx, c in enumerate(cands):
        if c.item_id in by_item:
            by_item[c.item_id].append(idx)

    lp = pulp.LpProblem("compra", pulp.LpMinimize)
    x = {s: pulp.LpVariable(f"x_{s}", cat="Binary") for s in range(len(cands))}
    n = {s: pulp.LpVariable(f"n_{s}", lowBound=0, upBound=N_MAX, cat="Integer") for s in range(len(cands))}
    y = {m: pulp.LpVariable(f"y_{j}", cat="Binary") for j, m in enumerate(merchants)}
    u = {i: pulp.LpVariable(f"u_{i}", cat="Binary") for i in items}

    lp += (
        pulp.lpSum(cands[s].price * n[s] for s in x)
        + pulp.lpSum(merchants[m].delivery_fee * y[m] for m in y)
        + pulp.lpSum((problem.alpha * cands[s].quality_penalty + cands[s].substitution_penalty
                      + cands[s].note_penalty) * x[s] for s in x)
        + pulp.lpSum(items[i].missing_penalty * u[i] for i in u)
    )

    for i, idxs in by_item.items():
        lp += pulp.lpSum(x[s] for s in idxs) + u[i] == 1, f"cobre_{i}"
    for s, c in enumerate(cands):
        req = items[c.item_id].required
        lp += c.size * n[s] >= req * x[s], f"qtd_{s}"
        lp += c.size * n[s] <= OVERBUY * req + c.size, f"exagero_{s}"
        lp += n[s] <= N_MAX * x[s], f"liga_n_{s}"
        lp += x[s] <= y[c.merchant_id], f"liga_y_{s}"
    for j, (m_id, m) in enumerate(merchants.items()):
        in_m = [s for s, c in enumerate(cands) if c.merchant_id == m_id]
        lp += pulp.lpSum(cands[s].price * n[s] for s in in_m) >= m.minimum_order * y[m_id], f"minimo_{j}"
        # mercado sem nenhum produto escolhido não é usado (evita pagar frete à toa)
        lp += y[m_id] <= pulp.lpSum(x[s] for s in in_m), f"usado_{j}"
    lp += pulp.lpSum(y.values()) <= k, "max_mercados"
    for t, used in enumerate(exclude_sets or []):
        others = [m for m in merchants if m not in used]
        lp += (pulp.lpSum(y[m] for m in used if m in y) - pulp.lpSum(y[m] for m in others)
               <= len(used) - 1), f"corte_{t}"

    lp.solve(_solver())
    status = pulp.LpStatus[lp.status]
    if status not in ("Optimal", "Feasible") and not (status == "Not Solved" and lp.sol_status > 0):
        return None

    picks: list[Pick] = []
    for s, c in enumerate(cands):
        if (x[s].value() or 0) > 0.5:
            packs = int(round(n[s].value() or 0))
            picks.append(Pick(c.item_id, c.sku_id, c.merchant_id, max(packs, 1), c.price))
    used = sorted({p.merchant_id for p in picks})
    missing = [i for i in items if (u[i].value() or 0) > 0.5]
    products = round(sum(p.cost for p in picks), 2)
    delivery = round(sum(merchants[m].delivery_fee for m in used), 2)
    return Solution(merchants=used, picks=picks, missing=missing, products_cost=products, delivery_cost=delivery,
                    objective=round(pulp.value(lp.objective) or 0, 2), status=status)


def solve_with_alternatives(problem: Problem, split_min_savings: float | None, n_alternatives: int = 3) -> list[Solution]:
    """Decide entre um mercado e vários e gera até três soluções com conjuntos de mercados distintos.

    A versão com vários mercados só vale se economizar mais que `split_min_savings`
    no total estimado (produtos + frete) em relação à melhor com um mercado só.
    """
    single = solve(problem, max_merchants=1)
    multi = solve(problem) if problem.max_merchants > 1 else None
    threshold = split_min_savings if split_min_savings is not None else 0.0
    best, limit = single, 1
    if multi and (single is None or _worse(single, multi, threshold)):
        best, limit = multi, problem.max_merchants
    if best is None:
        return []
    if single:
        best.extra["single_estimated_total"] = single.estimated_total
        best.extra["single_merchants"] = single.merchants
    solutions = [best]
    excluded = [frozenset(best.merchants)]
    while len(solutions) < n_alternatives:
        alt = solve(problem, max_merchants=limit, exclude_sets=excluded)
        if alt is None or not alt.merchants:
            break
        solutions.append(alt)
        excluded.append(frozenset(alt.merchants))
    return solutions


def _worse(single: Solution, multi: Solution, threshold: float) -> bool:
    if len(multi.missing) < len(single.missing):
        return True
    if len(multi.missing) > len(single.missing):
        return False
    return single.objective - multi.objective > threshold


def packs_for(required: float, size: float) -> int:
    return max(1, math.ceil(required / size - 1e-9))
