"""Simulador local do MCP do iFood (modo `fake`).

Imita o formato real das respostas observado no sandbox em 17/10/2026 (itens com
`itemEan`, `merchant.minimumOrderAmount`, facets com contagem, `promotionalUnitPrice`
igual ao `unitPrice` quando não há promoção). Os mercados e alguns produtos vêm de
uma busca real em São Carlos; preços e o restante do catálogo são gerados de forma
determinística. Serve para os testes do pipeline e para ensaiar a demo sem rede.
"""

from __future__ import annotations

import base64
import random
import time
import uuid
from typing import Any

from app.heuristics import singular
from app.mcp.client import McpToolError
from app.rules import norm

ICMC = {"latitude": -22.0068, "longitude": -47.8954}

MERCHANTS = [
    # merchantId, nome, frete, pedido mínimo, distância, eta(min, max), fator de preço
    ("782805bb-66e0-4d8d-8e86-5befa3b5d7f8", "Carrefour Bairro - São Carlos 2", 8.49, 30, 0.79, (54, 74), 0.97),
    ("6af300f4-e672-43ca-8ae8-9ee96a501894", "Extra - São Carlos", 7.49, 60, 0.77, (45, 65), 1.02),
    ("8ca75d1c-bd6a-4cbf-bcdd-0cd1d8bdf1c2", "Savegnago - Shopping Passeio", 9.49, 60, 0.97, (50, 70), 0.93),
    ("75a8e0a0-2766-48e7-990f-5d551d441dbb", "Supermercados Arco Íris Express", 6.99, 30, 0.84, (51, 61), 1.05),
    ("2454a662-ab7d-4cb8-91c9-c69720983886", "Atacadão São Carlos", 19.99, 90, 4.67, (90, 120), 0.85),
]

