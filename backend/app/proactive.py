"""Proatividade orientada a eventos (seção 10 do plano, versão por evento).

Um evento `PromotionsPublished` (hoje disparado por `POST /admin/promotions`, que
injeta a promoção no simulador) reavalia as listas do usuário: cada oferta é casada
com os itens das listas, o desconto é medido contra o preço médio pago e a decisão
de sugerir depende do padrão de frequência de compra do item:

- `advance`: o item já passou da metade do ciclo (ou vence em até 7 dias) e o
  desconto passa de 15%. Sugere adiantar a compra.
- `stock`: o item foi comprado há pouco, mas o desconto é grande (30% ou mais) e a
  compra é regular. Sugere estocar.
- sem ação: comprado há pouco e desconto pequeno, ou sem padrão nenhum.

O padrão sai do histórico (duas compras já dão um intervalo) e, sem histórico, da
recorrência declarada no item ou na lista. Com dois ou mais itens em promoção na
mesma lista, uma sugestão `list_promo` agrega a economia e oferece montar o carrinho.

O job diário de `scheduler.py` usa a mesma avaliação (`evaluate_item`).
"""

from __future__ import annotations

import logging
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Awaitable, Callable

from sqlmodel import Session, select

from app.db import today
from app.models import ListItem, Order, OrderLine, Rule, ShoppingList, Suggestion
from app.rules import Resolved, norm, resolve

log = logging.getLogger(__name__)

ADVANCE_MIN_DISCOUNT = 0.15
STOCK_MIN_DISCOUNT = 0.30
STOCK_MIN_REGULARITY = 0.5
ADVANCE_MIN_PROGRESS = 0.5
HORIZON_DAYS = 7


# ------------------------------------------------------------------ eventos

@dataclass
class PromotionsPublished:
    """Uma ou mais ofertas entraram em promoção. `offers` segue o formato de `PromoOffer`."""

    offers: list[dict]
    source: str = "simulated"


Handler = Callable[[Session, Any], Awaitable[Any]]
_handlers: dict[type, list[Handler]] = defaultdict(list)


def subscribe(event_type: type, handler: Handler) -> None:
    _handlers[event_type].append(handler)


async def publish(s: Session, event: Any) -> list[Any]:
    """Despacha em processo e espera os handlers, para a rota devolver o resultado."""
    return [await h(s, event) for h in _handlers[type(event)]]


# ------------------------------------------------------------------ padrão de compra

@dataclass
class Pattern:
    item_id: int
    list_id: int
    purchases: int
    last_purchase: date | None
    interval_days: int | None
    next_due: date | None
    regularity: float  # 0 a 1; 1 = intervalos idênticos
    source: str  # history | item_recurrence | list_recurrence | list_due | none

    @property
    def days_to_due(self) -> int | None:
        return (self.next_due - today()).days if self.next_due else None

    @property
    def progress(self) -> float | None:
        """Fração do ciclo já passada: 0 = acabou de comprar, 1 = vence hoje, >1 = atrasado."""
        if self.days_to_due is None:
            return None
        if not self.interval_days:
            return 1.0 if self.days_to_due <= HORIZON_DAYS else 0.0
        return round(1 - self.days_to_due / self.interval_days, 2)


def purchase_dates(s: Session, item_id: int) -> list[date]:
    rows = s.exec(select(Order.created_at).join(OrderLine, OrderLine.order_id == Order.id)
                  .where(OrderLine.list_item_id == item_id)).all()
    return sorted({r.date() for r in rows})


def pattern(s: Session, item: ListItem, lst: ShoppingList) -> Pattern:
    dates = purchase_dates(s, item.id)
    last = dates[-1] if dates else None
    gaps = [(b - a).days for a, b in zip(dates, dates[1:]) if (b - a).days > 0]
    if gaps:
        interval, source = int(statistics.median(gaps)), "history"
        # um intervalo só não diz se a compra é regular; fica no meio
        regularity = 0.5 if len(gaps) == 1 else max(0.0, 1 - statistics.pstdev(gaps) / statistics.mean(gaps))
    elif item.recurrence_days:
        interval, source, regularity = item.recurrence_days, "item_recurrence", 0.7
    elif lst.recurrence_days:
        interval, source, regularity = lst.recurrence_days, "list_recurrence", 0.7
    else:
        interval, source, regularity = None, "list_due" if lst.next_due else "none", 0.5

    if last and interval:
        due = last + timedelta(days=interval)
    else:
        due = item.next_due or lst.next_due
    return Pattern(item_id=item.id, list_id=lst.id, purchases=len(dates), last_purchase=last, interval_days=interval,
                   next_due=due, regularity=round(regularity, 2), source=source)


