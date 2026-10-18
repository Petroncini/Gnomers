"""Pipeline "Criar carrinho" de ponta a ponta, com progresso por SSE (seção 7)."""

from __future__ import annotations

import asyncio
import logging
import time
import traceback
from dataclasses import dataclass, field

from sqlmodel import Session, select

from app.ai import heuristics, llm
from app.ai.heuristics import PARSER_VERSION
from app.core import metrics
from app.core.db import engine
from app.core.models import Address, ListItem, Plan, Rule, ShoppingList, User
from app.mcp.client import McpToolError, NotAuthenticated
from app.mcp.ifood import Ifood, ifood
from app.pipeline import address as address_mod
from app.pipeline import decisions, discovery, materialize, normalize, optimize
from app.preferences.rules import brl, norm, resolve

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


def effective(user: User, lst: ShoppingList) -> tuple[int, float | None]:
    k = lst.max_merchants if lst.max_merchants is not None else user.max_merchants
    split = lst.split_min_savings if lst.split_min_savings is not None else user.split_min_savings
    return max(1, k), split


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

    m = metrics.Metrics()
    metrics.current.set(m)

    async def say(stage: str, data: dict) -> None:
        m.stage(stage)
        await emit(plan_id, stage, data)

    with Session(engine) as s:
        plan = s.get(Plan, plan_id)
        try:
            plan.status = "running"
            s.add(plan)
            s.commit()
            await _run(s, api, plan, say, only_open)
            _save_metrics(s, plan, m)
            await emit(plan_id, "done", {"message": "Carrinho pronto", "status": plan.status}, final=True)
        except NotAuthenticated as exc:
            _fail(s, plan, "auth", str(exc))
            _save_metrics(s, plan, m)
            await emit(plan_id, "error", {"message": str(exc), "code": "AUTH"}, final=True)
        except McpToolError as exc:
            _fail(s, plan, exc.code, exc.user_message or exc.message)
            _save_metrics(s, plan, m)
            await emit(plan_id, "error", {"message": exc.user_message or exc.message, "code": exc.code}, final=True)
        except Exception as exc:  # noqa: BLE001 - a tela precisa saber que falhou
            log.error("pipeline falhou: %s", traceback.format_exc())
            _fail(s, plan, "internal", f"{type(exc).__name__}: {exc}")
            _save_metrics(s, plan, m)
            await emit(plan_id, "error", {"message": str(exc), "code": "INTERNAL"}, final=True)


def _save_metrics(s: Session, plan: Plan, m: metrics.Metrics) -> None:
    summary = m.summary()
    plan.result = {**(plan.result or {}), "metrics": summary}
    s.add(plan)
    s.commit()
    log.info("plano %s: %.1f s, %d chamadas MCP, %d chamadas LLM, US$ %.4f", plan.id, summary["total_s"],
             summary["mcp"]["calls"], summary["llm"]["calls"], summary["llm"]["cost_usd"])


def _env_only_open() -> bool:
    import os

    return os.environ.get("GNOMERS_ONLY_OPEN", "1") != "0"


def _fail(s: Session, plan: Plan, code: str, message: str) -> None:
    plan.status = "failed"
    plan.error = message
    plan.result = {**(plan.result or {}), "error": {"code": code, "message": message}}
    s.add(plan)
    s.commit()


async def _refresh_stale_items(s: Session, items: list[ListItem]) -> None:
    """Reinterpreta itens salvos por um parser antigo."""
    changed = False
    for it in items:
        before = (it.query, it.category, it.taxonomy_id, it.parser_version)
        if (it.parser_version or 0) < PARSER_VERSION:
            parsed = await llm.parse_item(it.raw_text)
            user_brand = it.brand
            for k, v in parsed.items():
                setattr(it, k, v)
            # marca escolhida nos chips vale mais que a releitura
            if user_brand and not it.brand:
                it.brand, it.intention = user_brand, "product_exact"
            it.taxonomy_id = None
            it.parser_version = PARSER_VERSION
            it.search_plan = None
        if (it.query, it.category, it.taxonomy_id, it.parser_version) != before:
            s.add(it)
            changed = True
    if changed:
        s.commit()


async def _ensure_search_plans(s: Session, items: list[ListItem]) -> None:
    """Buscas de cada item no vocabulário de rótulo, decididas pelo LLM uma vez e guardadas no item."""
    missing = [it for it in items if not it.search_plan]
    plans = await asyncio.gather(*(llm.plan_searches(it.raw_text, it.category or it.query, it.brand) for it in missing))
    for it, plan in zip(missing, plans):
        it.search_plan = plan
        s.add(it)
    if missing:
        s.commit()