# nome, marca, ean, preço base, taxonomia, tags dietéticas
PRODUCTS = [
    ("Leite Uht Integral com Tampa Italac 1l", "Italac", "7898080640611", 5.71, "LEITE_TRADICIONAL", []),
    ("Leite Integral Piracanjuba 1l", "Piracanjuba", "7898215151708", 5.55, "LEITE_TRADICIONAL", []),
    ("Leite Uht Parmalat Integral 1l", "Parmalat", "7896034610017", 6.29, "LEITE_TRADICIONAL", []),
    ("Leite Longa Vida Qualitá Integral Caixa 1l", "Qualitá", "7895000318636", 5.29, "LEITE_TRADICIONAL", []),
    ("Leite Uht Desnatado Italac 1l", "Italac", "7898080640628", 5.49, "LEITE_TRADICIONAL", []),
    ("Leite Zero Lactose Integral Piracanjuba 1l", "Piracanjuba", "7898215152217", 7.19, "LEITE_TRADICIONAL", ["LAC_FREE"]),
    ("Leite de Coco Integral Cocomix 200ml", "Cocomix", "7898975650367", 3.69, "LEITE_DE_COCO", ["VEGAN"]),
    ("Arroz Branco Tipo 1 Camil 5kg", "Camil", "7896006711117", 27.90, "ARROZ", ["VEGAN"]),
    ("Arroz Agulhinha Tipo 1 Tio João 5kg", "Tio João", "7893500020134", 31.49, "ARROZ", ["VEGAN"]),
    ("Arroz Branco Prato Fino 5kg", "Prato Fino", "7896041100075", 34.90, "ARROZ", ["VEGAN"]),
    ("Arroz Integral Camil 1kg", "Camil", "7896006744115", 8.99, "ARROZ", ["VEGAN"]),
    ("Feijão Carioca Tipo 1 Camil 1kg", "Camil", "7896006751410", 8.49, "FEIJAO", ["VEGAN"]),
    ("Feijão Carioca Kicaldo 1kg", "Kicaldo", "7896283000034", 7.99, "FEIJAO", ["VEGAN"]),
    ("Feijão Preto Camil 1kg", "Camil", "7896006751427", 8.99, "FEIJAO", ["VEGAN"]),
    ("Café Torrado e Moído Tradicional Pilão 500g", "Pilão", "7896089012408", 24.90, "CAFE", ["VEGAN"]),
    ("Café Extraforte Pilão 500g", "Pilão", "7896089012415", 25.90, "CAFE", ["VEGAN"]),
    ("Café Tradicional 3 Corações 500g", "3 Corações", "7896005800014", 22.49, "CAFE", ["VEGAN"]),
    ("Café Melitta Tradicional 500g", "Melitta", "7891021001045", 23.90, "CAFE", ["VEGAN"]),
    ("Detergente Líquido Neutro Ypê 500ml", "Ypê", "7896098900208", 2.49, "DETERGENTE", []),
    ("Detergente Limpol Neutro 500ml", "Limpol", "7891022100495", 2.29, "DETERGENTE", []),
    ("Detergente Minuano Limão 500ml", "Minuano", "7897664130142", 1.99, "DETERGENTE", []),
    ("Sabão em Pó Omo Lavagem Perfeita 1,6kg", "Omo", "7891150064317", 27.90, "SABAO_EM_PO", []),
    ("Sabão em Pó Tixan Ypê Primavera 1,6kg", "Ypê", "7896098902325", 18.90, "SABAO_EM_PO", []),
    ("Papel Higiênico Folha Dupla Neve 12 Rolos", "Neve", "7891172422539", 21.90, "PAPEL_HIGIENICO", []),
    ("Papel Higiênico Personal Folha Dupla 12 Rolos", "Personal", "7896110195322", 15.90, "PAPEL_HIGIENICO", []),
    ("Ovos Brancos Grandes 12 Unidades", None, "7898295020017", 11.90, "OVOS", ["VEGETARIAN"]),
    ("Ovos Caipira Vermelhos 10 Unidades", None, "7898295020024", 14.90, "OVOS", ["VEGETARIAN"]),
    ("Banana Prata 1kg", None, "2000000000015", 6.99, "FRUTAS", ["VEGAN"]),
    ("Banana Nanica 1kg", None, "2000000000022", 5.49, "FRUTAS", ["VEGAN"]),
    ("Açúcar Refinado União 1kg", "União", "7891910000197", 4.99, "ACUCAR", ["VEGAN"]),
    ("Açúcar Cristal Guarani 2kg", "Guarani", "7896215300034", 8.49, "ACUCAR", ["VEGAN"]),
    ("Óleo de Soja Liza 900ml", "Liza", "7896036090244", 7.49, "OLEO", ["VEGAN"]),
    ("Óleo de Soja Soya 900ml", "Soya", "7891107101621", 6.99, "OLEO", ["VEGAN"]),
    ("Macarrão Espaguete Barilla 500g", "Barilla", "7898951850033", 7.99, "MASSAS", ["VEGAN"]),
    ("Macarrão Espaguete Renata 500g", "Renata", "7896022204065", 4.79, "MASSAS", ["VEGAN"]),
    ("Molho de Tomate Tradicional Pomarola 300g", "Pomarola", "7896036098554", 3.49, "MOLHOS", ["VEGAN"]),
    ("Molho de Tomate Heinz 300g", "Heinz", "7896102000122", 4.29, "MOLHOS", ["VEGAN"]),
    ("Pão de Forma Tradicional Pullman 450g", "Pullman", "7896002301428", 9.49, "PAES", ["VEGETARIAN"]),
    ("Pão de Forma Integral Wickbold 500g", "Wickbold", "7896066334110", 11.90, "PAES", ["VEGETARIAN"]),
    ("Manteiga com Sal Aviação 200g", None, "7896051115014", 15.90, "MANTEIGA", ["VEGETARIAN"]),
    ("Margarina Qualy com Sal 500g", "Qualy", "7893000394117", 9.99, "MARGARINA", ["VEGETARIAN"]),
    ("Biscoito Integral Sabor Leite e Aveia Belvita 75g", "Belvita", "5280000496958", 5.49, "BISCOITOS/BISCOITO_SAUDAVEL", []),
    ("Biscoito Cream Cracker Vitarella 350g", "Vitarella", "7896213005184", 4.99, "BISCOITOS", ["VEGAN"]),
    ("Queijo Mussarela Fatiado 150g", None, "2000000000039", 9.90, "QUEIJOS", ["VEGETARIAN"]),
    ("Iogurte Natural Integral Nestlé 170g", "Nestlé", "7891000100103", 3.29, "IOGURTES", ["VEGETARIAN"]),
]

