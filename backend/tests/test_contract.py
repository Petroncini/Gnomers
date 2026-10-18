"""O contrato commitado precisa bater com o código, e todas as rotas precisam respeitá-lo."""

import pytest
from fastapi.testclient import TestClient

from app.contract import CONTRACT, render
from app.main import app
from app.mcp.fake import ICMC


def test_committed_contract_is_up_to_date():
    assert CONTRACT.exists(), "rode `uv run python -m app.contract`"
    assert CONTRACT.read_text(encoding="utf-8") == render(), (
        "contract/openapi.json está desatualizado: rode `uv run python -m app.contract`, "
        "depois `npm run gen:api` no front, e combine a mudança com quem cuida do front")


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_every_route_matches_its_response_model(client):
    """Passa por todas as rotas no simulador; o FastAPI valida cada resposta contra o schema."""
    ok = lambda r: (r.status_code < 400, r.status_code, r.text[:300])  # noqa: E731
    assert ok(client.get("/health"))[0]
    assert ok(client.get("/auth/status"))[0]
    assert ok(client.get("/me"))[0]
    assert ok(client.patch("/me", json={"quality_profile": 0.6}))[0]
    assert ok(client.post("/items/parse", json={"text": "2 leite integral Italac 1L"}))[0]
    lst = client.post("/lists", json={"name": "Contrato", "recurrence_days": 7}).json()
    item = client.post(f"/lists/{lst['id']}/items", json={"raw_text": "café 500g"}).json()
    client.post(f"/lists/{lst['id']}/items", json={"raw_text": "arroz 5kg"})
    client.post(f"/lists/{lst['id']}/items", json={"raw_text": "detergente 500ml"})
    assert ok(client.patch(f"/lists/{lst['id']}/items/{item['id']}", json={"brand": "Pilão"}))[0]
    assert ok(client.patch(f"/lists/{lst['id']}", json={"name": "Contrato 2"}))[0]
    assert ok(client.get(f"/lists/{lst['id']}"))[0]
    assert ok(client.get("/lists"))[0]
    rule = client.post("/rules", json={"kind": "variety", "category": "arroz", "payload": {"terms": ["agulhinha"]}}).json()
    assert ok(client.patch(f"/rules/{rule['id']}", json={"active": False}))[0]
    assert ok(client.get("/rules"))[0]
    assert ok(client.post("/rules/compile", json={"text": "detergente nunca Minuano", "category": "detergente"}))[0]
    assert ok(client.get("/addresses"))[0]
    assert ok(client.post("/location", json={"lat": ICMC["latitude"], "lng": ICMC["longitude"], "accuracy": 50}))[0]
    geo = client.post("/addresses", json={"address": "Rua Episcopal 100, São Carlos", **{"lat": ICMC["latitude"], "lng": ICMC["longitude"]}}).json()
    assert geo["status"] == "confirm" and geo["candidates"]
    made = client.post("/addresses", json={"address": "Rua Episcopal 100, São Carlos", "alias": "Trabalho",
                                           "candidate_id": geo["candidates"][0]["candidateId"]})
    assert ok(made)[0] and made.json()["status"] == "created"

    plan = client.post(f"/lists/{lst['id']}/plan", json={"lat": ICMC["latitude"], "lng": ICMC["longitude"]}).json()
    view = client.get(f"/plans/{plan['id']}")
    assert ok(view)[0] and view.json()["status"] == "ready", view.text[:300]
    cards = view.json()["result"]["decisions"]
    card = next((c for c in cards if c["item_id"] is not None), None)
    if card:
        assert ok(client.post(f"/plans/{plan['id']}/decisions/{card['id']}", json={"action": "accept"}))[0]
        assert ok(client.post(f"/plans/{plan['id']}/chat", json={"card_id": card["id"], "message": "por que esse mercado?"}))[0]
    assert ok(client.post(f"/plans/{plan['id']}/chat", json={"message": "quanto economizei?"}))[0]
    again = client.post(f"/plans/{plan['id']}/replan")
    assert ok(again)[0]
    assert client.get(f"/plans/{again.json()['id']}").json()["status"] in ("ready", "failed")
    done = client.post(f"/plans/{plan['id']}/checkout", json={"accept_changes": True})
    assert ok(done)[0]
    order_id = done.json()["orders"][0]["id"]
    assert ok(client.get(f"/orders/{order_id}/track"))[0]
    assert ok(client.post("/admin/simulate-days", json={"days": 7}))[0]
    assert ok(client.get("/admin/patterns"))[0]
    promo = client.post("/admin/promotions", json={"query": "café", "discount_pct": 30})
    assert ok(promo)[0] and promo.json()["offers"]
    sugs = client.get("/suggestions")
    assert ok(sugs)[0]
    for sg in sugs.json():
        assert ok(client.post(f"/suggestions/{sg['id']}/dismiss"))[0]
    assert ok(client.get("/debug/calls"))[0]
    assert ok(client.delete(f"/rules/{rule['id']}"))[0]
    assert ok(client.delete(f"/lists/{lst['id']}/items/{item['id']}"))[0]