def avg_paid(s: Session, item_id: int) -> float | None:
    prices = s.exec(select(OrderLine.unit_price).where(OrderLine.list_item_id == item_id)).all()
    return sum(prices) / len(prices) if prices else None


# ------------------------------------------------------------------ avaliação

PACK_TOLERANCE = 0.2


def matches(item: ListItem, name: str) -> bool:
    """Mesmo produto da lista: categoria, marca e variedade pedidas e embalagem parecida."""
    from app.heuristics import normalize_product

    prod = normalize_product(item.category, name or "", item.query)
    if not prod["matches"]:
        return False
    if item.brand and norm(item.brand) not in norm(name):
        return False
    if any(norm(t) not in norm(name) for t in item.variety_terms or []):
        return False
    if item.pack_size and prod["pack_size"]:
        if item.pack_unit != prod["pack_unit"] or abs(prod["pack_size"] / item.pack_size - 1) > PACK_TOLERANCE:
            return False
    return True


def preferences(s: Session, user_id: int, item: ListItem, lst: ShoppingList) -> Resolved:
    rules = s.exec(select(Rule).where(Rule.user_id == user_id, Rule.active == True)).all()  # noqa: E712
    return resolve(list(rules), item.category, lst.id)


def allowed(prefs: Resolved, offer: dict) -> bool:
    """Respeita marcas bloqueadas, cadeia de marcas fechada e mercados bloqueados."""
    from app.heuristics import find_brand

    brand = find_brand(offer.get("name", ""))
    if prefs.is_blocked_brand(brand) or offer.get("merchant_id") in prefs.block_merchants:
        return False
    return prefs.allow_other_brands or not prefs.brand_chain or prefs.brand_position(brand) is not None


def decide(p: Pattern, discount: float) -> tuple[str | None, str]:
    """Ação e motivo, em português, para a tela de teste."""
    if p.progress is None:
        return None, "sem padrão de compra (sem histórico, recorrência ou data prevista)"
    due_soon = p.days_to_due is not None and p.days_to_due <= HORIZON_DAYS
    if (p.progress >= ADVANCE_MIN_PROGRESS or due_soon) and discount >= ADVANCE_MIN_DISCOUNT:
        return "advance", f"{round(p.progress * 100)}% do ciclo passado e desconto de {round(discount * 100)}%"
    if discount >= STOCK_MIN_DISCOUNT and p.regularity >= STOCK_MIN_REGULARITY:
        return "stock", f"comprado há pouco, mas desconto de {round(discount * 100)}% em compra regular"
    if discount < ADVANCE_MIN_DISCOUNT:
        return None, f"desconto de {round(discount * 100)}% abaixo de {round(ADVANCE_MIN_DISCOUNT * 100)}%"
    return None, (f"só {round(p.progress * 100)}% do ciclo passado; estocar exige "
                  f"{round(STOCK_MIN_DISCOUNT * 100)}% de desconto e regularidade")


def brl(value: float) -> str:
    return f"R$ {value:.2f}".replace(".", ",")


def _when(days: int) -> str:
    if days < 0:
        return f"há {-days} dias (atrasada)"
    return "hoje" if days == 0 else ("amanhã" if days == 1 else f"em {days} dias")


def _text(action: str, best: dict, p: Pattern) -> str:
    every = f"Você compra a cada ~{p.interval_days} dias" if p.interval_days else "Está na sua lista"
    paid = f", você costuma pagar {brl(best['avg_paid'])}" if best.get("avg_paid") else ""
    head = f"{best['name']} está {best['pct']}% mais barato no {best['merchant']} ({brl(best['price'])}{paid})."
    if action == "stock":
        return f"{head} {every} e comprou há pouco, mas o desconto é bom. Estocar agora?"
    nxt = f", e a próxima compra seria {_when(p.days_to_due)}" if p.days_to_due is not None else ""
    return f"{head} {every}{nxt}. Adiantar?"


