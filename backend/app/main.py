"""Rotas FastAPI (seção 12 do plano)."""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import date, datetime, timedelta
from typing import Any, Literal, Optional

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel
from sqlmodel import Session, select
from sse_starlette.sse import EventSourceResponse

from app import actions, llm, onboarding, proactive, scheduler
from app import schemas as S
from app.config import settings
from app.heuristics import PARSER_VERSION
from app.db import advance_days, current_user, get_session, init_db, today
from app.mcp.client import McpToolError, NotAuthenticated
from app.mcp.ifood import ifood
from app.models import Address, ListItem, Order, Plan, Rule, ShoppingList, Suggestion
from app.pipeline import address as address_mod
from app.pipeline.runner import events_for, run_plan
from app.rules import describe, validate_payload

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
FRONTEND = "http://localhost:5173"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    scheduler.start()
    yield
    scheduler.stop()
    if ifood().connection:
        await ifood().connection.close()


app = FastAPI(title="Gnomers", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=[FRONTEND], allow_methods=["*"], allow_headers=["*"],
                   allow_credentials=True)


@app.exception_handler(McpToolError)
async def _mcp_error(_: Request, exc: McpToolError):
    return JSONResponse(status_code=502, content={"detail": exc.user_message, "error": exc.to_dict()})


@app.exception_handler(NotAuthenticated)
async def _auth_error(_: Request, exc: NotAuthenticated):
    return JSONResponse(status_code=401, content={"detail": str(exc), "login": "/auth/login"})


@app.exception_handler(actions.ActionError)
async def _action_error(_: Request, exc: actions.ActionError):
    return JSONResponse(status_code=exc.status, content={"detail": str(exc)})


@app.exception_handler(ValueError)
async def _value_error(_: Request, exc: ValueError):
    return JSONResponse(status_code=422, content={"detail": str(exc)})


# ---------------------------------------------------------------------- auth

@app.get("/auth/status", response_model=S.AuthStatus)
async def auth_status(s: Session = Depends(get_session)):
    api = ifood()
    user = current_user(s)
    connected = api.connection.connected if api.connection else True
    return {"mode": api.mode, "connected": connected, "has_token": bool(user.ifood_token) or api.mode != "live",
            "llm": llm.enabled(), "imported_history": user.imported_history}


@app.get("/auth/login")
async def auth_login():
    api = ifood()
    if api.connection is None:
        return RedirectResponse(FRONTEND)
    url = await api.connection.login()
    return RedirectResponse(url or FRONTEND)


@app.get("/callback")
async def auth_callback(code: str | None = None, state: str | None = None, iss: str | None = None,
                        error: str | None = None):
    api = ifood()
    ok = False
    if not error and code and api.connection is not None:
        api.connection.deliver_code(code, state, iss)
        try:
            ok = await api.connection.wait_connected(timeout=60)
        except asyncio.TimeoutError:
            ok = False
    # página própria em vez de redirecionar: o front-end pode não estar rodando
    title = "Conectado ao iFood" if ok else "O login no iFood falhou"
    detail = "Pode fechar esta aba e voltar ao app." if ok else f"Tente de novo em /auth/login. {error or ''}"
    return HTMLResponse(f"""<!doctype html><meta charset="utf-8"><title>{title}</title>
<body style="font-family:system-ui;max-width:32rem;margin:4rem auto;padding:0 1rem">
<h1>{title}</h1><p>{detail}</p><p><a href="{FRONTEND}/?login={'ok' if ok else 'erro'}">Abrir o Gnomers</a></p></body>""",
                        status_code=200 if ok else 400)


@app.post("/auth/logout", response_model=S.Ok)
async def auth_logout():
    api = ifood()
    if api.connection:
        await api.connection.close()
        api.connection.storage.clear()
    return {"ok": True}


# ------------------------------------------------------------------- perfil

class ProfileIn(BaseModel):
    quality_profile: Optional[float] = None
    max_merchants: Optional[int] = None
    split_min_savings: Optional[float] = None
    name: Optional[str] = None


