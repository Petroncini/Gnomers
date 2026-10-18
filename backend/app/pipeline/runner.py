"""Pipeline "Criar carrinho" de ponta a ponta, com progresso por SSE (seção 7)."""

from __future__ import annotations

import asyncio
import logging
import time
import traceback
from dataclasses import dataclass, field

from sqlmodel import Session, select

from app import learning
from app.db import engine
from app.mcp.client import McpToolError, NotAuthenticated
from app.mcp.ifood import Ifood, ifood
from app.models import Address, ListItem, Plan, Rule, ShoppingList, User
from app.pipeline import address as address_mod
from app.pipeline import decisions, discovery, materialize, normalize, optimize
from app.rules import norm, resolve

log = logging.getLogger(__name__)


@dataclass
class PlanEvents:
    events: list[dict] = field(default_factory=list)
    done: bool = False
    cond: asyncio.Condition = field(default_factory=asyncio.Condition)


_events: dict[int, PlanEvents] = {}


def events_for(plan_id: int) -> PlanEvents:
    return _events.setdefault(plan_id, PlanEvents())


async def emit(plan_id: int, stage: str, data: dict | None = None, *, final: bool = False) -> None:
    pe = events_for(plan_id)
    async with pe.cond:
        pe.events.append({"stage": stage, "t": round(time.time(), 2), **(data or {})})
        if final:
            pe.done = True
        pe.cond.notify_all()


def effective(user: User, lst: ShoppingList) -> tuple[float, int, float | None]:
    q = lst.quality_profile if lst.quality_profile is not None else user.quality_profile
    k = lst.max_merchants if lst.max_merchants is not None else user.max_merchants
    split = lst.split_min_savings if lst.split_min_savings is not None else user.split_min_savings
    return q, max(1, k), split


async def resolve_address(s: Session, api: Ifood, plan: Plan, user: User) -> Address | None:
    if plan.address_id:
        return s.get(Address, plan.address_id)
    addresses = await address_mod.sync_addresses(s, api, user.id)
    if plan.lat is None or plan.lng is None:
        last = next((a for a in addresses if a.is_last_used), None)
        return last or (addresses[0] if len(addresses) == 1 else None)
    chosen, _ = address_mod.pick_address(addresses, plan.lat, plan.lng)
    return chosen


async def run_plan(plan_id: int, api: Ifood | None = None, *, only_open: bool | None = None) -> None:
    api = api or ifood()
    if only_open is None:
        # precisa ser determinístico: o replay só acha a gravação com os mesmos argumentos.
        # Entrega agendada aceita mercado fechado agora (o iFood agenda o horário).
        with Session(engine) as s0:
            scheduled = bool(s0.get(Plan, plan_id).deliver_at)
        only_open = _env_only_open() and not scheduled

    async def say(stage: str, data: dict) -> None:
        await emit(plan_id, stage, data)

    with Session(engine) as s:
        plan = s.get(Plan, plan_id)
        try:
            plan.status = "running"
            s.add(plan)
            s.commit()
            await _run(s, api, plan, say, only_open)
            await emit(plan_id, "done", {"message": "Carrinho pronto", "status": plan.status}, final=True)
        except NotAuthenticated as exc:
            _fail(s, plan, "auth", str(exc))
            await emit(plan_id, "error", {"message": str(exc), "code": "AUTH"}, final=True)
        except McpToolError as exc:
            _fail(s, plan, exc.code, exc.user_message or exc.message)
            await emit(plan_id, "error", {"message": exc.user_message or exc.message, "code": exc.code}, final=True)
        except Exception as exc:  # noqa: BLE001 - a tela precisa saber que falhou
            log.error("pipeline falhou: %s", traceback.format_exc())
            _fail(s, plan, "internal", f"{type(exc).__name__}: {exc}")
            await emit(plan_id, "error", {"message": str(exc), "code": "INTERNAL"}, final=True)


def _env_only_open() -> bool:
    import os

    return os.environ.get("GNOMERS_ONLY_OPEN", "1") != "0"


def _fail(s: Session, plan: Plan, code: str, message: str) -> None:
    plan.status = "failed"
    plan.error = message
    plan.result = {**(plan.result or {}), "error": {"code": code, "message": message}}
    s.add(plan)
    s.commit()