VOUCHERS = {
    "6af300f4-e672-43ca-8ae8-9ee96a501894": [("EXTRA15", "FIXED", 15.0, None, 150.0, "R$15 de desconto acima de R$150")],
    "782805bb-66e0-4d8d-8e86-5befa3b5d7f8": [("CARREFOUR10", "PERCENTAGE", 10.0, 12.0, 80.0, "10% de desconto, até R$12")],
    "8ca75d1c-bd6a-4cbf-bcdd-0cd1d8bdf1c2": [("FRETEGRATIS", "FIXED", 9.49, None, 100.0, "Frete grátis acima de R$100"),
                                             ("SAVE5", "FIXED", 5.0, None, 60.0, "R$5 de desconto acima de R$60")],
}

PAYMENT_METHODS = [
    {"paymentMethodId": "75354fd8-e916-41db-9286-93be2232efe2", "kind": "PIX", "displayName": "PIX"},
    {"paymentMethodId": "550e8400-e29b-41d4-a716-446655440001", "kind": "CREDIT_CARD", "displayName": "Cartão de Crédito"},
]

STATUS_FLOW = ["ORDER_CREATED", "CONFIRMED", "PREPARING", "DISPATCHED", "ON_THE_WAY",
               "DELIVERY_ARRIVED_AT_DESTINATION", "DELIVERED"]
STATUS_TEXT = {
    "ORDER_CREATED": "Order created.", "CONFIRMED": "Merchant confirmed the order.",
    "PREPARING": "Merchant is preparing the order.", "DISPATCHED": "Merchant released order for delivery.",
    "ON_THE_WAY": "Delivery person is on the way.",
    "DELIVERY_ARRIVED_AT_DESTINATION": "Delivery person arrived at order delivery address.",
    "DELIVERED": "Order delivered to the customer.",
}


def _uuid(*parts: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "gnomers-fake/" + "/".join(parts)))


def _tokens(text: str) -> set[str]:
    import re

    return {singular(t) for t in re.findall(r"[a-z0-9]+", norm(text))}


def build_catalog(seed: int = 7) -> list[dict]:
    rng = random.Random(seed)
    items = []
    for m_id, m_name, fee, minimum, dist, eta, factor in MERCHANTS:
        for name, brand, ean, base, tax, tags in PRODUCTS:
            # cada mercado não tem tudo; Ypê falta no Carrefour para gerar substituição na demo
            if rng.random() < 0.18 or (brand == "Ypê" and m_id.startswith("782805bb")):
                continue
            price = round(base * factor * rng.uniform(0.92, 1.1), 2)
            promo = round(price * rng.uniform(0.7, 0.88), 2) if rng.random() < 0.15 else price
            items.append({
                "itemId": _uuid(m_id, ean), "itemName": name, "itemEan": ean,
                "itemSkuId": f"SKU-{ean[-8:]}", "unitPrice": price, "promotionalUnitPrice": promo,
                "brand": brand, "taxonomy": tax, "tags": tags, "merchantId": m_id,
                "itemImageUrl": None,
            })
    return items