@app.get("/me", response_model=S.UserView)
async def me(s: Session = Depends(get_session)):
    u = current_user(s)
    return u.model_dump(exclude={"ifood_token"}) | {"today": today().isoformat()}


@app.patch("/me", response_model=S.UserView)
async def patch_me(body: ProfileIn, s: Session = Depends(get_session)):
    u = current_user(s)
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(u, k, v)
    s.add(u)
    s.commit()
    return u.model_dump(exclude={"ifood_token"}) | {"today": today().isoformat()}


@app.post("/onboarding/import", response_model=S.ImportResult)
async def import_history(s: Session = Depends(get_session)):
    user = current_user(s)
    addrs = await address_mod.sync_addresses(s, ifood(), user.id)
    if not addrs:
        raise HTTPException(409, "cadastre um endereço antes de importar o histórico")
    a = next((x for x in addrs if x.is_last_used), addrs[0])
    return await onboarding.import_history(s, ifood(), user, a.lat, a.lng)


# ------------------------------------------------------------------- listas

class ListIn(BaseModel):
    name: Optional[str] = None
    recurrence_days: Optional[int] = None
    next_due: Optional[date] = None
    quality_profile: Optional[float] = None
    max_merchants: Optional[int] = None
    split_min_savings: Optional[float] = None


def _list_view(s: Session, lst: ShoppingList, with_items: bool = False) -> dict:
    data = lst.model_dump(mode="json")
    items = s.exec(select(ListItem).where(ListItem.list_id == lst.id).order_by(ListItem.id)).all()
    data["item_count"] = len(items)
    last = s.exec(select(Plan).where(Plan.list_id == lst.id, Plan.status.in_(["ready", "ordered"]))
                  .order_by(Plan.id.desc())).first()
    data["last_plan_id"] = last.id if last and not (last.result or {}).get("seed") else None
    if with_items:
        data["items"] = [i.model_dump(mode="json") for i in items]
    return data


@app.get("/lists", response_model=list[S.ShoppingListView])
async def get_lists(s: Session = Depends(get_session)):
    user = current_user(s)
    lists = s.exec(select(ShoppingList).where(ShoppingList.owner_id == user.id).order_by(ShoppingList.id)).all()
    return [_list_view(s, lst) for lst in lists]


@app.post("/lists", response_model=S.ShoppingListView)
async def create_list(body: ListIn, s: Session = Depends(get_session)):
    user = current_user(s)
    lst = ShoppingList(owner_id=user.id, name=body.name or "Nova lista", recurrence_days=body.recurrence_days,
                       next_due=body.next_due or (today() if body.recurrence_days else None))
    s.add(lst)
    s.commit()
    s.refresh(lst)
    return _list_view(s, lst, True)


def _get_list(s: Session, list_id: int) -> ShoppingList:
    lst = s.get(ShoppingList, list_id)
    if not lst:
        raise HTTPException(404, "lista não encontrada")
    return lst


@app.get("/lists/{list_id}", response_model=S.ShoppingListView)
async def get_list(list_id: int, s: Session = Depends(get_session)):
    return _list_view(s, _get_list(s, list_id), True)


@app.patch("/lists/{list_id}", response_model=S.ShoppingListView)
async def patch_list(list_id: int, body: ListIn, s: Session = Depends(get_session)):
    lst = _get_list(s, list_id)
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(lst, k, v)
    if body.recurrence_days and not lst.next_due:
        lst.next_due = today()
    s.add(lst)
    s.commit()
    return _list_view(s, lst, True)


@app.delete("/lists/{list_id}", response_model=S.Ok)
async def delete_list(list_id: int, s: Session = Depends(get_session)):
    lst = _get_list(s, list_id)
    for it in s.exec(select(ListItem).where(ListItem.list_id == list_id)).all():
        s.delete(it)
    for r in s.exec(select(Rule).where(Rule.scope == str(list_id))).all():
        s.delete(r)
    s.delete(lst)
    s.commit()
    return {"ok": True}


