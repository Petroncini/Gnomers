"""Rotas de demonstração e diagnóstico: simular dias e promoções, padrões de compra, chamadas ao MCP."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from app.api import schemas as S
from app.core.config import settings
from app.core.db import advance_days, current_user, get_session
from app.core.models import ListItem, ShoppingList
from app.mcp.ifood import ifood
from app.proactive import promotions, scheduler

router = APIRouter()


class SimulateIn(BaseModel):
    days: int = 7


@router.post("/admin/simulate-days", response_model=S.SimulateResult)
async def simulate_days(body: SimulateIn):
    new_today = advance_days(body.days)
    created = await scheduler.run_daily()
    return {"today": new_today.isoformat(), "created": [c.model_dump(mode="json") for c in created]}


class PromotionIn(BaseModel):
    query: str  # o que entra em promoção, como numa busca ("sabão em pó", "café pilão")
    discount_pct: float = 25
    merchant_id: Optional[str] = None


@router.post("/admin/promotions", response_model=S.PromotionResult)
async def publish_promotion(body: PromotionIn, s: Session = Depends(get_session)):
    """Simula uma promoção nova e dispara a reavaliação das listas (evento PromotionsPublished)."""
    if not 0 < body.discount_pct < 100:
        raise HTTPException(422, "discount_pct precisa estar entre 0 e 100")
    try:
        offers = await promotions.simulate_promotion(s, ifood(), body.query, body.discount_pct, body.merchant_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    [(evaluations, touched)] = await promotions.publish(s, promotions.PromotionsPublished(offers=offers))
    return {"offers": offers, "evaluations": [asdict(e) for e in evaluations],
            "suggestions": [t.model_dump(mode="json") for t in touched]}


@router.get("/admin/patterns", response_model=list[S.ItemPatternView])
async def purchase_patterns(s: Session = Depends(get_session)):
    """Padrão de compra de cada item, para conferir por que a proatividade sugeriu ou não."""
    out = []
    for lst in s.exec(select(ShoppingList).where(ShoppingList.owner_id == current_user(s).id)).all():
        for item in s.exec(select(ListItem).where(ListItem.list_id == lst.id)).all():
            p = promotions.pattern(s, item, lst)
            out.append({"item_id": item.id, "list_id": lst.id, "list_name": lst.name, "raw_text": item.raw_text,
                        "last_purchase": p.last_purchase, "next_due": p.next_due,
                        "pattern": {"interval_days": p.interval_days, "progress": p.progress,
                                    "days_to_due": p.days_to_due, "regularity": p.regularity, "source": p.source,
                                    "purchases": p.purchases}})
    return out


@router.get("/debug/calls", response_model=list[dict[str, Any]])
async def debug_calls(limit: int = 100):
    """Últimas chamadas ao MCP, para o painel técnico e para o feedback."""
    return ifood().recorder.log[-limit:]


@router.get("/health", response_model=S.Health)
async def health():
    return {"ok": True, "mode": settings.mcp_mode}