async def _run(s: Session, api: Ifood, plan: Plan, say, only_open: bool) -> None:
    user = s.get(User, plan.user_id)
    lst = s.get(ShoppingList, plan.list_id)
    items = s.exec(select(ListItem).where(ListItem.list_id == lst.id)).all()
    if not items:
        raise ValueError("a lista está vazia")
    rules = s.exec(select(Rule).where(Rule.user_id == user.id, Rule.active == True)).all()  # noqa: E712

    # 7.1 endereço
    await say("address", {"message": "Conferindo seus endereços no iFood"})
    addr = await resolve_address(s, api, plan, user)
    if addr is None:
        plan.status = "needs_address"
        plan.result = {"needs_address": True}
        s.add(plan)
        s.commit()
        await say("address", {"message": "Nenhum endereço salvo perto de você", "needs_address": True})
        return
    plan.address_id = addr.id
    lat, lng = addr.lat, addr.lng
    await say("address", {"message": f"Entregando em {addr.alias or addr.formatted}", "address": addr.alias or addr.formatted})

    specs: list[normalize.ItemSpec] = []
    queries: list[discovery.ItemQuery] = []
    for it in items:
        resolved = resolve(rules, it.category, lst.id)
        query = it.query or it.raw_text
        # variedade obrigatória vai para a query; a suave vira penalidade no solver
        if resolved.variety_hard:
            missing = [t for t in resolved.variety_terms if norm(t) not in norm(query)]
            query = " ".join([query] + missing)
        specs.append(normalize.ItemSpec(id=it.id, raw_text=it.raw_text, query=query, category=it.category or norm(query),
                                        brand=it.brand, variety_terms=list(it.variety_terms or []),
                                        pack_size=it.pack_size, pack_unit=it.pack_unit, quantity=it.quantity or 1,
                                        rules=resolved))
        queries.append(discovery.ItemQuery(item_id=it.id, raw_text=it.raw_text, query=query,
                                           intention=it.intention or "product", category=it.category or norm(query),
                                           brand=it.brand, taxonomy_id=it.taxonomy_id, rules=resolved))

    # 7.2 descoberta
    found = await discovery.discover(api, queries, lat, lng, only_open=only_open, emit=say)
    for item_id, tax in found.taxonomy_updates.items():
        it = s.get(ListItem, item_id)
        it.taxonomy_id = tax
        s.add(it)
    s.commit()
    if not found.merchants:
        raise McpToolError("searchOffers", "NO_MERCHANTS",
                           "Nenhum mercado aberto perto de você tem esses itens agora.")

    # 7.3 normalização
    await say("normalize", {"message": f"Comparando {len(found.offers)} produtos"})
    normalized = await normalize.build(s, specs, found.offers, affinity=learning.affinity(s, user.id))

    # 7.4 otimização
    quality, max_merchants, split_min = effective(user, lst)
    problem = optimize.Problem(
        items=normalized.items,
        merchants=[optimize.Merchant(m.id, m.name, m.delivery_fee, m.minimum_order) for m in found.merchants.values()],
        candidates=normalized.candidates, alpha=quality, max_merchants=max_merchants)
    await say("optimize", {"message": "Calculando a melhor combinação de produtos e mercados"})
    solutions = await asyncio.to_thread(optimize.solve_with_alternatives, problem, split_min)
    if not solutions or not solutions[0].merchants:
        names = ", ".join(f"{m.name} (mínimo R$ {m.minimum_order:.2f})" for m in found.merchants.values())
        covered = len({o.item_id for o in found.offers})
        message = (f"Nenhum mercado{' aberto' if only_open else ''} atende esta lista agora: achei {covered} de "
                   f"{len(specs)} itens em {names}, e o que dá para comprar não chega ao pedido mínimo.")
        if only_open:
            message += " Fora do horário comercial quase todos os mercados ficam fechados; tente de novo mais tarde."
        raise McpToolError("solver", "NO_SOLUTION", message)
    await say("optimize", {"message": f"{len(solutions)} opções para montar de verdade",
                           "estimates": [sol.estimated_total for sol in solutions]})

    # 7.5 carrinhos reais
    await say("carts", {"message": "Montando os carrinhos no iFood e testando cupons"})
    results = await materialize.materialize(api, solutions, normalized.skus, found.merchants,
                                            addr.ifood_address_id, lat, lng, emit=say, deliver_at=plan.deliver_at)
    winner = materialize.pick_winner(results)
    if winner is None:
        errors = [e for r in results for e in r["errors"]]
        first = errors[0]["error"] if errors else {}
        message = first.get("userMessage") if isinstance(first, dict) else str(first)
        raise McpToolError("createCart", (first or {}).get("code", "CART_FAILED") if isinstance(first, dict) else "CART_FAILED",
                           message or "Não consegui montar nenhum carrinho.")

    single_total = None
    single_merchants = solutions[0].extra.get("single_merchants")
    for r in results:
        if r["ok"] and len(r["merchants"]) == 1:
            single_total = min(single_total or r["total"], r["total"])
    if single_total is None:
        single_total = solutions[0].extra.get("single_estimated_total")

    # 7.6 cartões de decisão
    cards = await decisions.build_cards(specs, winner, normalized.skus, found.merchants, single_total)

    spec_by_id = {sp.id: sp for sp in specs}
    plan.result = {
        "address": {"id": addr.id, "alias": addr.alias, "formatted": addr.formatted},
        "items": [{"id": sp.id, "raw_text": sp.raw_text, "category": sp.category, "query": sp.query,
                   "required": normalize.required_amount(sp)[0], "unit": normalize.required_amount(sp)[1],
                   "rules": sp.rules.applied_rule_ids} for sp in specs],
        "merchants": {m.id: {"name": m.name, "delivery_fee": m.delivery_fee, "minimum_order": m.minimum_order,
                             "distance_km": m.distance_km, "eta_max": m.eta_max, "logo": m.logo,
                             "coverage": found.coverage.get(m.id, 0)} for m in found.merchants.values()},
        "chosen": winner,
        "alternatives": [r for r in results if r is not winner],
        "single_total": single_total,
        "single_merchants": single_merchants,
        "savings_vs_single": round(single_total - winner["total"], 2) if single_total and len(winner["carts"]) > 1 else None,
        "missing": [{"item_id": i, "raw_text": spec_by_id[i].raw_text} for i in winner["missing"]],
        "decisions": cards,
        "candidates": {str(sp.id): [info.to_dict() for key, info in normalized.skus.items() if info.item_id == sp.id]
                       for sp in specs},
        "rejected": {str(k): v for k, v in normalized.rejected.items()},
        "discovery_errors": found.errors,
        "only_open": only_open,
        "deliver_at": plan.deliver_at,
    }
    plan.status = "ready"
    s.add(plan)
    lst.last_total = winner["total"]
    s.add(lst)
    s.commit()