class ItemIn(BaseModel):
    raw_text: Optional[str] = None
    quantity: Optional[float] = None
    category: Optional[str] = None
    brand: Optional[str] = None
    variety_terms: Optional[list[str]] = None
    pack_size: Optional[float] = None
    pack_unit: Optional[Literal["g", "ml", "un"]] = None
    query: Optional[str] = None
    intention: Optional[Literal["product", "product_exact"]] = None
    recurrence_days: Optional[int] = None


class ParseIn(BaseModel):
    text: str


@app.post("/items/parse", response_model=S.ParsedItem)
async def parse_preview(body: ParseIn):
    """Interpretação do texto enquanto o usuário digita (chips da tela da lista)."""
    return await llm.parse_item(body.text)


@app.post("/lists/{list_id}/items", response_model=S.ListItemView)
async def add_item(list_id: int, body: ItemIn, s: Session = Depends(get_session)):
    _get_list(s, list_id)
    if not body.raw_text:
        raise HTTPException(422, "raw_text é obrigatório")
    parsed = await llm.parse_item(body.raw_text)
    # o que o usuário corrigiu nos chips vence a interpretação
    overrides = body.model_dump(exclude_unset=True, exclude={"raw_text"})
    item = ListItem(list_id=list_id, raw_text=body.raw_text, parser_version=PARSER_VERSION, **{**parsed, **overrides})
    if item.recurrence_days:
        item.next_due = today() + timedelta(days=item.recurrence_days)
    s.add(item)
    s.commit()
    s.refresh(item)
    return item


@app.patch("/lists/{list_id}/items/{item_id}", response_model=S.ListItemView)
async def patch_item(list_id: int, item_id: int, body: ItemIn, s: Session = Depends(get_session)):
    item = s.get(ListItem, item_id)
    if not item or item.list_id != list_id:
        raise HTTPException(404, "item não encontrado")
    changes = body.model_dump(exclude_unset=True)
    if "raw_text" in changes and changes["raw_text"] != item.raw_text:
        parsed = await llm.parse_item(changes["raw_text"])
        for k, v in parsed.items():
            setattr(item, k, v)
        item.taxonomy_id = None
        item.parser_version = PARSER_VERSION
    for k, v in changes.items():
        setattr(item, k, v)
    if "brand" in changes:
        item.intention = "product_exact" if item.brand else "product"
    s.add(item)
    s.commit()
    s.refresh(item)
    return item


@app.delete("/lists/{list_id}/items/{item_id}", response_model=S.Ok)
async def delete_item(list_id: int, item_id: int, s: Session = Depends(get_session)):
    item = s.get(ListItem, item_id)
    if not item or item.list_id != list_id:
        raise HTTPException(404, "item não encontrado")
    s.delete(item)
    s.commit()
    return {"ok": True}


# ------------------------------------------------------------------- regras

class RuleIn(BaseModel):
    scope: Optional[str] = "global"
    category: Optional[str] = None
    kind: Optional[str] = None
    payload: Optional[dict] = None
    hardness: Optional[Literal["hard", "soft"]] = None
    active: Optional[bool] = None
    source: Optional[Literal["user", "chat"]] = None


@app.get("/rules", response_model=list[S.RuleView])
async def get_rules(scope: str | None = None, s: Session = Depends(get_session)):
    user = current_user(s)
    q = select(Rule).where(Rule.user_id == user.id)
    if scope:
        q = q.where(Rule.scope == scope)
    return [actions.rule_view(r) for r in s.exec(q.order_by(Rule.id)).all()]


@app.post("/rules", response_model=S.RuleView)
async def post_rule(body: RuleIn, s: Session = Depends(get_session)):
    user = current_user(s)
    if not body.kind:
        raise HTTPException(422, "kind é obrigatório")
    rule = actions.save_rule(s, user.id, body.model_dump(exclude_unset=False), source=body.source or "user")
    return actions.rule_view(rule)


