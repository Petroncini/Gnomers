"""Proatividade por evento: promoção publicada reavalia as listas pelo padrão de compra."""

from datetime import datetime, time, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app import proactive
from app.db import engine, today
from app.main import app
from app.mcp import ifood as ifood_mod
from app.models import ListItem, Order, OrderLine, Plan, Rule, ShoppingList


@pytest.fixture(scope="module")
def client():
    # simulador próprio: as promoções mexem no catálogo e não podem vazar para outros testes
    previous = ifood_mod._instance
    ifood_mod.set_ifood(ifood_mod.Ifood(mode="fake"))
    with TestClient(app) as c:
        yield c
    ifood_mod.set_ifood(previous)


def _buy(s: Session, lst_id: int, item_id: int, days_ago: list[int], price: float) -> None:
    plan = Plan(user_id=1, list_id=lst_id, status="ordered")
    s.add(plan)
    s.commit()
    for d in days_ago:
        when = datetime.combine(today() - timedelta(days=d), time(12, tzinfo=timezone.utc))
        order = Order(plan_id=plan.id, merchant_id="seed", ifood_cart_id="seed", created_at=when)
        s.add(order)
        s.commit()
        s.add(OrderLine(order_id=order.id, list_item_id=item_id, ifood_item_id="seed", unit_price=price, qty=1))
    s.commit()


@pytest.fixture(scope="module")
def setup(client):
    lst = client.post("/lists", json={"name": "Proativa", "recurrence_days": 30}).json()
    ids = {raw: client.post(f"/lists/{lst['id']}/items", json={"raw_text": raw}).json()["id"]
           for raw in ("sabão em pó 1,6kg", "café 500g", "arroz 5kg", "detergente 500ml")}
    with Session(engine) as s:
        _buy(s, lst["id"], ids["sabão em pó 1,6kg"], [35, 5], 27.9)  # comprado há pouco
        _buy(s, lst["id"], ids["café 500g"], [30, 15], 25.9)  # vence hoje
        _buy(s, lst["id"], ids["arroz 5kg"], [25], 40.0)  # uma compra só: usa a recorrência da lista
    return lst, ids


def _evals(r: dict, list_id: int) -> dict[str, dict]:
    return {e["raw_text"]: e for e in r["evaluations"] if e["list_id"] == list_id}


def _promote(client, query: str, pct: float) -> dict:
    r = client.post("/admin/promotions", json={"query": query, "discount_pct": pct})
    assert r.status_code == 200, r.text
    return r.json()


def test_patterns_from_two_purchases_or_list_recurrence(client, setup):
    lst, ids = setup
    by_id = {p["item_id"]: p for p in client.get("/admin/patterns").json()}
    sabao = by_id[ids["sabão em pó 1,6kg"]]["pattern"]
    assert sabao["source"] == "history" and sabao["interval_days"] == 30 and sabao["days_to_due"] == 25
    arroz = by_id[ids["arroz 5kg"]]["pattern"]
    assert arroz["source"] == "list_recurrence" and arroz["days_to_due"] == 5
    detergente = by_id[ids["detergente 500ml"]]["pattern"]
    assert detergente["purchases"] == 0 and detergente["source"] == "list_recurrence"


def test_decide_by_cycle_progress():
    p = proactive.Pattern(item_id=1, list_id=1, purchases=2, last_purchase=today(), interval_days=30,
                          next_due=today() + timedelta(days=25), regularity=0.5, source="history")
    assert proactive.decide(p, 0.2)[0] is None
    assert proactive.decide(p, 0.35)[0] == "stock"
    p.next_due = today() + timedelta(days=10)
    assert proactive.decide(p, 0.2)[0] == "advance"
    assert proactive.decide(p, 0.1)[0] is None


def test_recent_purchase_needs_big_discount(client, setup):
    lst, _ = setup
    small = _evals(_promote(client, "sabão em pó", 20), lst["id"])["sabão em pó 1,6kg"]
    assert small["action"] is None and small["suggestion_id"] is None
    big = _promote(client, "sabão em pó", 40)
    ev = _evals(big, lst["id"])["sabão em pó 1,6kg"]
    assert ev["action"] == "stock"
    sg = next(s for s in big["suggestions"] if s["id"] == ev["suggestion_id"])
    assert sg["kind"] == "promo" and "Estocar" in sg["text"] and sg["data"]["action"] == "stock"


def test_due_item_advances_and_list_aggregates(client, setup):
    lst, _ = setup
    r = _promote(client, "café", 25)
    ev = _evals(r, lst["id"])["café 500g"]
    assert ev["action"] == "advance" and ev["discount_pct"] >= 25
    agg = [s for s in client.get("/suggestions").json() if s["kind"] == "list_promo" and s["list_id"] == lst["id"]]
    assert len(agg) == 1 and agg[0]["data"]["count"] >= 2 and agg[0]["data"]["savings"] > 0


def test_dismissed_offer_is_not_suggested_again(client, setup):
    lst, ids = setup
    sgs = [s for s in client.get("/suggestions").json() if s["list_item_id"] == ids["café 500g"]]
    assert sgs
    client.post(f"/suggestions/{sgs[0]['id']}/dismiss")
    ev = _evals(_promote(client, "café", 25), lst["id"])["café 500g"]
    assert ev["suggestion_id"] is None and "dispensou" in ev["reason"]


def test_blocked_brand_is_never_suggested(client, setup):
    lst, ids = setup
    with Session(engine) as s:
        cat = s.get(ListItem, ids["detergente 500ml"]).category
        s.add(Rule(user_id=1, scope=str(lst["id"]), category=cat, kind="brand_chain", hardness="hard",
                   payload={"order": ["Ypê"], "allow_others": False}))
        s.commit()
    r = _promote(client, "detergente", 30)
    ev = _evals(r, lst["id"])["detergente 500ml"]
    assert ev["action"] == "advance" and "Ypê" in ev["offer"]


def test_list_without_pattern_gets_no_suggestion(client):
    with Session(engine) as s:
        lst = ShoppingList(owner_id=1, name="Sem padrão")
        s.add(lst)
        s.commit()
        s.add(ListItem(list_id=lst.id, raw_text="arroz 5kg", category="arroz", query="arroz", pack_size=5000,
                       pack_unit="g"))
        s.commit()
        lst_id = lst.id
    ev = _evals(_promote(client, "arroz", 30), lst_id)["arroz 5kg"]
    assert ev["action"] is None and "sem padrão" in ev["reason"]