@dataclass
class Evaluation:
    item_id: int
    list_id: int
    raw_text: str
    offer: str | None = None
    discount_pct: int | None = None
    action: str | None = None
    reason: str = ""
    suggestion_id: int | None = None
    pattern: dict = field(default_factory=dict)
    changed: bool = False  # a sugestão foi criada ou atualizada agora


def evaluate_item(s: Session, user_id: int, lst: ShoppingList, item: ListItem, offers: list[dict]) -> Evaluation:
    """Escolhe a melhor oferta para o item, decide pelo padrão e cria ou atualiza a sugestão."""
    p = pattern(s, item, lst)
    ev = Evaluation(item_id=item.id, list_id=lst.id, raw_text=item.raw_text,
                    pattern={"interval_days": p.interval_days, "progress": p.progress, "days_to_due": p.days_to_due,
                             "regularity": p.regularity, "source": p.source, "purchases": p.purchases})
    # o desconto é a promoção da própria oferta; o histórico só barra o que sai mais caro
    # do que o usuário costuma pagar (comparar com outra marca ou tamanho daria descontos falsos)
    paid = avg_paid(s, item.id)
    prefs = preferences(s, user_id, item, lst)
    best = None
    for o in offers:
        if not o.get("price") or not o.get("unit_price") or not matches(item, o.get("name", "")):
            continue
        if not allowed(prefs, o):
            continue
        discount = 1 - o["price"] / o["unit_price"]
        if best is None or discount > best["discount"] or (discount == best["discount"] and o["price"] < best["price"]):
            best = {"discount": round(discount, 3), "pct": round(discount * 100), "price": o["price"],
                    "reference": o["unit_price"], "avg_paid": round(paid, 2) if paid else None,
                    "name": o.get("name"), "sku_id": o.get("sku_id"), "merchant_id": o.get("merchant_id"),
                    "merchant": o.get("merchant"), "simulated": bool(o.get("simulated"))}
    if best is None:
        ev.reason = "nenhuma oferta casa com o item (categoria, marca, variedade, tamanho ou regras)"
        return ev
    ev.offer, ev.discount_pct = best["name"], best["pct"]
    if paid and best["price"] >= paid:
        ev.reason = f"mesmo com desconto, {brl(best['price'])} não é menor que a média paga ({brl(paid)})"
        return ev
    action, ev.reason = decide(p, best["discount"])
    if action is None:
        return ev
    ev.action = action

    data = {**best, "action": action, "quantity": item.quantity, **{f"pattern_{k}": v for k, v in ev.pattern.items()}}
    active = s.exec(select(Suggestion).where(Suggestion.list_item_id == item.id, Suggestion.list_id == lst.id,
                                             Suggestion.kind == "promo", Suggestion.dismissed == False)).first()  # noqa: E712
    if active and active.data.get("discount", 0) >= best["discount"]:
        ev.reason += "; já existe sugestão com desconto igual ou maior"
        ev.suggestion_id = active.id
        return ev
    dismissed = s.exec(select(Suggestion).where(Suggestion.list_item_id == item.id, Suggestion.kind == "promo",
                                                Suggestion.dismissed == True)).all()  # noqa: E712
    if any(d.data.get("sku_id") == best["sku_id"] and d.data.get("price", 0) <= best["price"] for d in dismissed):
        ev.action, ev.reason = None, "o usuário já dispensou esta oferta por este preço"
        return ev
    sg = active or Suggestion(user_id=user_id, list_id=lst.id, list_item_id=item.id, kind="promo", text="")
    sg.text, sg.data = _text(action, best, p), data
    s.add(sg)
    s.flush()
    ev.suggestion_id, ev.changed = sg.id, True
    return ev


