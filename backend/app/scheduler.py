"""Recorrência e proatividade (seção 10 do plano).

O job diário olha os itens e listas que vencem nos próximos sete dias. Para cada
item, roda a query salva só com promoções e passa as ofertas para
`proactive.evaluate_item`, que decide pelo desconto e pelo padrão de compra. A
proatividade por evento (promoção publicada) está em `proactive.py`.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlmodel import Session, select

from app import proactive
from app.db import engine, today
from app.mcp.client import McpToolError, NotAuthenticated
from app.mcp.ifood import Ifood, ifood
from app.models import Address, ListItem, ShoppingList, Suggestion, User
from app.pipeline.address import sync_addresses

log = logging.getLogger(__name__)
HORIZON_DAYS = 7


def _location(s: Session, user_id: int) -> tuple[float, float] | None:
    addrs = s.exec(select(Address).where(Address.user_id == user_id)).all()
    if not addrs:
        return None
    a = next((x for x in addrs if x.is_last_used), addrs[0])
    return a.lat, a.lng


async def run_daily(api: Ifood | None = None) -> list[Suggestion]:
    api = api or ifood()
    created: list[Suggestion] = []
    horizon = today() + timedelta(days=HORIZON_DAYS)
    with Session(engine) as s:
        user = s.get(User, 1)
        try:
            await sync_addresses(s, api, user.id)
        except (McpToolError, NotAuthenticated) as exc:
            log.info("sem endereço atualizado para o job: %s", exc)
        loc = _location(s, user.id)
        existing = {(sg.list_item_id, sg.kind, sg.list_id) for sg in s.exec(
            select(Suggestion).where(Suggestion.dismissed == False)).all()}  # noqa: E712

        for lst in s.exec(select(ShoppingList).where(ShoppingList.owner_id == user.id)).all():
            items = s.exec(select(ListItem).where(ListItem.list_id == lst.id)).all()
            if lst.next_due and lst.next_due <= horizon and (None, "list_due", lst.id) not in existing:
                days = (lst.next_due - today()).days
                when = "hoje" if days <= 0 else ("amanhã" if days == 1 else f"em {days} dias")
                sg = Suggestion(user_id=user.id, list_id=lst.id, kind="list_due",
                                text=f"A lista \"{lst.name}\" vence {when}. Quer que eu monte o carrinho?",
                                data={"due": lst.next_due.isoformat(), "items": len(items)})
                s.add(sg)
                created.append(sg)
            if loc is None:
                continue
            for item in items:
                due = item.next_due or lst.next_due
                if not due or due > horizon or (item.id, "promo", lst.id) in existing:
                    continue
                try:
                    body = await api.search_items(item.query or item.raw_text, loc[0], loc[1],
                                                  intention=item.intention or "product", page_size=10,
                                                  refinements={"onlyWithPromotion": True, "sort": "PRICE"})
                except (McpToolError, NotAuthenticated, ValueError) as exc:
                    log.info("busca de promoção falhou para %s: %s", item.raw_text, exc)
                    continue
                ev = proactive.evaluate_item(s, user.id, lst, item, proactive.offers_from_search(body))
                if ev.changed:
                    created.append(s.get(Suggestion, ev.suggestion_id))
            agg = proactive.aggregate_list(s, user.id, lst)
            if agg:
                created.append(agg)
        s.commit()
        for sg in created:
            s.refresh(sg)
    return created


_scheduler: AsyncIOScheduler | None = None


def start() -> None:
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler()
        _scheduler.add_job(run_daily, "cron", hour=8, minute=0, id="daily", replace_existing=True)
        _scheduler.start()


def stop() -> None:
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None
