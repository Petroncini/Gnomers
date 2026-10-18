"""Fluxo completo da demo contra o simulador do MCP (modo fake, sem LLM)."""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.mcp.fake import ICMC

ITEMS = ["2 leite integral 1L", "arroz 5kg", "feijão carioca 1kg", "café 500g", "detergente 500ml",
         "papel higiênico 12 rolos", "12 ovos", "açúcar refinado 1kg", "óleo de soja 900ml",
         "macarrão espaguete 500g"]


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def ready_plan(client):
    lst = client.post("/lists", json={"name": "Compra do mês", "recurrence_days": 30}).json()
    for raw in ITEMS:
        r = client.post(f"/lists/{lst['id']}/items", json={"raw_text": raw})
        assert r.status_code == 200, r.text
    client.post("/rules", json={"kind": "brand_chain", "category": "detergente",
                                "payload": {"order": ["Ypê", "Limpol"], "allow_others": True}})
    r = client.post(f"/lists/{lst['id']}/plan", json={"lat": ICMC["latitude"], "lng": ICMC["longitude"]})
    plan_id = r.json()["id"]
    plan = client.get(f"/plans/{plan_id}").json()
    assert plan["status"] == "ready", plan.get("error")
    return lst, plan


def test_location_picks_saved_address(client):
    r = client.post("/location", json={"lat": ICMC["latitude"] + 0.0005, "lng": ICMC["longitude"]}).json()
    assert r["address"]["alias"] == "Faculdade"
    far = client.post("/location", json={"lat": -23.55, "lng": -46.63}).json()
    assert far["needs_choice"] and not far["needs_address"]
    assert far["nearby"][0]["alias"] == "Faculdade"
    # margem de erro grande do navegador amplia o raio
    coarse = client.post("/location", json={"lat": ICMC["latitude"] + 0.006, "lng": ICMC["longitude"], "accuracy": 900}).json()
    assert coarse["address"]["alias"] == "Faculdade"


def test_plan_has_real_total_and_cards(ready_plan):
    _, plan = ready_plan
    res = plan["result"]
    chosen = res["chosen"]
    assert chosen["ok"] and chosen["total"] > 0
    lines = [ln for c in chosen["carts"] for ln in c["lines"]]
    assert len(lines) + len(res["missing"]) == len(ITEMS)
    # o total vem do carrinho real: produtos + frete − cupom
    for cart in chosen["carts"]:
        s = cart["summary"]
        assert abs(s["subtotal"] + s["delivery"] + s["fees"] - s["voucher"] - s["total"]) < 0.02
        assert cart["payment"]["kind"] == "PIX"
    assert res["decisions"], "deveria haver pelo menos um cartão"
    assert "candidates" not in res  # a lista de candidatos fica só no servidor


def test_detergent_follows_brand_chain(ready_plan):
    _, plan = ready_plan
    lines = {ln["name"]: ln for c in plan["result"]["chosen"]["carts"] for ln in c["lines"]}
    detergent = [n for n in lines if n.lower().startswith("detergente")]
    assert detergent
    assert any(b in detergent[0] for b in ("Ypê", "Limpol"))


def test_events_stream(client, ready_plan):
    _, plan = ready_plan
    with client.stream("GET", f"/plans/{plan['id']}/events") as r:
        body = "".join(r.iter_text())
    for stage in ("address", "discovery", "optimize", "carts", "done"):
        assert f'"stage": "{stage}"' in body


