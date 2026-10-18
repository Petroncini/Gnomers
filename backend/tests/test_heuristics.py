import pytest

from app.heuristics import normalize_product, parse_item, parse_pack
from app.mcp.client import normalize_error


@pytest.mark.parametrize("text,expected", [
    ("Leite Uht Integral com Tampa Italac 1l", (1000.0, "ml")),
    ("Arroz Branco Tipo 1 Camil 5kg", (5000.0, "g")),
    ("Sabão em Pó Omo 1,6kg", (1600.0, "g")),
    ("Papel Higiênico Neve 12 Rolos", (12.0, "un")),
    ("Iogurte 6x170g", (1020.0, "g")),
    ("Banana Prata", (None, None)),
])
def test_parse_pack(text, expected):
    assert parse_pack(text) == expected


def test_parse_item_fields():
    out = parse_item("2 leite integral Italac 1L")
    assert out["quantity"] == 2 and out["brand"] == "Italac" and out["intention"] == "product_exact"
    assert out["category"] == "leite" and out["pack_size"] == 1000 and "integral" in out["variety_terms"]
    assert "1L" not in out["query"]


def test_parse_item_size_is_not_quantity():
    out = parse_item("1kg de feijão carioca")
    assert out["quantity"] == 1 and out["pack_size"] == 1000 and out["category"] == "feijao"
    assert out["query"] == "feijão carioca"


def test_derivatives_do_not_match():
    assert normalize_product("leite", "Leite Uht Integral Italac 1l")["matches"]
    assert not normalize_product("leite", "Leite de Coco Integral Cocomix 200ml")["matches"]
    assert not normalize_product("leite", "Biscoito Integral Sabor Leite e Aveia Belvita 75g")["matches"]
    assert normalize_product("papel higienico", "Papel Higiênico Folha Dupla Neve 12 Rolos")["matches"]


def test_three_error_shapes():
    od = normalize_error("searchOffers", {"errors": {"code": "INVALID_QUERY", "message": "m", "retryable": True}})
    cart = normalize_error("createCart", {"error": {"code": "MERCHANT_CLOSED", "message": "m",
                                                    "userFriendlyMessage": "fechado"}})
    track = normalize_error("trackOrder", {"errorType": "ResourceNotFoundError", "code": "ORDER_NOT_FOUND",
                                           "message": "m", "retryable": False})
    assert (od.code, od.retryable) == ("INVALID_QUERY", True)
    assert (cart.code, cart.user_message) == ("MERCHANT_CLOSED", "fechado")
    assert track.code == "ORDER_NOT_FOUND" and not track.retryable
    assert normalize_error("getCart", {"cartId": "x", "summary": {}}) is None


def test_unwrap_success_data_envelope():
    from app.mcp.client import McpToolError, unwrap

    # formato real do createAddress no sandbox (17/10), diferente da doc
    real = {"success": True, "data": {"matchConfidence": "MULTIPLE", "candidates": [{"candidateId": "b3262b4d"}]},
            "agentHints": {"confirmAddressGuidance": "..."}}
    out = unwrap("createAddress", real)
    assert out["candidates"][0]["candidateId"] == "b3262b4d"
    assert out["agentHints"]["confirmAddressGuidance"] == "..."
    # respostas sem envelope passam intactas
    plain = {"results": [], "agentHints": {}}
    assert unwrap("listAddresses", plain) is plain
    with pytest.raises(McpToolError):
        unwrap("createAddress", {"success": False, "data": None, "message": "falhou"})


def test_kilo_is_a_size():
    out = parse_item("1 kilo de arroz branco")
    assert out["category"] == "arroz" and out["pack_size"] == 1000 and out["quantity"] == 1


@pytest.mark.parametrize("name,derivative", [
    ("Café Com Leite Solúvel 3 Corações Sache 20g", True),        # caso real, Extra, 18/10
    ("Café Cappuccino Lor Premium Avelã Sachet 17,8g", True),     # caso real, Pão de Açúcar
    ("Café Individual 3 Corações Tradicional Drip Coffee", True),  # caso real, Savegnago
    ("Café Tradicional 3 Corações 500g", False),
    ("Café Torrado e Moído Pilão 500g", False),
])
def test_coffee_derivatives(name, derivative):
    from app.heuristics import is_derivative

    assert is_derivative("cafe", name) is derivative


def test_effective_fee_uses_promotional_fee():
    from app.pipeline.discovery import effective_fee

    # sandbox 18/10: fee 6.49 + promotionalFee 0 virou frete grátis no carrinho do Extra
    assert effective_fee({"deliveryMethodId": "DEFAULT", "fee": 6.49, "promotionalFee": 0}) == 0
    assert effective_fee({"deliveryMethodId": "DEFAULT", "fee": 10.99, "promotionalFee": 10.99}) == 10.99
    assert effective_fee({"deliveryMethodId": "DEFAULT", "fee": 8.0, "promotionalFee": None}) == 8.0


def test_upstream_internal_error_keeps_its_message():
    from app.mcp.client import normalize_error

    # resposta real do setCartDelivery no sandbox em 18/10, com isError
    body = {"code": "INTERNAL", "message": "An internal failure at an upstream service prevented the request from completing.",
            "agent_instruction": "Do not retry immediately. Report the failure and continue without this tool."}
    err = normalize_error("setCartDelivery", body)
    assert err.code == "INTERNAL" and "upstream" in err.message and "continue without" in err.user_message
    assert normalize_error("trackOrder", {"status": {"code": "DELIVERED", "description": "x"}, "statusHistory": []}) is None




def test_search_text_helpers():
    from app.heuristics import clean_query, core_query, has_term, query_variants

    # casos reais de 18/10
    assert clean_query("feijão cru em grão") == "feijão"
    assert clean_query("granola sem açúcar (>500g)") == "granola sem açúcar"
    assert core_query("feijao", "feijão cru em grão") == "feijão"
    assert core_query("leite", "leite italac integral", "Italac") == "leite Italac"
    assert query_variants("granola sem açúcar")[0] == "granola zero açúcar"
    assert query_variants("leite italac integral") == []
    assert has_term("Granola Tradicional Zero Açúcares Mãe Terra 250g", "sem açúcar")
    assert not has_term("Granola Kobber 250g Tradicional", "sem açúcar")
