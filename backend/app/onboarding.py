"""Primeiro login: importa o histórico do iFood para não começar do zero (seção 9)."""

from __future__ import annotations

import logging

from sqlmodel import Session

from app import heuristics, learning
from app.mcp.client import McpToolError
from app.mcp.ifood import Ifood
from app.models import Signal, User

log = logging.getLogger(__name__)


async def import_history(s: Session, api: Ifood, user: User, lat: float, lng: float) -> dict:
    """RECENT_ORDERS de mercado vira sinal "chosen" com o peso de timesOrdered."""
    imported, signals = 0, 0
    try:
        body = await api.recommendations("RECENT_ORDERS", lat, lng, result_kind="ITEM", merchant_type="GROCERIES")
    except McpToolError as exc:
        log.info("sem histórico para importar: %s", exc)
        body = {"results": []}
    for r in body.get("results", []):
        name = r.get("itemName", "")
        parsed = heuristics.parse_item(name)
        brand = heuristics.find_brand(name)
        times = int(r.get("timesOrdered") or 1)
        imported += 1
        for _ in range(min(times, 5)):
            s.add(Signal(user_id=user.id, category=parsed["category"], brand=brand, kind="chosen",
                         unit_price=r.get("promotionalUnitPrice") or r.get("unitPrice")))
            signals += 1
    profile = None
    try:
        profile = (await api.get_user(include_profile=True)).get("profileData", {}).get("userBehaviorDescription")
    except McpToolError:
        profile = None
    user.imported_history = True
    s.add(user)
    s.commit()
    rules = learning.update_rules(s, user.id)
    # o que vem do histórico entra como aprendido e com confiança baixa
    for rule in rules:
        rule.confidence = min(rule.confidence, 0.4)
        s.add(rule)
    s.commit()
    return {"items": imported, "signals": signals, "rules": len(rules), "profile": profile}
