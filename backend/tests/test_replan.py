"""Casos reais de 18/10: troca que deixa a divisão sem sentido, derivados aceitos pelo LLM e SKU sem tamanho."""

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.ai import llm
from app.cart import changes
from app.core.db import engine
from app.core.models import Plan
from app.main import app
from app.pipeline import normalize
from app.pipeline.discovery import Offer
from app.preferences.rules import Resolved

A, B = "aaaaaaaa-0000-4000-8000-000000000001", "bbbbbbbb-0000-4000-8000-000000000002"


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def cand(item, sku, merchant, name, price, size):
    return {"sku_id": sku, "item_id": item, "merchant_id": merchant, "name": name, "brand": None, "price": price,
            "size": size, "reasons": [], "substitution_penalty": 0, "note_penalty": 0}


def test_swap_that_kills_the_split_suggests_replan(client):
    lst = client.post("/lists", json={"name": "Granola"}).json()
    # plano 13: arroz/leite/café no B; granola 1 kg barata no A justificava dividir
    result = {
        "items": [{"id": 1, "raw_text": "granola", "category": "granola", "query": "granola", "required": 500, "unit": "g"},
                  {"id": 2, "raw_text": "arroz", "category": "arroz", "query": "arroz", "required": 1000, "unit": "g"}],
        "merchants": {A: {"name": "Arco Íris", "delivery_fee": 7.49, "minimum_order": 30},
                      B: {"name": "Savegnago", "delivery_fee": 10.99, "minimum_order": 30}},
        "candidates": {"1": [cand(1, "g1kg", A, "Granola 1kg", 32.98, 1000), cand(1, "g250a", A, "Granola 250g", 27.95, 250),
                             cand(1, "g250b", B, "Granola 250g", 16.40, 250)],
                       "2": [cand(2, "arroz", B, "Arroz 1kg", 25.15, 1000)]},
        "single_total": 73.28,
        "chosen": {"index": 0, "merchants": [A, B], "ok": True, "total": 138.66, "estimated_total": 111.76, "missing": [],
                   "errors": [], "carts": [
                       {"merchant_id": A, "merchant_name": "Arco Íris", "cart_id": "c1", "blocking": False,
                        "summary": {"total": 65.38}, "lines": [{"item_id": 1, "sku_id": "g250a", "quantity": 2,
                                                                 "user_action": "swapped"}]},
                       {"merchant_id": B, "merchant_name": "Savegnago", "cart_id": "c2", "blocking": False,
                        "summary": {"total": 73.28}, "lines": [{"item_id": 2, "sku_id": "arroz", "quantity": 1}]}]},
        "decisions": [{"id": 0, "type": "split", "status": "open", "text": "Dividi e economizei", "item_id": None}],
    }
    with Session(engine) as s:
        plan = Plan(user_id=1, list_id=lst["id"], status="ready", result=result)
        s.add(plan)
        s.commit()
        s.refresh(plan)
        assert changes.pins_from(result) == {"1": {"size": 250}}
        best = changes.estimate_replan(s, plan, result)
        assert best.merchants == [B]  # 2 x 250 g no Savegnago, tudo num mercado só
        changes._update_replan_card(s, plan, result)
    cards = {c["type"]: c for c in result["decisions"]}
    assert cards["replan"]["status"] == "open" and cards["replan"]["saving"] > 50
    assert "Savegnago" in cards["replan"]["text"]
    assert "deixou de compensar" in cards["split"]["text"]


async def _build(monkeypatch, names_sizes, spec_kw, llm_matches=True):
    async def fake_normalize(category, query, names):
        from app.ai.heuristics import normalize_product
        out = [normalize_product(category, n, query) for n in names]
        for o in out:
            o["matches"] = llm_matches  # o LLM às vezes aceita qualquer coisa
        return out

    monkeypatch.setattr(llm, "normalize_products", fake_normalize)
    from app.core.db import init_db
    init_db()
    spec = normalize.ItemSpec(id=99, raw_text="x", query=spec_kw["query"], category=spec_kw["category"], brand=None,
                              variety_terms=[], pack_size=spec_kw.get("pack_size"), pack_unit=spec_kw.get("pack_unit"),
                              quantity=spec_kw.get("quantity", 1), rules=spec_kw.get("rules", Resolved()))
    offers = [Offer(99, f"{i:08d}-0000-4000-8000-{i:012d}", A, name, None, price, price, None, "fase1")
              for i, (name, price) in enumerate(names_sizes, start=900)]
    with Session(engine) as s:
        return await normalize.build(s, [spec], offers)


async def test_llm_cannot_turn_powdered_milk_into_milk(monkeypatch):
    out = await _build(monkeypatch, [("Leite em Pó Integral Instantâneo Italac Lata 400g", 22.98),
                                     ("Leite Integral Italac 1l", 5.79)],
                       {"query": "leite italac integral", "category": "leite", "quantity": 2})
    names = [info.name for info in out.skus.values()]
    assert names == ["Leite Integral Italac 1l"]


async def test_sku_without_size_is_not_assumed_to_be_the_requested_size(monkeypatch):
    out = await _build(monkeypatch, [("Café Tradicional Pilão Almofada", 1.49),
                                     ("Café Tradicional 3 Corações 500g", 22.49)],
                       {"query": "café", "category": "cafe", "pack_size": 500, "pack_unit": "g"})
    assert [info.name for info in out.skus.values()] == ["Café Tradicional 3 Corações 500g"]
    assert out.rejected[99]["tamanho desconhecido"] == 1


async def test_exact_quantity_rule_drops_bigger_pack(monkeypatch):
    rules = Resolved(quantity_exact=True, max_overbuy=1.1)
    out = await _build(monkeypatch, [("Granola Sem Açúcar 1kg", 32.98), ("Granola Sem Açúcar 250g", 16.40)],
                       {"query": "granola sem açúcar", "category": "granola", "pack_size": 500, "pack_unit": "g",
                        "rules": rules})
    assert [info.name for info in out.skus.values()] == ["Granola Sem Açúcar 250g"]


def test_note_about_exact_quantity_counts_as_rule():
    from app.preferences.rules import resolve

    class R:
        id, scope, category, kind, hardness, source, confidence, active = 1, "global", "granola", "note", "soft", "chat", 1, True
        payload = {"text": "Preferir a quantidade exata pedida, mesmo que fique mais caro"}

    assert resolve([R()], "granola", None).quantity_exact


async def test_tiny_packs_do_not_cover_a_big_request(monkeypatch):
    out = await _build(monkeypatch, [("Café Torrado e Moído Pilão Pacote 10g", 1.49),
                                     ("Café Torrado e Moído Tradicional 3 Corações 500g", 22.49)],
                       {"query": "café moído", "category": "cafe", "pack_size": 500, "pack_unit": "g"})
    assert [info.name for info in out.skus.values()] == ["Café Torrado e Moído Tradicional 3 Corações 500g"]
    assert out.rejected[99].get("embalagem pequena demais") == 1
