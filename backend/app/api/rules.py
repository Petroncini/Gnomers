"""Preferências do usuário guardadas como regras tipadas."""

from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from app.ai import llm
from app.api import schemas as S
from app.core.db import current_user, get_session
from app.core.models import Rule
from app.preferences.rules import describe, validate_payload
from app.preferences.store import rule_view, save_rule

router = APIRouter()


class RuleIn(BaseModel):
    scope: Optional[str] = "global"
    category: Optional[str] = None
    kind: Optional[str] = None
    payload: Optional[dict] = None
    hardness: Optional[Literal["hard", "soft"]] = None
    active: Optional[bool] = None
    source: Optional[Literal["user", "chat"]] = None


@router.get("/rules", response_model=list[S.RuleView])
async def get_rules(scope: str | None = None, s: Session = Depends(get_session)):
    user = current_user(s)
    q = select(Rule).where(Rule.user_id == user.id)
    if scope:
        q = q.where(Rule.scope == scope)
    return [rule_view(r) for r in s.exec(q.order_by(Rule.id)).all()]


@router.post("/rules", response_model=S.RuleView)
async def post_rule(body: RuleIn, s: Session = Depends(get_session)):
    user = current_user(s)
    if not body.kind:
        raise HTTPException(422, "kind é obrigatório")
    rule = save_rule(s, user.id, body.model_dump(exclude_unset=False), source=body.source or "user")
    return rule_view(rule)


@router.patch("/rules/{rule_id}", response_model=S.RuleView)
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
    return rule_view(rule)


@router.delete("/rules/{rule_id}", response_model=S.Ok)
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


@router.post("/rules/compile", response_model=S.ProposedRule)
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
