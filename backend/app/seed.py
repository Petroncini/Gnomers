"""Semeia a demo: lista "Compra do mês", regras do usuário e um histórico fictício.

Uso: `uv run python -m app.seed` (apaga e recria as listas, regras e o histórico).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from sqlmodel import Session, delete, select

from app import learning, llm
from app.db import engine, init_db, today
from app.models import ListItem, Order, OrderLine, Plan, Rule, ShoppingList, Signal, Suggestion, User

DEMO_ITEMS = [
    "2 leite integral 1L",
    "arroz 5kg",
    "feijão carioca 1kg",
    "café 500g",
    "detergente 500ml",
    "sabão em pó 1,6kg",
    "papel higiênico 12 rolos",
    "12 ovos",
    "banana prata 1kg",
    "açúcar refinado 1kg",
    "óleo de soja 900ml",
    "macarrão espaguete 500g",
    "molho de tomate 300g",
    "pão de forma",
    "manteiga 200g",
]

# compras passadas fictícias: (dias atrás, categoria, marca, preço pago)
HISTORY = [
    (60, "cafe", "Pilão", 25.9), (45, "cafe", "Pilão", 26.5), (30, "cafe", "Pilão", 24.9), (15, "cafe", "Pilão", 25.4),
    (60, "leite", "Italac", 6.1), (45, "leite", "Italac", 6.2), (30, "leite", "Piracanjuba", 6.3),
    (15, "leite", "Italac", 6.4),
]


async def seed() -> None:
    init_db()
    with Session(engine) as s:
        for model in (Suggestion, Signal, OrderLine, Order, Plan, Rule, ListItem, ShoppingList):
            s.exec(delete(model))
        s.commit()
        user = s.get(User, 1)
        user.quality_profile, user.max_merchants, user.split_min_savings = 0.4, 2, 8.0
        s.add(user)

        lst = ShoppingList(owner_id=user.id, name="Compra do mês", recurrence_days=30,
                           next_due=today() + timedelta(days=3))
        s.add(lst)
        s.commit()
        s.refresh(lst)
        for raw in DEMO_ITEMS:
            parsed = await llm.parse_item(raw)
            s.add(ListItem(list_id=lst.id, raw_text=raw, **parsed))
        s.commit()

        s.add(Rule(user_id=user.id, scope="global", category="detergente", kind="brand_chain",
                   payload={"order": ["Ypê", "Limpol"], "allow_others": True}, hardness="soft", source="user"))

        items = {i.category: i for i in s.exec(select(ListItem).where(ListItem.list_id == lst.id)).all()}
        plan = Plan(user_id=user.id, list_id=lst.id, status="ordered", result={"seed": True})
        s.add(plan)
        s.commit()
        s.refresh(plan)
        for days, cat, brand, price in HISTORY:
            when = datetime.now(timezone.utc) - timedelta(days=days)
            order = Order(plan_id=plan.id, merchant_id="seed", merchant_name="Histórico", ifood_cart_id="seed",
                          total=price, status="DELIVERED", created_at=when)
            s.add(order)
            s.commit()
            s.refresh(order)
            item = items.get(cat)
            s.add(OrderLine(order_id=order.id, list_item_id=item.id if item else 0, ifood_item_id="seed",
                            name=f"{cat} {brand}", brand=brand, unit_price=price, qty=1))
            s.add(Signal(user_id=user.id, category=cat, brand=brand, kind="chosen", unit_price=price,
                         list_item_id=item.id if item else None, created_at=when))
        s.commit()
        learning.update_rules(s, user.id)
        learning.update_periodicity(s, lst.id)
        print(f"lista {lst.id} com {len(DEMO_ITEMS)} itens; regras:",
              [(r.category, r.kind, r.payload, r.source) for r in s.exec(select(Rule)).all()])


if __name__ == "__main__":
    asyncio.run(seed())