async def _run(s: Session, api: Ifood, plan: Plan, say, only_open: bool) -> None:
    user = s.get(User, plan.user_id)
    lst = s.get(ShoppingList, plan.list_id)
    items = s.exec(select(ListItem).where(ListItem.list_id == lst.id)).all()
    pins = plan.pins or {}
    items = [it for it in items if not pins.get(str(it.id), {}).get("removed")]
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

    await _refresh_stale_items(s, items)
    await _ensure_search_plans(s, items)

    specs: list[normalize.ItemSpec] = []
    queries: list[discovery.ItemQuery] = []
    for it in items:
        resolved = resolve(rules, it.category, lst.id)
        search = dict(it.search_plan or {})
        # o plano do LLM separa característica de rótulo de modo de uso ("filtro" não vem no rótulo);
        # a lista crua do parser só vale quando não há plano
        variety = list(search["must_have"]) if "must_have" in search else list(it.variety_terms or [])
        required_terms: list[str] = []
        if resolved.product_description:
            # preferência "o que eu quero de verdade" para a categoria: as buscas saem da descrição dela
            search = await llm.plan_searches(resolved.product_description, it.category, it.brand)
            required_terms = list(search.get("must_have") or [])
            variety = list(dict.fromkeys(variety + required_terms))
        query = search.get("specific") or it.query or it.raw_text
        core = search.get("broad") or heuristics.core_query(it.category or norm(query), query, it.brand)
        # variedade obrigatória (regra dura) entra na busca específica
        if resolved.variety_hard:
            missing = [t for t in resolved.variety_terms if norm(t) not in norm(query)]
            query = " ".join([query] + missing)
        specs.append(normalize.ItemSpec(id=it.id, raw_text=it.raw_text, query=query, category=it.category or norm(query),
                                        brand=it.brand, variety_terms=variety,
                                        pack_size=it.pack_size, pack_unit=it.pack_unit, quantity=it.quantity or 1,
                                        rules=resolved, pin_size=pins.get(str(it.id), {}).get("size"),
                                        required_terms=required_terms,
                                        request=resolved.product_description or it.raw_text))
        queries.append(discovery.ItemQuery(item_id=it.id, raw_text=it.raw_text, query=query,
                                           intention=it.intention or "product", category=it.category or norm(query),
                                           brand=it.brand, rules=resolved, core=core,
                                           variants=list(search.get("alternatives") or [])))

    # 7.2 descoberta
    found = await discovery.discover(api, queries, lat, lng, only_open=only_open, emit=say)
    if not found.merchants:
        searches = sum(len(q.searches()) for q in queries)
        failed = sum(1 for e in found.errors if e.get("stage") == "fase1")
        if failed and failed >= searches:
            raise McpToolError("searchOffers", "SEARCH_FAILED",
                               "O iFood não respondeu à busca agora. Tente de novo em instantes.")
        raise McpToolError("searchOffers", "NO_MERCHANTS",
                           "Nenhum mercado perto de você tem esses itens agora."
                           + (" Escolha um horário de entrega para incluir os mercados fechados." if only_open else ""))

    # 7.3 normalização
    await say("normalize", {"message": f"Comparando {len(found.offers)} produtos"})
    normalized = await normalize.build(s, specs, found.offers)

    # 7.4 otimização
    max_merchants, split_min = effective(user, lst)
    problem = optimize.Problem(
        items=normalized.items,
        merchants=[optimize.Merchant(m.id, m.name, m.delivery_fee, m.minimum_order) for m in found.merchants.values()],
        candidates=normalized.candidates, max_merchants=max_merchants)
    await say("optimize", {"message": "Calculando a melhor combinação de produtos e mercados"})
    solutions = await asyncio.to_thread(optimize.solve_with_alternatives, problem, split_min)
    if not solutions or not solutions[0].merchants:
        covered = {c.item_id for c in normalized.candidates}
        cheapest = {}
        for c in normalized.candidates:
            req = next(i.required for i in normalized.items if i.id == c.item_id)
            cost = c.price * optimize.packs_for(req, c.size)
            cheapest[c.item_id] = min(cheapest.get(c.item_id, cost), cost)
        basket = round(sum(cheapest.values()), 2)
        lowest_min = min((m.minimum_order for m in found.merchants.values()), default=0)
        missing = [sp.raw_text for sp in specs if sp.id not in covered]
        parts = [f"Achei {len(covered)} de {len(specs)} itens."]
        if missing:
            parts.append("Não encontrei: " + ", ".join(missing) + ".")
        if covered and basket < lowest_min:
            parts.append(f"Os itens encontrados custam cerca de {brl(basket)}, abaixo do menor pedido mínimo "
                         f"entre os mercados ({brl(lowest_min)}). Acrescente itens à lista.")
        elif only_open:
            parts.append("Fora do horário comercial quase todos os mercados ficam fechados; "
                         "escolha um horário de entrega ou tente mais tarde.")
        else:
            parts.append("Nenhum mercado tem os itens com um total que chegue ao pedido mínimo dele.")
        message = " ".join(parts)
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