@app.patch("/rules/{rule_id}", response_model=S.RuleView)
async def patch_rule(rule_id: int, body: RuleIn, s: Session = Depends(get_session)):
    rule = s.get(Rule, rule_id)
    if not rule:
        raise HTTPException(404, "regra não encontrada")
    changes = body.model_dump(exclude_unset=True)
    if "payload" in changes:
        changes["payload"] = validate_payload(changes.get("kind", rule.kind), changes["payload"])
    for k, v in changes.items():
        setattr(rule, k, v)
    s.add(rule)
    s.commit()
    return actions.rule_view(rule)


@app.delete("/rules/{rule_id}", response_model=S.Ok)
async def delete_rule(rule_id: int, s: Session = Depends(get_session)):
    rule = s.get(Rule, rule_id)
    if rule:
        s.delete(rule)
        s.commit()
    return {"ok": True}


class CompileIn(BaseModel):
    text: str
    category: Optional[str] = None
    list_id: Optional[int] = None


@app.post("/rules/compile", response_model=S.ProposedRule)
async def compile_rule(body: CompileIn):
    """Texto livre para uma regra proposta; a tela confirma antes de salvar."""
    out = await llm.extract_rule(body.text, {"type": "preference", "category": body.category, "alternatives": []})
    rule = out.get("rule") or {"kind": "note", "category": body.category, "hardness": "soft",
                               "payload": {"text": body.text}}
    payload = validate_payload(rule["kind"], rule["payload"])
    category = rule.get("category") or body.category
    return {"kind": rule["kind"], "category": category, "hardness": rule.get("hardness", "soft"), "payload": payload,
            "text": describe(rule["kind"], payload, category),
            "scope_options": ["global"] + ([str(body.list_id)] if body.list_id else [])}


# ----------------------------------------------------------------- endereço

class LocationIn(BaseModel):
    lat: float
    lng: float
    accuracy: Optional[float] = None


@app.post("/location", response_model=S.LocationResult)
async def location(body: LocationIn, s: Session = Depends(get_session)):
    user = current_user(s)
    addrs = await address_mod.sync_addresses(s, ifood(), user.id)
    chosen, ranked = address_mod.pick_address(addrs, body.lat, body.lng, body.accuracy)
    # sem endereço perto: a tela deixa escolher entre os salvos; só pede cadastro se não houver nenhum
    return {"address": chosen.model_dump(mode="json") if chosen else None, "nearby": ranked,
            "needs_choice": chosen is None and bool(addrs), "needs_address": not addrs}


@app.get("/addresses", response_model=list[S.AddressView])
async def addresses(s: Session = Depends(get_session)):
    user = current_user(s)
    return [a.model_dump(mode="json") for a in await address_mod.sync_addresses(s, ifood(), user.id)]


class AddressIn(BaseModel):
    address: str
    candidate_id: Optional[str] = None
    alias: Optional[str] = None
    complement: Optional[str] = None
    neighborhood: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None


@app.post("/addresses", response_model=S.CreateAddressResult)
async def create_address(body: AddressIn, s: Session = Depends(get_session)):
    user = current_user(s)
    return await address_mod.create_address(s, ifood(), user.id, body.address, candidate_id=body.candidate_id,
                                            alias=body.alias, complement=body.complement,
                                            neighborhood=body.neighborhood,
                                            near=(body.lat, body.lng) if body.lat is not None and body.lng is not None else None)


# -------------------------------------------------------------------- plano

class PlanIn(BaseModel):
    lat: Optional[float] = None
    lng: Optional[float] = None
    address_id: Optional[int] = None
    deliver_at: Optional[str] = None  # horário local, ex.: "2026-10-19T10:00"


