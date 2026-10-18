"""Provedor OpenRouter com respostas simuladas (sem rede)."""

import dataclasses
import json

import httpx
import pytest

from app import llm


@pytest.fixture
def openrouter(monkeypatch):
    calls: list[dict] = []
    replies: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        reply = replies.pop(0)
        if isinstance(reply, httpx.Response):
            return reply
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": reply}}]})

    monkeypatch.setattr(llm, "settings", dataclasses.replace(llm.settings, openrouter_api_key="sk-or-teste",
                                                             llm_enabled=True, llm_provider=""))
    monkeypatch.setattr(llm, "_http", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    return calls, replies


PARSED = {"query": "leite integral Italac", "intention": "product_exact", "category": "leite", "brand": "Italac",
          "variety_terms": ["integral"], "pack_size": 1000, "pack_unit": "ml", "quantity": 2}


async def test_parse_item_via_openrouter(openrouter):
    calls, replies = openrouter
    replies.append(json.dumps(PARSED))
    out = await llm.parse_item("2 leites integral italac 1L")
    assert out["query"] == "leite integral Italac" and out["quantity"] == 2
    body = calls[0]
    assert body["model"] == "anthropic/claude-haiku-5.5"
    fmt = body["response_format"]["json_schema"]
    assert fmt["strict"] is True and fmt["schema"]["additionalProperties"] is False
    assert set(fmt["schema"]["required"]) == set(PARSED)
    assert body["messages"][0]["role"] == "system"


async def test_invalid_json_is_retried_with_the_error(openrouter):
    calls, replies = openrouter
    replies.extend(['{"query": "leite"}', json.dumps(PARSED)])
    out = await llm.parse_item("2 leites integral italac 1L")
    assert out["brand"] == "Italac"
    assert len(calls) == 2 and "não passou na validação" in calls[1]["messages"][-1]["content"]


async def test_provider_error_falls_back_to_heuristics(openrouter):
    _, replies = openrouter
    replies.append(httpx.Response(402, json={"error": {"message": "sem créditos"}}))
    out = await llm.parse_item("1 kilo de arroz branco")
    assert out["category"] == "arroz" and out["pack_size"] == 1000  # veio da heurística


async def test_strong_tasks_use_opus(openrouter):
    calls, replies = openrouter
    replies.append(json.dumps({"answer": "Porque deu o menor total real."}))
    await llm.explain("por que esse mercado?", {"chosen": {"carts": []}})
    assert calls[0]["model"] == "anthropic/claude-opus-5.5"


def test_strict_schema_keeps_field_named_title():
    from pydantic import BaseModel

    class Card(BaseModel):
        title: str
        default: int = 0

    schema = llm.strict_schema(Card)
    assert set(schema["properties"]) == {"title", "default"} and schema["required"] == ["title", "default"]


async def test_search_plan_output_is_validated(openrouter):
    """O LLM decide as buscas, mas o código impõe o formato: alternativa com o produto, sem tamanho nas exigências."""
    _, replies = openrouter
    replies.append(json.dumps({"specific": "café moído", "broad": "café", "alternatives": ["torrado e moído", "café torrado e moído"],
                               "must_have": ["moído", "mais de 500g"]}))
    plan = await llm.plan_searches("500g café moído de filtro", "cafe")
    assert plan["alternatives"] == ["café torrado e moído"]  # "torrado e moído" sem o produto é descartada
    assert plan["must_have"] == ["moído"]


async def test_judge_decides_product_and_variety_together(openrouter):
    calls, replies = openrouter
    replies.append(json.dumps({"products": [
        {"index": 0, "verdict": "exato", "reason": ""},
        {"index": 1, "verdict": "substituto", "reason": "não é sem açúcar"},
        {"index": 2, "verdict": "nao_serve", "reason": "é leite de aveia"}]}))
    out = await llm.judge_products("1 granola sem açucar (>500g)", "granola", ["sem açúcar"],
                                   ["Granola Zero Açúcares Mãe Terra 250g", "Granola Kobber Tradicional", "Nude Leite de Aveia 1l"])
    assert [v["verdict"] for v in out] == ["exato", "substituto", "nao_serve"]
    asked = calls[0]["messages"][-1]["content"]
    assert "1 granola sem açucar" in asked and "sem açúcar" in asked  # o pedido inteiro, numa chamada só


async def test_judge_fallback_without_llm():
    # LLM desligado nos testes: as regras viram o plano B do juiz
    out = await llm.judge_products("2 leite italac integral", "leite", ["integral"],
                                   ["Leite Integral Italac 1l", "Leite em Pó Integral Italac 400g", "Leite Desnatado Italac 1l"])
    assert [v["verdict"] for v in out] == ["exato", "nao_serve", "substituto"]
