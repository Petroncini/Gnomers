"""Aprendizado implícito (seção 9 do plano).

Cada escolha vira sinal. Por categoria, o sistema calcula uma pontuação por marca e,
quando uma marca domina, cria ou atualiza uma `brand_chain` suave com origem
"learned". Rejeições repetidas viram `brand_block` suave. A periodicidade de cada
item sai das datas de compra.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import timedelta

from sqlmodel import Session, select

from app.db import today
from app.models import ListItem, OrderLine, Rule, Signal
from app.rules import norm

WEIGHTS = {"chosen": 1.0, "accepted": 1.0, "swapped_to": 2.0, "swapped_from": -1.5, "rejected": -2.0}
MIN_POSITIVE = 3
SHARE = 0.7


def record(s: Session, user_id: int, category: str, brand: str | None, kind: str, *,
           unit_price: float | None = None, list_item_id: int | None = None) -> None:
    s.add(Signal(user_id=user_id, category=norm(category), brand=brand, kind=kind, unit_price=unit_price,
                 list_item_id=list_item_id))


def brand_scores(s: Session, user_id: int) -> dict[str, dict[str, dict]]:
    """categoria -> marca -> {score, positive, rejected}"""
    out: dict[str, dict[str, dict]] = defaultdict(lambda: defaultdict(lambda: {"score": 0.0, "positive": 0, "rejected": 0}))
    for sig in s.exec(select(Signal).where(Signal.user_id == user_id)).all():
        if not sig.brand:
            continue
        entry = out[sig.category][sig.brand]
        entry["score"] += WEIGHTS.get(sig.kind, 0.0)
        if WEIGHTS.get(sig.kind, 0) > 0:
            entry["positive"] += 1
        if sig.kind == "rejected":
            entry["rejected"] += 1
    return out


def update_rules(s: Session, user_id: int) -> list[Rule]:
    """Cria ou atualiza as regras aprendidas a partir dos sinais."""
    changed: list[Rule] = []
    learned = {(r.category, r.kind): r for r in s.exec(
        select(Rule).where(Rule.user_id == user_id, Rule.source == "learned")).all()}
    for cat, brands in brand_scores(s, user_id).items():
        positives = {b: v for b, v in brands.items() if v["score"] > 0}
        total_pos = sum(v["positive"] for v in positives.values())
        ranked = sorted(positives.items(), key=lambda kv: -kv[1]["score"])
        if ranked:
            brand, v = ranked[0]
            if v["positive"] >= MIN_POSITIVE and total_pos and v["positive"] / total_pos > SHARE:
                order = [brand] + [b for b, w in ranked[1:3] if w["positive"] >= 2]
                conf = round(min(0.9, 0.3 + 0.1 * v["positive"]), 2)
                rule = learned.get((cat, "brand_chain")) or Rule(user_id=user_id, category=cat, kind="brand_chain",
                                                                 source="learned", hardness="soft")
                payload = {"order": order, "allow_others": True, "evidence": v["positive"]}
                if rule.payload != payload or rule.confidence != conf:
                    rule.payload, rule.confidence = payload, conf
                    s.add(rule)
                    changed.append(rule)
        for brand, v in brands.items():
            if v["rejected"] >= 2:
                rule = learned.get((cat, "brand_block"))
                if rule is None:
                    rule = Rule(user_id=user_id, category=cat, kind="brand_block", source="learned",
                                hardness="soft", payload={"brands": []}, confidence=0.5)
                if brand not in rule.payload.get("brands", []):
                    rule.payload = {"brands": rule.payload.get("brands", []) + [brand]}
                    s.add(rule)
                    changed.append(rule)
    s.commit()
    return changed


def update_periodicity(s: Session, list_id: int) -> None:
    """Mediana dos intervalos entre compras preenche o next_due de quem não tem recorrência."""
    from app.models import Order, Plan

    items = s.exec(select(ListItem).where(ListItem.list_id == list_id)).all()
    for item in items:
        if item.recurrence_days:
            continue
        rows = s.exec(select(Order.created_at).join(OrderLine, OrderLine.order_id == Order.id)
                      .where(OrderLine.list_item_id == item.id).order_by(Order.created_at)).all()
        dates = sorted({r.date() for r in rows})
        if len(dates) < 2:
            continue
        gaps = [(b - a).days for a, b in zip(dates, dates[1:]) if (b - a).days > 0]
        if gaps:
            median = int(statistics.median(gaps))
            item.next_due = dates[-1] + timedelta(days=median)
            s.add(item)
    s.commit()


def item_due(item: ListItem, list_due) -> bool:
    due = item.next_due or list_due
    return bool(due and due <= today() + timedelta(days=7))