@app.post("/lists/{list_id}/plan", response_model=S.PlanCreated)
async def create_plan(list_id: int, body: PlanIn, background: BackgroundTasks, s: Session = Depends(get_session)):
    user = current_user(s)
    _get_list(s, list_id)
    if body.address_id and not s.get(Address, body.address_id):
        raise HTTPException(404, "endereço não encontrado")
    deliver_at = None
    if body.deliver_at:
        try:
            deliver_at = datetime.fromisoformat(body.deliver_at).strftime("%Y-%m-%dT%H:%M")
        except ValueError:
            raise HTTPException(422, "deliver_at precisa ser data e hora, ex.: 2026-10-19T10:00")
    plan = Plan(user_id=user.id, list_id=list_id, lat=body.lat, lng=body.lng, address_id=body.address_id,
                deliver_at=deliver_at)
    s.add(plan)
    s.commit()
    s.refresh(plan)
    events_for(plan.id)
    background.add_task(run_plan, plan.id)
    return {"id": plan.id, "status": plan.status}


@app.post("/plans/{plan_id}/replan", response_model=S.PlanCreated)
async def replan(plan_id: int, background: BackgroundTasks, s: Session = Depends(get_session)):
    """Novo carrinho para a mesma lista, mantendo endereço, horário e as escolhas feitas neste."""
    old = _get_plan(s, plan_id)
    if not (old.result or {}).get("chosen"):
        raise HTTPException(409, "este plano não tem carrinho para refazer")
    plan = Plan(user_id=old.user_id, list_id=old.list_id, lat=old.lat, lng=old.lng, address_id=old.address_id,
                deliver_at=old.deliver_at, pins=actions.pins_from(old.result))
    s.add(plan)
    s.commit()
    s.refresh(plan)
    events_for(plan.id)
    background.add_task(run_plan, plan.id)
    return {"id": plan.id, "status": plan.status}


def _get_plan(s: Session, plan_id: int) -> Plan:
    plan = s.get(Plan, plan_id)
    if not plan:
        raise HTTPException(404, "plano não encontrado")
    return plan


@app.get("/plans/{plan_id}", response_model=S.PlanView)
async def get_plan(plan_id: int, s: Session = Depends(get_session)):
    plan = _get_plan(s, plan_id)
    data = actions.plan_view(plan)
    data["orders"] = [o.model_dump(mode="json") for o in s.exec(select(Order).where(Order.plan_id == plan_id)).all()]
    return data


@app.get("/plans/{plan_id}/events", response_class=EventSourceResponse, responses={200: {
    "model": S.ProgressEvent,
    "description": "text/event-stream: eventos `progress` com este JSON, `ping` a cada 15 s e `end` no fim"}})
async def plan_events(plan_id: int, request: Request):
    pe = events_for(plan_id)

    async def stream():
        sent = 0
        while True:
            async with pe.cond:
                while sent >= len(pe.events) and not pe.done:
                    try:
                        await asyncio.wait_for(pe.cond.wait(), timeout=15)
                    except asyncio.TimeoutError:
                        break
                batch = pe.events[sent:]
                done = pe.done
            for ev in batch:
                yield {"event": "progress", "data": json.dumps(ev, ensure_ascii=False)}
            sent += len(batch)
            if done and sent >= len(pe.events):
                yield {"event": "end", "data": "{}"}
                return
            if await request.is_disconnected():
                return
            if not batch:
                yield {"event": "ping", "data": "{}"}

    return EventSourceResponse(stream())


class DecisionIn(BaseModel):
    action: Literal["accept", "swap", "remove", "set_quantity", "dismiss"]
    sku_id: Optional[str] = None
    quantity: Optional[int] = None
    rule: Optional[dict] = None


@app.post("/plans/{plan_id}/decisions/{card_id}", response_model=S.DecisionResponse)
async def decide(plan_id: int, card_id: int, body: DecisionIn, s: Session = Depends(get_session)):
    plan = _get_plan(s, plan_id)
    return await actions.answer_decision(s, ifood(), plan, card_id, body.action, sku_id=body.sku_id,
                                         quantity=body.quantity, rule=body.rule)


class ChatIn(BaseModel):
    message: str
    card_id: Optional[int] = None


@app.post("/plans/{plan_id}/chat", response_model=S.ChatResponse)
async def plan_chat(plan_id: int, body: ChatIn, s: Session = Depends(get_session)):
    plan = _get_plan(s, plan_id)
    return await actions.chat(s, ifood(), plan, body.card_id, body.message)


