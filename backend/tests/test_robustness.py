"""Plano interrompido, alterações simultâneas no mesmo plano e ritmo das chamadas ao iFood."""

import asyncio
import contextlib

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app import actions
from app.db import engine, fail_interrupted_plans
from app.main import app
from app.mcp.fake import ICMC
from app.mcp.ifood import Ifood, RateLimiter
from app.models import Plan


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def plan(client):
    lst = client.post("/lists", json={"name": "Robustez"}).json()
    for raw in ["café 500g", "2 leite integral 1L", "arroz 5kg", "feijão carioca 1kg"]:
        client.post(f"/lists/{lst['id']}/items", json={"raw_text": raw})
    r = client.post(f"/lists/{lst['id']}/plan", json={"lat": ICMC["latitude"], "lng": ICMC["longitude"]})
    plan = client.get(f"/plans/{r.json()['id']}").json()
    assert plan["status"] == "ready", plan.get("error")
    return plan


def test_interrupted_plans_fail_on_startup(plan):
    with Session(engine) as s:
        stuck = Plan(user_id=1, list_id=plan["list_id"], status="running")
        s.add(stuck)
        s.commit()
        s.refresh(stuck)
    assert fail_interrupted_plans() >= 1
    with Session(engine) as s:
        again = s.get(Plan, stuck.id)
        assert again.status == "failed" and "reiniciou" in again.error


async def _remove_two_at_once(plan: dict, monkeypatch) -> list[str]:
    original = Ifood.remove_item

    async def slow_remove(self, cart_id, cart_item_id):
        await asyncio.sleep(0.05)  # o iFood leva ~1,5 s; sem espera as duas requisições nem se cruzam
        return await original(self, cart_id, cart_item_id)

    monkeypatch.setattr(Ifood, "remove_item", slow_remove)
    cards = {c["item_id"]: c["id"] for c in plan["result"]["decisions"] if c["item_id"] is not None}
    lines = [ln for c in plan["result"]["chosen"]["carts"] for ln in c["lines"]]
    with_card = [ln for ln in lines if ln["item_id"] in cards][:2]
    assert len(with_card) == 2, "o plano de teste precisa de dois itens com cartão"
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        await asyncio.gather(*(ac.post(f"/plans/{plan['id']}/decisions/{cards[ln['item_id']]}",
                                       json={"action": "remove"}) for ln in with_card))
        after = (await ac.get(f"/plans/{plan['id']}")).json()
    left = {ln["item_id"] for c in after["result"]["chosen"]["carts"] for ln in c["lines"]}
    return [ln["item_id"] for ln in with_card if ln["item_id"] in left]


async def test_concurrent_changes_to_a_plan_are_not_lost(plan, monkeypatch):
    assert await _remove_two_at_once(plan, monkeypatch) == []  # os dois saíram do carrinho


async def test_without_the_lock_one_change_is_lost(client, monkeypatch):
    """Prova que o teste acima mede algo: sem a trava, a segunda gravação apaga a primeira."""
    lst = client.post("/lists", json={"name": "Sem trava"}).json()
    for raw in ["café 500g", "2 leite integral 1L", "arroz 5kg", "feijão carioca 1kg"]:
        client.post(f"/lists/{lst['id']}/items", json={"raw_text": raw})
    r = client.post(f"/lists/{lst['id']}/plan", json={"lat": ICMC["latitude"], "lng": ICMC["longitude"]})
    plan = client.get(f"/plans/{r.json()['id']}").json()

    @contextlib.asynccontextmanager
    async def no_lock():
        yield

    monkeypatch.setattr(actions, "plan_lock", lambda plan_id: no_lock())
    assert len(await _remove_two_at_once(plan, monkeypatch)) == 1


async def test_rate_limiter_waits_for_the_window():
    now = [0.0]
    slept: list[float] = []

    async def fake_sleep(seconds):
        slept.append(seconds)
        now[0] += seconds

    limiter = RateLimiter(3, 60.0, clock=lambda: now[0], sleep=fake_sleep)
    for t in (0, 1, 2):
        now[0] = t
        await limiter.acquire()
    assert slept == []
    now[0] = 10
    await limiter.acquire()  # a 4ª chamada espera a 1ª sair da janela (t = 60)
    assert slept == [50.0] and now[0] == 60
