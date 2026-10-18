"""Gravação das regras do usuário e o texto com que elas aparecem na tela e no chat."""

from __future__ import annotations

from sqlmodel import Session

from app.core.models import Plan, Rule
from app.preferences.rules import describe, validate_payload


def save_rule(s: Session, user_id: int, data: dict, *, source: str = "user") -> Rule:
    payload = validate_payload(data["kind"], data.get("payload", {}))
    rule = Rule(user_id=user_id, scope=str(data.get("scope", "global")), category=data.get("category") or None,
                kind=data["kind"], payload=payload, hardness=data.get("hardness", "soft"), source=source,
                confidence=1.0 if source == "user" else 0.8)
    s.add(rule)
    s.commit()
    s.refresh(rule)
    return rule


def rule_view(rule: Rule) -> dict:
    text = describe(rule.kind, rule.payload, rule.category)
    if rule.source == "learned" and rule.payload.get("evidence"):
        text += f" (aprendido de {rule.payload['evidence']} compras)"
    return {**rule.model_dump(mode="json"), "text": text}


def rule_proposal(rule: dict, card: dict | None, plan: Plan) -> dict | None:
    """Regra que o chat propõe; a tela confirma o escopo e pode refazer o carrinho com ela."""
    category = rule.get("category") or (card or {}).get("category")
    try:
        payload = validate_payload(rule["kind"], rule["payload"])
    except ValueError:
        return None
    return {"kind": rule["kind"], "category": category, "hardness": rule.get("hardness", "soft"), "payload": payload,
            "text": describe(rule["kind"], payload, category), "scope_options": ["global", str(plan.list_id)]}