class CheckoutIn(BaseModel):
    accept_changes: bool = False


@app.post("/plans/{plan_id}/checkout", response_model=S.CheckoutResponse)
async def plan_checkout(plan_id: int, body: CheckoutIn, s: Session = Depends(get_session)):
    plan = _get_plan(s, plan_id)
    return await actions.checkout(s, ifood(), plan, accept_changes=body.accept_changes)


@app.get("/orders/{order_id}/track", response_model=S.TrackingView)
async def track(order_id: int, s: Session = Depends(get_session)):
    order = s.get(Order, order_id)
    if not order:
        raise HTTPException(404, "pedido não encontrado")
    return await actions.track(s, ifood(), order)


# ------------------------------------------------------------ proatividade

@app.get("/suggestions", response_model=list[S.SuggestionView])
async def suggestions(s: Session = Depends(get_session)):
    rows = s.exec(select(Suggestion).where(Suggestion.dismissed == False).order_by(Suggestion.id.desc())).all()  # noqa: E712
    return [r.model_dump(mode="json") for r in rows]


@app.post("/suggestions/{sid}/dismiss", response_model=S.Ok)
async def dismiss(sid: int, s: Session = Depends(get_session)):
    sg = s.get(Suggestion, sid)
    if sg:
        sg.dismissed = True
        s.add(sg)
        s.commit()
    return {"ok": True}


class SimulateIn(BaseModel):
    days: int = 7


@app.post("/admin/simulate-days", response_model=S.SimulateResult)
async def simulate_days(body: SimulateIn):
    new_today = advance_days(body.days)
    created = await scheduler.run_daily()
    return {"today": new_today.isoformat(), "created": [c.model_dump(mode="json") for c in created]}


class PromotionIn(BaseModel):
    query: str  # o que entra em promoção, como numa busca ("sabão em pó", "café pilão")
    discount_pct: float = 25
    merchant_id: Optional[str] = None


@app.post("/admin/promotions", response_model=S.PromotionResult)
async def publish_promotion(body: PromotionIn, s: Session = Depends(get_session)):
    """Simula uma promoção nova e dispara a reavaliação das listas (evento PromotionsPublished)."""
    if not 0 < body.discount_pct < 100:
        raise HTTPException(422, "discount_pct precisa estar entre 0 e 100")
    try:
        offers = await proactive.simulate_promotion(s, ifood(), body.query, body.discount_pct, body.merchant_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    [(evaluations, touched)] = await proactive.publish(s, proactive.PromotionsPublished(offers=offers))
    return {"offers": offers, "evaluations": [asdict(e) for e in evaluations],
            "suggestions": [t.model_dump(mode="json") for t in touched]}


@app.get("/admin/patterns", response_model=list[S.ItemPatternView])
async def purchase_patterns(s: Session = Depends(get_session)):
    """Padrão de compra de cada item, para conferir por que a proatividade sugeriu ou não."""
    out = []
    for lst in s.exec(select(ShoppingList).where(ShoppingList.owner_id == current_user(s).id)).all():
        for item in s.exec(select(ListItem).where(ListItem.list_id == lst.id)).all():
            p = proactive.pattern(s, item, lst)
            out.append({"item_id": item.id, "list_id": lst.id, "list_name": lst.name, "raw_text": item.raw_text,
                        "last_purchase": p.last_purchase, "next_due": p.next_due,
                        "pattern": {"interval_days": p.interval_days, "progress": p.progress,
                                    "days_to_due": p.days_to_due, "regularity": p.regularity, "source": p.source,
                                    "purchases": p.purchases}})
    return out


@app.get("/debug/calls", response_model=list[dict[str, Any]])
async def debug_calls(limit: int = 100):
    """Últimas chamadas ao MCP, para o painel técnico e para o feedback."""
    return ifood().recorder.log[-limit:]


@app.get("/health", response_model=S.Health)
async def health():
    return {"ok": True, "mode": settings.mcp_mode}