def test_decision_and_chat_rule(client, ready_plan):
    lst, plan = ready_plan
    cards = [c for c in plan["result"]["decisions"] if c.get("item_id")]
    assert cards
    card = next((c for c in cards if c.get("alternatives")), cards[0])
    before = plan["result"]["chosen"]["total"]
    if card.get("alternatives"):
        alt = card["alternatives"][0]
        same_merchant = any(c["merchant_id"] == alt["merchant_id"] for c in plan["result"]["chosen"]["carts"])
        if same_merchant and alt.get("brand"):
            r = client.post(f"/plans/{plan['id']}/chat",
                            json={"card_id": card["id"], "message": f"prefiro {alt['brand']}"}).json()
            assert r["proposed_rule"]["kind"] == "brand_chain"
            saved = client.post("/rules", json={**{k: r["proposed_rule"][k] for k in ("kind", "category", "payload", "hardness")},
                                                "scope": "global", "source": "chat"}).json()
            assert saved["source"] == "chat"
            rules = client.get("/rules").json()
            assert any(x["id"] == saved["id"] for x in rules)
            after = client.get(f"/plans/{plan['id']}").json()["result"]["chosen"]
            names = [ln["name"] for c in after["carts"] for ln in c["lines"]]
            assert alt["name"] in names
            assert after["total"] != before or alt["price"] == card["chosen"]["price"]
            return
    r = client.post(f"/plans/{plan['id']}/decisions/{card['id']}", json={"action": "accept"}).json()
    assert any(c["id"] == card["id"] and c["status"] == "resolved" for c in r["plan"]["result"]["decisions"])


def test_checkout_and_tracking(client, ready_plan):
    _, plan = ready_plan
    r = client.post(f"/plans/{plan['id']}/checkout", json={"accept_changes": True}).json()
    assert r["status"] == "ordered", r
    assert all(o["pix"] for o in r["orders"])
    order_id = r["orders"][0]["id"]
    t = client.get(f"/orders/{order_id}/track").json()
    assert t["status"]["code"] == "ORDER_CREATED" and t["retry_after_s"] == 5
    again = client.get(f"/orders/{order_id}/track").json()
    assert again["cached"], "deve respeitar o intervalo do retryMessage"
    # segunda tentativa de pedir o mesmo carrinho é recusada
    assert client.post(f"/plans/{plan['id']}/checkout", json={}).status_code == 409


def test_simulate_days_creates_suggestion(client, ready_plan):
    r = client.post("/admin/simulate-days", json={"days": 28}).json()
    kinds = {c["kind"] for c in r["created"]}
    assert "list_due" in kinds
    assert client.get("/suggestions").json()


def test_scheduled_delivery_picks_covering_slot(client, ready_plan):
    from datetime import date, timedelta

    lst, _ = ready_plan
    when = f"{(date.today() + timedelta(days=1)).isoformat()}T10:00"
    r = client.post(f"/lists/{lst['id']}/plan", json={"lat": ICMC["latitude"], "lng": ICMC["longitude"], "deliver_at": when})
    plan = client.get(f"/plans/{r.json()['id']}").json()
    assert plan["status"] == "ready", plan.get("error")
    assert plan["result"]["deliver_at"] == when
    for cart in plan["result"]["chosen"]["carts"]:
        slot = cart["delivery"]["slot"]
        assert slot and slot["from"] <= f"{when}:00" < slot["to"]
    assert client.post(f"/lists/{lst['id']}/plan", json={"deliver_at": "segunda"}).status_code == 422


def test_choose_delivery_rules():
    from app.pipeline.materialize import choose_delivery

    methods = [
        {"deliveryMethodId": "FAST_DELIVERY", "deliveryMethodMode": "DELIVERY", "fee": 3.0},
        {"deliveryMethodId": "DEFAULT", "deliveryMethodMode": "DELIVERY", "fee": 0.0, "timeslots": [
            {"timeslotId": "a", "from": "2026-10-19T09:00:00", "to": "2026-10-19T11:00:00"},
            {"timeslotId": "b", "from": "2026-10-19T10:00:00", "to": "2026-10-19T12:00:00"}]},
        {"deliveryMethodId": "TAKEOUT", "deliveryMethodMode": "TAKEOUT", "fee": 0.0},
    ]
    # formato real do sandbox (17/10): DEFAULT só com horários agendados
    m, slot, _ = choose_delivery(methods, "2026-10-19T10:00")
    assert m["deliveryMethodId"] == "DEFAULT" and slot["timeslotId"] == "b"
    m, slot, _ = choose_delivery(methods, None)
    assert m["deliveryMethodId"] == "DEFAULT" and slot["timeslotId"] == "a"
    m, slot, problem = choose_delivery(methods, "2026-10-21T22:00")
    assert m is None and "21/10 22:00" in problem
