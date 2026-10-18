"""Regras de preferência tipadas e resolução por escopo (seção 4 do plano).

Cada `kind` tem um payload validado e um destino concreto: refinements da busca no
MCP, filtro duro antes do solver ou penalidade dentro do solver.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from typing import Literal, Protocol

from pydantic import BaseModel, Field

DietaryTag = Literal["LAC_FREE", "SUGAR_FREE", "VEGAN", "VEGETARIAN", "ORGANIC", "GLUTEN_FREE"]


class DietaryPayload(BaseModel):
    tags: list[DietaryTag] = Field(min_length=1)


class VarietyPayload(BaseModel):
    terms: list[str] = Field(min_length=1)


class BrandChainPayload(BaseModel):
    order: list[str] = Field(min_length=1)
    allow_others: bool = True


class BrandBlockPayload(BaseModel):
    brands: list[str] = Field(min_length=1)


class MerchantPayload(BaseModel):
    prefer: list[str] = []  # merchantIds
    block: list[str] = []
    names: dict[str, str] = {}  # merchantId -> nome, só para exibir


class DeliveryPayload(BaseModel):
    max_fee: float | None = None
    max_minutes: int | None = None


class NotePayload(BaseModel):
    text: str = Field(min_length=1)


PAYLOADS: dict[str, type[BaseModel]] = {
    "dietary": DietaryPayload,
    "variety": VarietyPayload,
    "brand_chain": BrandChainPayload,
    "brand_block": BrandBlockPayload,
    "merchant": MerchantPayload,
    "delivery": DeliveryPayload,
    "note": NotePayload,
}

# Tipos em que as regras se somam em vez de uma vencer as outras.
ACCUMULATE = {"note", "brand_block"}

SOURCE_RANK = {"user": 3, "chat": 2, "learned": 1}


def validate_payload(kind: str, payload: dict) -> dict:
    if kind not in PAYLOADS:
        raise ValueError(f"tipo de regra desconhecido: {kind}")
    return PAYLOADS[kind].model_validate(payload).model_dump()


def norm(text: str | None) -> str:
    """Minúsculas sem acento, para comparar marcas, categorias e termos."""
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower().strip()


class RuleLike(Protocol):
    id: int | None
    scope: str
    category: str | None
    kind: str
    payload: dict
    hardness: str
    source: str
    confidence: float
    active: bool


@dataclass
class Resolved:
    """Preferências efetivas de um item depois de resolver conflitos."""

    dietary: list[str] = field(default_factory=list)
    variety_terms: list[str] = field(default_factory=list)
    variety_hard: bool = False
    brand_chain: list[str] = field(default_factory=list)
    allow_other_brands: bool = True
    brand_chain_source: str | None = None
    blocked_brands: set[str] = field(default_factory=set)
    prefer_merchants: list[str] = field(default_factory=list)
    block_merchants: set[str] = field(default_factory=set)
    merchant_hard: bool = False
    max_delivery_fee: float | None = None
    max_delivery_minutes: int | None = None
    notes: list[str] = field(default_factory=list)
    applied_rule_ids: list[int] = field(default_factory=list)

    def brand_position(self, brand: str | None) -> int | None:
        """Posição da marca na cadeia do usuário (0 = preferida) ou None se fora dela."""
        b = norm(brand)
        for i, name in enumerate(self.brand_chain):
            if norm(name) == b and b:
                return i
        return None

    def is_blocked_brand(self, brand: str | None) -> bool:
        return bool(brand) and norm(brand) in {norm(x) for x in self.blocked_brands}


def _applies(rule: RuleLike, category: str, list_id: int | None) -> bool:
    if not rule.active:
        return False
    if rule.scope != "global" and rule.scope != str(list_id):
        return False
    return rule.category is None or norm(rule.category) == norm(category)


def _priority(rule: RuleLike) -> tuple:
    # lista vale mais que global, categoria vale mais que nula; empate vai pela origem,
    # depois pela confiança e por fim pela regra mais nova (id maior)
    return (
        2 if rule.scope != "global" else 0,
        1 if rule.category else 0,
        SOURCE_RANK.get(rule.source, 0),
        rule.confidence,
        rule.id or 0,
    )


def resolve(rules: list[RuleLike], category: str, list_id: int | None) -> Resolved:
    applicable = [r for r in rules if _applies(r, category, list_id)]
    winners: dict[str, RuleLike] = {}
    accumulated: dict[str, list[RuleLike]] = {k: [] for k in ACCUMULATE}
    for rule in applicable:
        if rule.kind in ACCUMULATE:
            accumulated[rule.kind].append(rule)
        elif rule.kind not in winners or _priority(rule) > _priority(winners[rule.kind]):
            winners[rule.kind] = rule

    out = Resolved()
    for kind, rule in winners.items():
        p = rule.payload
        if kind == "dietary":
            out.dietary = list(p.get("tags", []))
        elif kind == "variety":
            out.variety_terms = list(p.get("terms", []))
            out.variety_hard = rule.hardness == "hard"
        elif kind == "brand_chain":
            out.brand_chain = list(p.get("order", []))
            out.allow_other_brands = bool(p.get("allow_others", True))
            out.brand_chain_source = rule.source
        elif kind == "merchant":
            out.prefer_merchants = list(p.get("prefer", []))
            out.block_merchants = set(p.get("block", []))
            out.merchant_hard = rule.hardness == "hard"
        elif kind == "delivery":
            out.max_delivery_fee = p.get("max_fee")
            out.max_delivery_minutes = p.get("max_minutes")
        if rule.id is not None:
            out.applied_rule_ids.append(rule.id)

    for rule in accumulated["brand_block"]:
        out.blocked_brands.update(rule.payload.get("brands", []))
    for rule in accumulated["note"]:
        out.notes.append(rule.payload.get("text", ""))
    for rule in accumulated["brand_block"] + accumulated["note"]:
        if rule.id is not None:
            out.applied_rule_ids.append(rule.id)

    # uma marca da cadeia que também está bloqueada sai da cadeia
    if out.blocked_brands:
        out.brand_chain = [b for b in out.brand_chain if not out.is_blocked_brand(b)]
    return out


def describe(kind: str, payload: dict, category: str | None) -> str:
    """Frase curta da regra para a tela de preferências e para a proposta do chat."""
    prefix = f"{category.capitalize()}: " if category else ""
    if kind == "dietary":
        labels = {"LAC_FREE": "sem lactose", "SUGAR_FREE": "sem açúcar", "VEGAN": "vegano",
                  "VEGETARIAN": "vegetariano", "ORGANIC": "orgânico", "GLUTEN_FREE": "sem glúten"}
        return prefix + ", ".join(labels.get(t, t) for t in payload["tags"])
    if kind == "variety":
        return prefix + "sempre " + ", ".join(payload["terms"])
    if kind == "brand_chain":
        chain = ", depois ".join(payload["order"])
        return prefix + chain + ("" if payload.get("allow_others", True) else " (nenhuma outra)")
    if kind == "brand_block":
        return prefix + "nunca " + ", ".join(payload["brands"])
    if kind == "merchant":
        names = payload.get("names", {})
        parts = []
        if payload.get("prefer"):
            parts.append("preferir " + ", ".join(names.get(m, m[:8]) for m in payload["prefer"]))
        if payload.get("block"):
            parts.append("evitar " + ", ".join(names.get(m, m[:8]) for m in payload["block"]))
        return prefix + "; ".join(parts)
    if kind == "delivery":
        parts = []
        if payload.get("max_fee") is not None:
            parts.append(f"frete até R$ {payload['max_fee']:.2f}")
        if payload.get("max_minutes") is not None:
            parts.append(f"entrega em até {payload['max_minutes']} min")
        return prefix + ", ".join(parts)
    if kind == "note":
        return prefix + payload["text"]
    return prefix + kind
