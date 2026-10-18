"""Chat com tools: o agente consulta o iFood (simulador) e troca um item no carrinho real."""

import dataclasses
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app import llm
from app.main import app
from app.mcp.fake import ICMC


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def plan(client):
    lst = client.post("/lists", json={"name": "Agente"}).json()
    for raw in ["café 500g", "2 leite integral 1L", "arroz 5kg"]:
        client.post(f"/lists/{lst['id']}/items", json={"raw_text": raw})
    r = client.post(f"/lists/{lst['id']}/plan", json={"lat": ICMC["latitude"], "lng": ICMC["longitude"]})
    plan = client.get(f"/plans/{r.json()['id']}").json()
    assert plan["status"] == "ready", plan.get("error")
    return plan


@pytest.fixture
def scripted(monkeypatch):
    """OpenRouter roteirizado: cada resposta é um dict de mensagem ou uma função do corpo da requisição."""
    calls: list[dict] = []
    replies: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body)
        reply = replies.pop(0)
        message = reply(body) if callable(reply) else reply
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": message}]})

    monkeypatch.setattr(llm, "settings", dataclasses.replace(llm.settings, openrouter_api_key="sk-or-teste",
                                                             llm_enabled=True, llm_provider=""))
    monkeypatch.setattr(llm, "_http", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    return calls, replies


def tool_call(name: str, args: dict, call_id: str = "c1") -> dict:
    return {"content": "", "tool_calls": [{"id": call_id, "type": "function",
                                           "function": {"name": name, "arguments": json.dumps(args)}}]}


def last_tool_result(body: dict) -> dict:
    return json.loads(next(m for m in reversed(body["messages"]) if m["role"] == "tool")["content"])


def test_agent_searches_and_swaps_in_the_real_cart(client, plan, scripted):
    calls, replies = scripted
    cafe = next(i for i in plan["result"]["items"] if i["category"] == "cafe")
    cart = next(c for c in plan["result"]["chosen"]["carts"] if any(ln["item_id"] == cafe["id"] for ln in c["lines"]))

    def search(body):
        seen = last_tool_result(body)
        assert any(ln["itemDaLista"] == cafe["id"] for c in seen["carrinhos"] for ln in c["itens"])
        return tool_call("searchOffers", {"query": "café extraforte", "merchantId": cart["merchant_id"]}, "c2")

    def swap(body):
        found = last_tool_result(body)["resultados"]
        pick = next(r for r in found if "Extraforte" in r["nome"] and r["mercado_no_carrinho"])
        return tool_call("trocarItem", {"itemDaLista": cafe["id"], "itemId": pick["itemId"], "quantidade": 1}, "c3")

    replies.extend([tool_call("verCarrinho", {}), search, swap,
                    {"content": "Troquei o café por um extraforte; o total ficou em R$ 100,00."}])
    out = client.post(f"/plans/{plan['id']}/chat", json={"message": "troca o café por um extraforte"}).json()

    assert out["reply"].startswith("Troquei")
    assert out["steps"][0].startswith("Busquei “café extraforte” no ")
    assert out["steps"][1].startswith("Troquei ")
    names = [ln["name"] for c in out["plan"]["result"]["chosen"]["carts"] for ln in c["lines"]
             if ln["item_id"] == cafe["id"]]
    assert len(names) == 1 and "Extraforte" in names[0]
    assert calls[0]["tools"] and calls[0]["tool_choice"] == "auto"


def test_agent_refuses_invented_ids_and_other_merchants(client, plan, scripted):
    _, replies = scripted
    cafe = next(i for i in plan["result"]["items"] if i["category"] == "cafe")
    replies.extend([
        tool_call("trocarItem", {"itemDaLista": cafe["id"], "itemId": "00000000-0000-0000-0000-000000000000",
                                 "quantidade": 1}),
        lambda body: (last_tool_result_text(body).startswith("Erro: esse itemId não veio")
                      and {"content": "Não achei esse produto."}) or pytest.fail("ID inventado foi aceito"),
    ])
    out = client.post(f"/plans/{plan['id']}/chat", json={"message": "troca o café"}).json()
    assert out["reply"] == "Não achei esse produto." and out["steps"] == []


def test_agent_failure_falls_back_to_the_old_chat(client, plan, monkeypatch):
    monkeypatch.setattr(llm, "settings", dataclasses.replace(llm.settings, openrouter_api_key="sk-or-teste",
                                                             llm_enabled=True, llm_provider=""))
    monkeypatch.setattr(llm, "_http", httpx.AsyncClient(transport=httpx.MockTransport(
        lambda r: httpx.Response(402, json={"error": {"message": "sem créditos"}}))))
    out = client.post(f"/plans/{plan['id']}/chat", json={"message": "por que esse mercado?"}).json()
    assert out["reply"] and out["steps"] == []


def last_tool_result_text(body: dict) -> str:
    return next(m for m in reversed(body["messages"]) if m["role"] == "tool")["content"]