class FakeIfood:
    def __init__(self, open_now: bool = True):
        self.catalog = build_catalog()
        self.by_id = {i["itemId"]: i for i in self.catalog}
        self.merchants = {m[0]: m for m in MERCHANTS}
        self.open_now = open_now
        self.addresses: list[dict] = [{
            "addressId": "4f6b8d6e-5a2c-4b1e-9d3f-2c7a1e0b9f11", "alias": "Faculdade",
            "formattedAddress": "Avenida Trabalhador São-carlense, 400, ICMC, Parque Arnold Schimidt, São Carlos, SP",
            "location": {"street": "Avenida Trabalhador São-carlense", "number": "400",
                         "neighborhood": "Parque Arnold Schimidt", "city": "São Carlos", "state": "SP"},
            "coordinates": dict(ICMC), "isLastUsedAddress": True, "isMostUsedAddress30d": True,
            "topMerchantTypes": {"GROCERIES": "3", "RESTAURANT": "5"},
        }]
        self.carts: dict[str, dict] = {}
        self.orders: dict[str, dict] = {}
        self.recent: list[dict] = []
        self._candidates: dict[str, dict] = {}

    # ------------------------------------------------------------ helpers

    def _merchant_summary(self, m_id: str) -> dict:
        _, name, fee, minimum, dist, eta, _ = self.merchants[m_id]
        return {
            "merchantId": m_id, "merchantName": name, "merchantLogoUrl": None, "reviewRating": 4.8,
            "isOpen": self.open_now, "minimumOrderAmount": minimum, "straightLineDistanceKm": dist,
            "deliveryMethod": {"deliveryMethodId": "DEFAULT", "deliveryMethodMode": "DELIVERY", "fee": fee,
                               "promotionalFee": fee, "estimatedMinutes": {"min": eta[0], "max": eta[1]}},
        }

    def _item_out(self, item: dict) -> dict:
        out = {k: item[k] for k in ("itemId", "itemName", "itemEan", "itemSkuId", "itemImageUrl",
                                    "unitPrice", "promotionalUnitPrice")}
        out["merchant"] = self._merchant_summary(item["merchantId"])
        return out

    def _filter(self, items: list[dict], ref: dict) -> list[dict]:
        out = []
        for it in items:
            m = self._merchant_summary(it["merchantId"])
            price = it["promotionalUnitPrice"]
            if ref.get("merchantId") and it["merchantId"] != ref["merchantId"]:
                continue
            if ref.get("maxItemPrice") is not None and price > ref["maxItemPrice"]:
                continue
            if ref.get("maxDeliveryFee") is not None and m["deliveryMethod"]["fee"] > ref["maxDeliveryFee"]:
                continue
            if ref.get("maxDistanceKm") is not None and m["straightLineDistanceKm"] > ref["maxDistanceKm"]:
                continue
            if ref.get("maxMinimumOrderAmount") is not None and m["minimumOrderAmount"] > ref["maxMinimumOrderAmount"]:
                continue
            if ref.get("maxDeliveryTimeMinutes") is not None and m["deliveryMethod"]["estimatedMinutes"]["max"] > ref["maxDeliveryTimeMinutes"]:
                continue
            if ref.get("onlyOpen") and not m["isOpen"]:
                continue
            if ref.get("onlyWithPromotion") and not it["promotionalUnitPrice"] < it["unitPrice"]:
                continue
            if ref.get("itemRestrictions") and not set(ref["itemRestrictions"]) <= set(it["tags"]):
                continue
            if ref.get("itemTaxonomy"):
                wanted = ref["itemTaxonomy"]
                if not any(it["taxonomy"] == t or it["taxonomy"].startswith(t + "/") for t in wanted):
                    continue
            out.append(it)
        sort = ref.get("sort", "RELEVANCE")
        if sort == "PRICE":
            out.sort(key=lambda i: i["promotionalUnitPrice"])
        elif sort in ("DELIVERY_FEE", "DISTANCE", "DELIVERY_TIME"):
            key = {"DELIVERY_FEE": lambda i: self.merchants[i["merchantId"]][2],
                   "DISTANCE": lambda i: self.merchants[i["merchantId"]][4],
                   "DELIVERY_TIME": lambda i: self.merchants[i["merchantId"]][5][1]}[sort]
            out.sort(key=key)
        return out

    @staticmethod
    def _page(results: list, args: dict) -> tuple[list, str | None]:
        size = min(int(args.get("pageSize", 10)), 25)
        offset = int(base64.b64decode(args["nextPageToken"]).decode().split(";")[0]) if args.get("nextPageToken") else 0
        page = results[offset: offset + size]
        nxt = offset + size
        token = base64.b64encode(f"{nxt};1".encode()).decode() if nxt < len(results) else None
        return page, token

    @staticmethod
    def _facets(items: list[dict]) -> dict:
        counts: dict[str, int] = {}
        for it in items:
            counts[it["taxonomy"]] = counts.get(it["taxonomy"], 0) + 1
        return {"itemTaxonomy": [{"count": c, "id": t, "name": t.split("/")[-1]}
                                 for t, c in sorted(counts.items(), key=lambda x: -x[1])]}

    def _listing(self, items: list[dict], args: dict, extra: dict) -> dict:
        kind = args.get("resultKind", "MERCHANT")
        if kind == "MERCHANT":
            seen: list[str] = []
            for it in items:
                if it["merchantId"] not in seen:
                    seen.append(it["merchantId"])
            results, token = self._page([self._merchant_summary(m) for m in seen], args)
        else:
            results, token = self._page([self._item_out(i) for i in items], args)
        body = {"resultKind": kind, "results": results, "hasMore": token is not None, "nextPageToken": token,
                "agentHints": {}, **extra}
        if args.get("includeFacets"):
            body["facets"] = self._facets(items)
        if not results:
            body["agentHints"]["noResultsGuidance"] = "No results found. Try a broader term."
        return body

    def _cart(self, cart_id: str) -> dict:
        cart = self.carts.get(cart_id)
        if not cart:
            raise McpToolError("cart", "CART_NOT_FOUND", f"O carrinho '{cart_id}' não foi encontrado.")
        return cart

    def _cart_body(self, cart: dict) -> dict:
        m = self._merchant_summary(cart["merchantId"])
        subtotal = round(sum(self.by_id[i["itemId"]]["promotionalUnitPrice"] * i["quantity"] for i in cart["items"]), 2)
        method = cart.get("deliveryMethod") or {"fee": m["deliveryMethod"]["fee"]}
        voucher = 0.0
        if cart.get("voucher"):
            code, effect, value, cap, minimum, _ = cart["voucher"]
            voucher = value if effect == "FIXED" else min(subtotal * value / 100, cap or 1e9)
            voucher = round(min(voucher, subtotal), 2)
        fee = method["fee"]
        total = round(subtotal + fee - voucher, 2)
        warning = None
        if subtotal < m["minimumOrderAmount"]:
            warning = {"code": "BELOW_MINIMUM_ORDER", "message": f"Subtotal abaixo do pedido mínimo de R$ {m['minimumOrderAmount']:.2f}.",
                       "details": {}, "impact": {"canProceedToCheckout": False}, "suggestedActions": [],
                       "userFriendlyMessage": "Pedido abaixo do mínimo da loja."}
        return {
            "cartId": cart["cartId"],
            "merchant": {"merchantId": m["merchantId"], "merchantName": m["merchantName"],
                         "orderMinimumPrice": m["minimumOrderAmount"], "formattedAddress": None},
            "items": [{"itemId": i["itemId"], "cartItemId": i["cartItemId"], "name": self.by_id[i["itemId"]]["itemName"],
                       "quantity": i["quantity"], "unitPrice": self.by_id[i["itemId"]]["promotionalUnitPrice"],
                       "customerNote": i.get("customerNote"), "options": []} for i in cart["items"]],
            "deliveryAddress": {"streetName": "Avenida Trabalhador São-carlense", "streetNumber": "400",
                                "neighborhood": "Parque Arnold Schimidt"},
            "deliveryMethod": cart.get("deliveryMethod"),
            "paymentMethod": cart.get("paymentMethod"),
            "fees": [{"name": "Taxa de serviço", "description": None, "amount": 0.0}],
            "summary": {"totalName": "Total", "totalAmount": total, "subtotalName": "Subtotal", "subtotalAmount": subtotal,
                        "deliveryFeeName": "Taxa de entrega", "deliveryFeeAmount": fee, "feeTotalName": "Taxas",
                        "feeTotalAmount": 0.0, "benefitsTotalName": "Benefícios", "benefitsTotalAmount": 0.0,
                        "voucherTotalName": "Cupom", "voucherTotalAmount": voucher},
            "warning": warning, "appDeeplink": f"ifoodapp://cart/{cart['cartId']}", "agentHints": {},
        }

    @staticmethod
    def _timeslots(m_id: str) -> list[dict]:
        """Janelas de 2 h, das 9h às 20h, nos dois dias seguintes, com ID (como no sandbox)."""
        from datetime import date, timedelta

        out = []
        for d in (1, 2):
            day = date.today() + timedelta(days=d)
            for h in range(9, 20):
                start = f"{day.isoformat()}T{h:02d}:00:00"
                end = f"{day.isoformat()}T{min(h + 2, 20):02d}:00:00"
                out.append({"timeslotId": _uuid("slot", m_id, start), "from": start, "to": end})
        return out

    def _delivery_methods(self, m_id: str) -> list[dict]:
        _, _, fee, _, _, eta, _ = self.merchants[m_id]
        return [
            {"deliveryMethodId": "DEFAULT", "deliveryMethodMode": "DELIVERY", "deliveryMethodName": "Entrega Padrão",
             "fee": fee, "estimatedMinutes": {"min": eta[0], "max": eta[1]}, "timeslots": None},
            {"deliveryMethodId": "EXPRESS", "deliveryMethodMode": "DELIVERY", "deliveryMethodName": "Entrega Expressa",
             "fee": round(fee + 6, 2), "estimatedMinutes": {"min": eta[0] // 2, "max": eta[1] // 2}, "timeslots": None},
            {"deliveryMethodId": "SCHEDULED", "deliveryMethodMode": "DELIVERY", "deliveryMethodName": "Agendada",
             "fee": fee, "estimatedMinutes": {"min": 120, "max": 180}, "timeslots": self._timeslots(m_id)},
        ]

    # --------------------------------------------------------------- tools

    async def __call__(self, tool: str, args: dict) -> Any:
        handler = getattr(self, f"t_{tool}", None)
        if handler is None:
            raise McpToolError(tool, "UNKNOWN_TOOL", f"tool {tool} não existe no simulador")
        return handler(args)

    def t_getUser(self, args):
        body = {"fullName": "Conta de Teste Gnomers", "email": "teste@gnomers.dev"}
        if args.get("includeProfileData"):
            body["profileData"] = {"userBehaviorDescription":
                                   "- Compra mercado a cada 2 semanas\n- Prefere café Pilão\n- Leite integral sempre"}
        return body

    def t_listAddresses(self, args):
        return {"results": self.addresses, "agentHints": {"addressCount": len(self.addresses)}}

    def t_createAddress(self, args):
        if args.get("candidateId"):
            cand = self._candidates.get(args["candidateId"])
            if not cand:
                raise McpToolError("createAddress", "UNKNOWN_CANDIDATE", "candidateId desconhecido")
            addr = {"addressId": _uuid("addr", args["candidateId"]), "alias": args.get("alias"),
                    "formattedAddress": cand["formattedAddress"], "coordinates": cand["coordinates"],
                    "isLastUsedAddress": False, "isMostUsedAddress30d": False, "topMerchantTypes": {}}
            self.addresses.append(addr)
            return {**addr, "agentHints": {}}
        cid = f"cand-{len(self._candidates) + 1:03d}"
        cand = {"candidateId": cid, "formattedAddress": args["address"],
                "location": {"neighborhood": "Centro", "city": "São Carlos", "state": "SP"},
                "coordinates": {"latitude": ICMC["latitude"] + 0.003, "longitude": ICMC["longitude"] - 0.002}}
        self._candidates[cid] = cand
        return {"matchConfidence": "SINGLE", "candidates": [cand], "agentHints": {}}

    def t_searchOffers(self, args):
        q = _tokens(args.get("query", "")) - {"de", "da", "do", "com", "em"}
        if not q:
            raise McpToolError("searchOffers", "INVALID_QUERY", "The search query is empty.", retryable=True)
        exact = args.get("intention") in ("product_exact", "merchant_exact")
        items = []
        for it in self.catalog:
            toks = _tokens(it["itemName"])
            hits = len(q & toks)
            # busca relevante: todos os termos no modo exato, a maioria no modo normal
            if (exact and hits == len(q)) or (not exact and hits >= max(1, len(q) - 1)):
                items.append(it)
        items = self._filter(items, args.get("refinements", {}))
        return self._listing(items, args, {"interpretedQuery": args["query"]})

    def t_browseOffers(self, args):
        items = self._filter(list(self.catalog), args.get("refinements", {}))
        return self._listing(items, args, {"merchantType": args.get("merchantType")})

    def t_getRecommendations(self, args):
        mode = args["contentMode"]
        if mode == "RECENT_ORDERS":
            items = [self.by_id[r["itemId"]] for r in self.recent if r["itemId"] in self.by_id]
        elif mode == "BEST_DEALS":
            items = [i for i in self.catalog if i["promotionalUnitPrice"] < i["unitPrice"]]
        else:
            items = self.catalog[:25]
        items = self._filter(items, args.get("refinements", {}))
        body = self._listing(items, args, {"contentMode": mode})
        if mode == "RECENT_ORDERS" and args.get("resultKind") == "ITEM":
            by_item = {r["itemId"]: r for r in self.recent}
            for r in body["results"]:
                rec = by_item[r["itemId"]]
                r.update(lastOrderedAt=rec["lastOrderedAt"], timesOrdered=rec["timesOrdered"], lastOrderId=rec["orderId"])
        return body

    def t_getMerchant(self, args):
        m_id = args["merchantId"]
        if m_id not in self.merchants:
            raise McpToolError("getMerchant", "MERCHANT_NOT_FOUND", f"The merchant ID '{m_id}' was not found.")
        s = self._merchant_summary(m_id)
        return {**s, "merchantType": "GROCERIES",
                "operatingHours": {"today": {"openLocal": "07:00", "closeLocal": "22:00"}},
                "deliveryMethods": [{k: v for k, v in d.items() if k != "deliveryMethodName"} for d in self._delivery_methods(m_id)],
                "catalogSummary": {"totalItems": sum(1 for i in self.catalog if i["merchantId"] == m_id)},
                "featuredItems": [], "agentHints": {}}

    def t_getItem(self, args):
        it = self.by_id.get(args["itemId"])
        if not it or it["merchantId"] != args["merchantId"]:
            raise McpToolError("getItem", "ITEM_NOT_FOUND", "item não encontrado neste mercado")
        return {"itemId": it["itemId"], "itemName": it["itemName"], "merchantId": it["merchantId"],
                "unitPrice": it["unitPrice"], "promotionalUnitPrice": it["promotionalUnitPrice"],
                "itemDescription": it["itemName"], "optionGroups": [], "agentHints": {}}

    def t_createCart(self, args):
        m_id = args["merchantId"]
        cart_id = _uuid("cart", str(len(self.carts)), m_id)
        cart = {"cartId": cart_id, "merchantId": m_id, "addressId": args["addressId"], "items": []}
        for i, item in enumerate(args["items"]):
            if self.by_id.get(item["itemId"], {}).get("merchantId") != m_id:
                raise McpToolError("createCart", "ITEM_NOT_AVAILABLE", f"item {item['itemId']} não pertence ao mercado")
            cart["items"].append({"itemId": item["itemId"], "quantity": int(item["quantity"]),
                                  "cartItemId": _uuid(cart_id, str(i)), "customerNote": item.get("customerNote")})
        self.carts[cart_id] = cart
        return self._cart_body(cart)

    def t_getCart(self, args):
        return self._cart_body(self._cart(args["cartId"]))

    def t_addItemToCart(self, args):
        cart = self._cart(args["cartId"])
        cart["items"].append({"itemId": args["itemId"], "quantity": int(args["quantity"]),
                              "cartItemId": _uuid(cart["cartId"], str(len(cart["items"])), args["itemId"])})
        return self._cart_body(cart)

    def t_updateCartItem(self, args):
        cart = self._cart(args["cartId"])
        for it in cart["items"]:
            if it["cartItemId"] == args["cartItemId"]:
                it["quantity"] = int(args["quantity"])
                return self._cart_body(cart)
        raise McpToolError("updateCartItem", "CART_ITEM_NOT_FOUND", "cartItemId não encontrado")

    def t_removeItemFromCart(self, args):
        cart = self._cart(args["cartId"])
        cart["items"] = [i for i in cart["items"] if i["cartItemId"] != args["cartItemId"]]
        if not cart["items"]:
            del self.carts[cart["cartId"]]
            return {"agentHints": {"cartDeletedAfterLastItemRemoved": "O carrinho foi excluído."}}
        return self._cart_body(cart)

    def t_listVouchers(self, args):
        cart = self._cart(args["cartId"])
        subtotal = self._cart_body(cart)["summary"]["subtotalAmount"]
        out = []
        for code, effect, value, cap, minimum, desc in VOUCHERS.get(cart["merchantId"], []):
            if subtotal >= minimum:
                out.append({"code": code, "campaignId": _uuid("campaign", code), "effectType": effect,
                            "effectValue": value, "maxDiscountValue": cap, "description": desc, "available": True})
        return {"vouchers": out, "agentHints": {}}

    def t_setVoucher(self, args):
        cart = self._cart(args["cartId"])
        for v in VOUCHERS.get(cart["merchantId"], []):
            if v[0] == args["code"] and _uuid("campaign", v[0]) == args["campaignId"]:
                cart["voucher"] = v
                return self._cart_body(cart)
        raise McpToolError("setVoucher", "VOUCHER_INVALID", f"O voucher '{args['code']}' não pôde ser aplicado.")

    def t_removeVoucher(self, args):
        cart = self._cart(args["cartId"])
        cart.pop("voucher", None)
        return self._cart_body(cart)

    def t_getCartDeliveryMethods(self, args):
        cart = self._cart(args["cartId"])
        return {"cartId": cart["cartId"], "deliveryMethods": self._delivery_methods(cart["merchantId"]),
                "warning": None, "agentHints": {}}

    def t_setCartDelivery(self, args):
        cart = self._cart(args["cartId"])
        for d in self._delivery_methods(cart["merchantId"]):
            if d["deliveryMethodId"] == args["deliveryMethodId"]:
                slot = None
                if d["timeslots"]:
                    slot = next((t for t in d["timeslots"] if t["timeslotId"] == args.get("selectedTimeSlotId")), None)
                    if slot is None:
                        raise McpToolError("setCartDelivery", "TIMESLOT_REQUIRED", "método agendado exige selectedTimeSlotId válido")
                cart["deliveryMethod"] = {k: d[k] for k in ("deliveryMethodMode", "deliveryMethodName", "fee", "estimatedMinutes")} | {
                    "timeSlot": {"from": slot["from"], "to": slot["to"]} if slot else None}
                return self._cart_body(cart)
        raise McpToolError("setCartDelivery", "DELIVERY_METHOD_UNAVAILABLE", "método indisponível")

    def t_listPaymentMethods(self, args):
        return {"paymentMethods": [dict(p, cardTokenId=None, brand=None, issuerBank=None) for p in PAYMENT_METHODS],
                "agentHints": {}}

    def t_setCartPayment(self, args):
        cart = self._cart(args["cartId"])
        for p in PAYMENT_METHODS:
            if p["paymentMethodId"] == args["paymentMethodId"]:
                cart["paymentMethod"] = dict(p, cardTokenId=None, brand=None, issuerBank=None)
                return self._cart_body(cart)
        raise McpToolError("setCartPayment", "PAYMENT_METHOD_UNAVAILABLE", "forma de pagamento indisponível")

    def t_submitOrder(self, args):
        cart = self._cart(args["cartId"])
        if not args.get("userConsent"):
            raise McpToolError("submitOrder", "CONSENT_REQUIRED", "userConsent precisa ser true")
        missing = [k for k in ("deliveryMethod", "paymentMethod") if not cart.get(k)]
        if missing:
            raise McpToolError("submitOrder", "CART_NOT_READY", f"Faltando: {', '.join(missing)}")
        body = self._cart_body(cart)
        if body["warning"]:
            raise McpToolError("submitOrder", "CART_NOT_READY", body["warning"]["message"])
        order_id = _uuid("order", cart["cartId"])
        self.orders[order_id] = {"cart": body, "created": time.time()}
        for it in cart["items"]:
            prev = next((r for r in self.recent if r["itemId"] == it["itemId"]), None)
            if prev:
                prev["timesOrdered"] += 1
            else:
                self.recent.append({"itemId": it["itemId"], "timesOrdered": 1, "orderId": order_id,
                                    "lastOrderedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        del self.carts[cart["cartId"]]
        return {"orderId": order_id, "agentHints": {"nextStep": "Comece a rastrear o pedido com trackOrder."}}

    def _status_index(self, order: dict) -> int:
        return min(int((time.time() - order["created"]) // 8), len(STATUS_FLOW) - 1)

    def t_trackOrder(self, args):
        order = self.orders.get(args["orderId"])
        if not order:
            return {"errorType": "ResourceNotFoundError", "code": "ORDER_NOT_FOUND",
                    "message": f"Order ID '{args['orderId']}' not found", "retryable": False}
        idx = self._status_index(order)
        history = [{"code": STATUS_FLOW[i], "description": STATUS_TEXT[STATUS_FLOW[i]]} for i in range(idx, -1, -1)][:5]
        terminal = STATUS_FLOW[idx] == "DELIVERED"
        return {"status": history[0], "statusHistory": history,
                "agentHints": {"retryMessage": None if terminal else "Please call again in 5 seconds for updates",
                               "moreStatusEvents": None, "pollingStop": True if terminal else None,
                               "stopMessage": "Do not call this tool for this order." if terminal else None}}

    def t_getOrder(self, args):
        order = self.orders.get(args["orderId"])
        if not order:
            raise McpToolError("getOrder", "ORDER_NOT_FOUND", "pedido não encontrado", retryable=True)
        cart = order["cart"]
        return {"orderId": args["orderId"], "merchantName": cart["merchant"]["merchantName"],
                "items": [f"{i['quantity']}x {i['name']}" for i in cart["items"]],
                "payment": {"method": "PIX", "total": cart["summary"]["totalAmount"],
                            "pixCopyPaste": f"00020126580014br.gov.bcb.pix0136{args['orderId']}5204000053039865406"
                                            f"{cart['summary']['totalAmount']:.2f}5802BR6009SAO CARLOS62070503***6304ABCD"},
                "deliveryVerificationCode": "4821"}
