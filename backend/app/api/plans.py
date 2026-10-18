"""Montagem do carrinho (plano), progresso por SSE, cartões, chat, checkout e rastreio."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any, Literal, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlmodel import Session, select
from sse_starlette.sse import EventSourceResponse

from app.ai.chat import chat
from app.api import schemas as S
from app.api.lists import get_list_or_404
from app.cart import changes
from app.cart.checkout import checkout
from app.cart.checkout import track as track_order
from app.core.db import current_user, get_session
from app.core.models import Address, Order, Plan
from app.mcp.ifood import ifood
from app.pipeline.runner import events_for, run_plan

router = APIRouter()


class PlanIn(BaseModel):
    lat: Optional[float] = None
    lng: Optional[float] = None
    address_id: Optional[int] = None
    deliver_at: Optional[str] = None  # horário local, ex.: "2026-10-19T10:00"


@router.post("/lists/{list_id}/plan", response_model=S.PlanCreated)
async def create_plan(list_id: int, body: PlanIn, background: BackgroundTasks, s: Session = Depends(get_session)):
    user = current_user(s)
    get_list_or_404(s, list_id)
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


@router.post("/plans/{plan_id}/replan", response_model=S.PlanCreated)
async def replan(plan_id: int, background: BackgroundTasks, s: Session = Depends(get_session)):
    """Novo carrinho para a mesma lista, mantendo endereço, horário e as escolhas feitas neste."""
    old = _get_plan(s, plan_id)
    if not (old.result or {}).get("chosen"):
        raise HTTPException(409, "este plano não tem carrinho para refazer")
    plan = Plan(user_id=old.user_id, list_id=old.list_id, lat=old.lat, lng=old.lng, address_id=old.address_id,
                deliver_at=old.deliver_at, pins=changes.pins_from(old.result))
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


@router.get("/plans/{plan_id}", response_model=S.PlanView)
async def get_plan(plan_id: int, s: Session = Depends(get_session)):
    plan = _get_plan(s, plan_id)
    data = changes.plan_view(plan)
    data["orders"] = [o.model_dump(mode="json") for o in s.exec(select(Order).where(Order.plan_id == plan_id)).all()]
    return data


@router.get("/plans/{plan_id}/metrics", response_model=dict[str, Any])
async def plan_metrics(plan_id: int, s: Session = Depends(get_session)):
    """Diagnóstico: tempo por etapa, chamadas ao MCP e ao LLM, e custo em USD deste carrinho."""
    plan = _get_plan(s, plan_id)
    return (plan.result or {}).get("metrics") or {}


@router.get("/plans/{plan_id}/events", response_class=EventSourceResponse, responses={200: {
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


def _fresh_plan(s: Session, plan_id: int) -> Plan:
    """O plano como está no banco agora: outra requisição pode ter mudado o carrinho enquanto esta esperava."""
    plan = _get_plan(s, plan_id)
    s.refresh(plan)
    return plan


@router.post("/plans/{plan_id}/decisions/{card_id}", response_model=S.DecisionResponse)
async def decide(plan_id: int, card_id: int, body: DecisionIn, s: Session = Depends(get_session)):
    async with changes.plan_lock(plan_id):
        plan = _fresh_plan(s, plan_id)
        return await changes.answer_decision(s, ifood(), plan, card_id, body.action, sku_id=body.sku_id,
                                             quantity=body.quantity, rule=body.rule)


class ChatIn(BaseModel):
    message: str
    card_id: Optional[int] = None


@router.post("/plans/{plan_id}/chat", response_model=S.ChatResponse)
async def plan_chat(plan_id: int, body: ChatIn, s: Session = Depends(get_session)):
    async with changes.plan_lock(plan_id):
        plan = _fresh_plan(s, plan_id)
        return await chat(s, ifood(), plan, body.card_id, body.message)


class CheckoutIn(BaseModel):
    accept_changes: bool = False


@router.post("/plans/{plan_id}/checkout", response_model=S.CheckoutResponse)
async def plan_checkout(plan_id: int, body: CheckoutIn, s: Session = Depends(get_session)):
    async with changes.plan_lock(plan_id):
        plan = _fresh_plan(s, plan_id)
        return await checkout(s, ifood(), plan, accept_changes=body.accept_changes)


@router.get("/orders/{order_id}/track", response_model=S.TrackingView)
async def track(order_id: int, s: Session = Depends(get_session)):
    order = s.get(Order, order_id)
    if not order:
        raise HTTPException(404, "pedido não encontrado")
    return await track_order(s, ifood(), order)