def aggregate_list(s: Session, user_id: int, lst: ShoppingList) -> Suggestion | None:
    """Com duas ou mais promoções ativas na lista, uma sugestão só para montar o carrinho."""
    promos = s.exec(select(Suggestion).where(Suggestion.list_id == lst.id, Suggestion.kind == "promo",
                                             Suggestion.dismissed == False)).all()  # noqa: E712
    current = s.exec(select(Suggestion).where(Suggestion.list_id == lst.id, Suggestion.kind == "list_promo",
                                              Suggestion.dismissed == False)).first()  # noqa: E712
    if len(promos) < 2:
        return None
    savings = round(sum(max(0.0, p.data["reference"] - p.data["price"]) * (p.data.get("quantity") or 1)
                        for p in promos), 2)
    data = {"items": [p.list_item_id for p in promos], "count": len(promos), "savings": savings}
    if current and current.data == data:
        return None
    sg = current or Suggestion(user_id=user_id, list_id=lst.id, kind="list_promo", text="")
    sg.text = (f"{len(promos)} itens da lista \"{lst.name}\" estão em promoção agora, "
               f"economia estimada de {brl(savings)}. Montar o carrinho?")
    sg.data = data
    s.add(sg)
    s.flush()
    return sg


def reevaluate(s: Session, user_id: int, offers: list[dict]) -> tuple[list[Evaluation], list[Suggestion]]:
    """Casa as ofertas com todas as listas do usuário. Devolve as avaliações e as sugestões tocadas."""
    evaluations: list[Evaluation] = []
    touched: dict[int, Suggestion] = {}
    for lst in s.exec(select(ShoppingList).where(ShoppingList.owner_id == user_id)).all():
        items = s.exec(select(ListItem).where(ListItem.list_id == lst.id)).all()
        for item in items:
            if not any(matches(item, o.get("name", "")) for o in offers):
                continue
            ev = evaluate_item(s, user_id, lst, item, offers)
            evaluations.append(ev)
            if ev.changed:
                touched[ev.suggestion_id] = s.get(Suggestion, ev.suggestion_id)
        agg = aggregate_list(s, user_id, lst)
        if agg:
            touched[agg.id] = agg
    s.commit()
    for sg in touched.values():
        s.refresh(sg)
    return evaluations, list(touched.values())


async def _on_promotions(s: Session, event: PromotionsPublished):
    log.info("reavaliando listas para %d ofertas em promoção (%s)", len(event.offers), event.source)
    return reevaluate(s, 1, event.offers)


subscribe(PromotionsPublished, _on_promotions)


# ------------------------------------------------------------------ simulação

async def simulate_promotion(s: Session, api, query: str, discount_pct: float,
                             merchant_id: str | None = None) -> list[dict]:
    """Coloca em promoção o que casa com `query` e devolve as ofertas no formato do evento.

    No simulador a promoção muda o catálogo, então o carrinho criado depois reflete o
    preço. Fora dele não dá para criar promoção no iFood: a busca real fornece os
    itens e o preço promocional é só simulado (o carrinho virá com o preço real).
    """
    factor = 1 - discount_pct / 100
    if api.fake is not None:
        return [dict(o, simulated=False) for o in api.fake.add_promotion(query, factor, merchant_id)]
    from app.scheduler import _location

    loc = _location(s, 1)
    if loc is None:
        raise ValueError("sem endereço salvo para buscar ofertas reais; crie um plano antes")
    refinements = {"merchantId": merchant_id} if merchant_id else None
    body = await api.search_items(query, loc[0], loc[1], refinements=refinements, page_size=10)
    out = []
    for r in body.get("results", []):
        m = r.get("merchant") or {}
        unit = r.get("unitPrice")
        if not unit:
            continue
        out.append({"sku_id": r.get("itemId"), "name": r.get("itemName", ""), "merchant_id": m.get("merchantId"),
                    "merchant": m.get("merchantName"), "unit_price": unit, "price": round(unit * factor, 2),
                    "simulated": True})
    return out


def offers_from_search(body: dict) -> list[dict]:
    """Resultados do `searchOffers` em promoção, no formato do evento (usado pelo job diário)."""
    out = []
    for r in body.get("results", []):
        promo, unit = r.get("promotionalUnitPrice"), r.get("unitPrice")
        if not promo or not unit:
            continue
        m = r.get("merchant") or {}
        out.append({"sku_id": r.get("itemId"), "name": r.get("itemName", ""), "merchant_id": m.get("merchantId"),
                    "merchant": m.get("merchantName"), "unit_price": unit, "price": promo, "simulated": False})
    return out
