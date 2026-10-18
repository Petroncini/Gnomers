"""Descoberta em duas fases (seção 7.2 do plano).

Fase 1: uma busca por item em todo o raio, ordenada por preço. Com isso o sistema
conta a cobertura de cada mercado e escolhe até cinco candidatos. Fase 2: preenche
as lacunas de cada par (item, mercado candidato) com buscas restritas ao mercado e
percorre a cadeia de marcas do usuário com `product_exact`.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from app.mcp.client import McpToolError
from app.mcp.ifood import Ifood
from app.rules import Resolved, norm

log = logging.getLogger(__name__)

MAX_MERCHANTS = 5
GAP_MERCHANTS = 3
DEFAULT_MAX_DISTANCE_KM = 8.0


@dataclass
class ItemQuery:
    item_id: int
    raw_text: str
    query: str
    intention: str
    category: str
    brand: str | None
    rules: Resolved
    core: str = ""  # busca curta: só o produto e a marca ("feijão", "leite Italac")
    variants: list[str] = field(default_factory=list)  # a busca com sinônimo de rótulo

    def searches(self) -> list[tuple[str, str]]:
        """(texto, intention) de cada busca do item, sem repetição."""
        out = [(self.query, self.intention)]
        if self.core and self.core != self.query:
            out.append((self.core, "product_exact" if self.brand else "product"))
        out += [(v, self.intention) for v in self.variants[:1]]
        seen, unique = set(), []
        for text, intention in out:
            if text.lower() not in seen:
                seen.add(text.lower())
                unique.append((text, intention))
        return unique


@dataclass
class Offer:
    item_id: int
    sku_id: str
    merchant_id: str
    name: str
    ean: str | None
    price: float          # preço efetivo (promocional quando houver)
    list_price: float
    image: str | None
    found_by: str         # "fase1", "fase2", "marca:<x>"


@dataclass
class MerchantInfo:
    id: str
    name: str
    delivery_fee: float
    minimum_order: float
    distance_km: float | None
    eta_max: int | None
    is_open: bool
    logo: str | None = None
    delivery_methods: list[dict] = field(default_factory=list)


@dataclass
class DiscoveryResult:
    offers: list[Offer]
    merchants: dict[str, MerchantInfo]
    coverage: dict[str, int]
    errors: list[dict]


Emit = Callable[[str, dict], Awaitable[None]]


def _refinements(q: ItemQuery, *, only_open: bool, max_distance_km: float | None,
                 max_minimum: float | None, merchant_id: str | None = None) -> dict:
    r = q.rules
    # Ordenação por relevância (o padrão). Com sort PRICE, "feijão" traz tempero, lámen e macarrão
    # instantâneo antes de qualquer pacote de feijão (sandbox, 18/10); quem compara preço é o solver.
    ref: dict[str, Any] = {}
    if only_open:
        ref["onlyOpen"] = True
    if max_distance_km and not merchant_id:
        ref["maxDistanceKm"] = max_distance_km
    if max_minimum is not None and not merchant_id:
        ref["maxMinimumOrderAmount"] = max_minimum
    if r.dietary:
        ref["itemRestrictions"] = r.dietary
    # sem itemTaxonomy: a busca não mostra a categoria de cada produto e um filtro errado
    # esconde tudo (LEITE_CONDENSADO para leite, 18/10); quem decide o que é o produto é o app
    if r.max_delivery_fee is not None:
        ref["maxDeliveryFee"] = r.max_delivery_fee
    if r.max_delivery_minutes is not None:
        ref["maxDeliveryTimeMinutes"] = r.max_delivery_minutes
    if merchant_id:
        ref["merchantId"] = merchant_id
    return ref


def effective_fee(method: dict) -> float:
    """Frete que o carrinho cobra de fato.

    Confirmado no sandbox em 18/10: `fee: 6.49, promotionalFee: 0` virou frete R$ 0,00
    no carrinho real, e `fee: 10.99, promotionalFee: 10.99` virou R$ 10,99. Mercado
    fechado vem com o método vazio (`deliveryMethodId: ""` e tudo zero); aí não há
    informação e vale o `fee` cheio.
    """
    fee = float(method.get("fee") or 0)
    promo = method.get("promotionalFee")
    if promo is None or not method.get("deliveryMethodId"):
        return fee
    return min(fee, float(promo))


def _merchant_from(summary: dict) -> MerchantInfo:
    dm = summary.get("deliveryMethod") or {}
    eta = dm.get("estimatedMinutes") or {}
    return MerchantInfo(
        id=summary["merchantId"], name=summary.get("merchantName", ""),
        delivery_fee=effective_fee(dm), minimum_order=float(summary.get("minimumOrderAmount") or 0),
        distance_km=summary.get("straightLineDistanceKm"), eta_max=eta.get("max") or None,
        is_open=bool(summary.get("isOpen", True)), logo=summary.get("merchantLogoUrl"),
    )


def _offers_from(body: dict, item_id: int, found_by: str) -> tuple[list[Offer], dict[str, MerchantInfo]]:
    offers, merchants = [], {}
    for r in body.get("results", []):
        m = r.get("merchant") or {}
        if not r.get("itemId") or not m.get("merchantId"):
            continue
        price = r.get("promotionalUnitPrice") or r.get("unitPrice")
        if price is None:
            continue
        offers.append(Offer(
            item_id=item_id, sku_id=r["itemId"], merchant_id=m["merchantId"], name=r.get("itemName", ""),
            ean=r.get("itemEan"), price=float(price), list_price=float(r.get("unitPrice") or price),
            image=r.get("itemImageUrl"), found_by=found_by,
        ))
        merchants.setdefault(m["merchantId"], _merchant_from(m))
    return offers, merchants


async def discover(api: Ifood, queries: list[ItemQuery], lat: float, lng: float, *,
                   only_open: bool = True, max_distance_km: float | None = DEFAULT_MAX_DISTANCE_KM,
                   emit: Emit | None = None) -> DiscoveryResult:
    async def say(stage: str, data: dict) -> None:
        if emit:
            await emit(stage, data)

    errors: list[dict] = []
    offers: list[Offer] = []
    merchants: dict[str, MerchantInfo] = {}

    # ---------------------------------------------------------- fase 1
    # A busca do iFood é por palavras e exige todas: o texto do item sozinho perde produtos
    # ("sem açúcar" não acha "Zero Açúcar"). Por item: a busca específica, a curta e um sinônimo.
    async def search(q: ItemQuery, text: str, intention: str):
        try:
            body = await api.search_items(text, lat, lng, intention=intention,
                                          refinements=_refinements(q, only_open=only_open,
                                                                   max_distance_km=max_distance_km, max_minimum=None))
        except (McpToolError, ValueError) as exc:
            errors.append({"item_id": q.item_id, "stage": "fase1", "query": text, "error": str(exc)})
            return q, text, {}
        return q, text, body

    jobs = [(q, text, intention) for q in queries for text, intention in q.searches()]
    await say("discovery", {"message": f"Buscando {len(queries)} itens nos mercados próximos ({len(jobs)} buscas)"})
    for q, text, body in await asyncio.gather(*(search(q, t, i) for q, t, i in jobs)):
        o, m = _offers_from(body, q.item_id, f"fase1:{text}")
        offers.extend(o)
        for k, v in m.items():
            merchants.setdefault(k, v)

    # cobertura: quantos itens distintos cada mercado tem
    coverage: dict[str, set[int]] = {}
    for o in offers:
        coverage.setdefault(o.merchant_id, set()).add(o.item_id)

    blocked = set().union(*(q.rules.block_merchants for q in queries)) if queries else set()
    preferred = [m for q in queries for m in q.rules.prefer_merchants]

    def rank(m_id: str) -> tuple:
        info = merchants[m_id]
        return (-len(coverage.get(m_id, ())), info.delivery_fee + 0.1 * info.minimum_order)

    ranked = [m for m in sorted(coverage, key=rank) if m not in blocked]
    candidates = ranked[:MAX_MERCHANTS]
    for m in preferred:
        if m not in candidates and m not in blocked:
            candidates.append(m)
    await say("merchants", {"message": f"{len(coverage)} mercados encontrados; {len(candidates)} candidatos",
                            "candidates": [{"id": m, "name": merchants[m].name if m in merchants else m,
                                            "coverage": len(coverage.get(m, ()))} for m in candidates]})

    # detalhe de cada candidato antes de apresentá-lo (getMerchant)
    async def detail(m_id: str):
        try:
            body = await api.get_merchant(m_id, lat, lng)
        except McpToolError as exc:
            errors.append({"merchant_id": m_id, "stage": "getMerchant", "error": str(exc)})
            return m_id, None
        return m_id, body

    for m_id, body in await asyncio.gather(*(detail(m) for m in candidates)):
        if body is None:
            if m_id not in merchants:
                candidates.remove(m_id)
            continue
        info = merchants.get(m_id) or MerchantInfo(m_id, body.get("merchantName", ""), 0, 0, None, None, True)
        info.name = body.get("merchantName") or info.name
        info.minimum_order = float(body.get("minimumOrderAmount") or info.minimum_order)
        info.is_open = bool(body.get("isOpen", info.is_open))
        methods = [d for d in body.get("deliveryMethods", []) if d.get("deliveryMethodMode", "DELIVERY") in ("DELIVERY", "")]
        info.delivery_methods = methods
        fees = [effective_fee(d) for d in methods if d.get("fee") is not None and d.get("deliveryMethodId")]
        if fees:
            info.delivery_fee = min(fees)
        merchants[m_id] = info

    # ---------------------------------------------------------- fase 2
    have = {(o.item_id, o.merchant_id) for o in offers}
    by_item_brand: dict[int, set[str]] = {}
    for o in offers:
        by_item_brand.setdefault(o.item_id, set()).add(norm(o.name))

    async def fill(q: ItemQuery, m_id: str):
        # dentro de um mercado, a busca curta traz as opções dele; o app confere a variedade depois
        try:
            body = await api.search_items(q.core or q.query, lat, lng,
                                          intention="product_exact" if q.brand else "product", page_size=10,
                                          refinements=_refinements(q, only_open=False, max_distance_km=None,
                                                                   max_minimum=None, merchant_id=m_id))
        except (McpToolError, ValueError) as exc:
            errors.append({"item_id": q.item_id, "merchant_id": m_id, "stage": "fase2", "error": str(exc)})
            return []
        return _offers_from(body, q.item_id, "fase2")[0]

    # lacunas só nos mercados que mais cobrem a lista: são os que o solver acaba escolhendo
    gap_merchants = candidates[:GAP_MERCHANTS]
    gaps = [(q, m) for q in queries for m in gap_merchants if (q.item_id, m) not in have]
    await say("gaps", {"message": f"Completando {len(gaps)} lacunas nos mercados candidatos"})
    for found in await asyncio.gather(*(fill(q, m) for q, m in gaps)):
        offers.extend(found)

    # cadeia de marcas: se a primeira não apareceu, busca a próxima com product_exact
    async def chain(q: ItemQuery):
        out: list[Offer] = []
        names = by_item_brand.get(q.item_id, set())
        for brand in q.rules.brand_chain:
            if any(norm(brand) in n for n in names) or norm(brand) in norm(q.query):
                continue
            query = f"{q.core.replace(q.brand or '', '').strip() or q.category} {brand}"
            try:
                body = await api.search_items(query, lat, lng, intention="product_exact",
                                              refinements=_refinements(q, only_open=only_open,
                                                                       max_distance_km=max_distance_km,
                                                                       max_minimum=None))
            except (McpToolError, ValueError) as exc:
                errors.append({"item_id": q.item_id, "stage": f"marca:{brand}", "error": str(exc)})
                continue
            found, m = _offers_from(body, q.item_id, f"marca:{brand}")
            found = [o for o in found if o.merchant_id in candidates]
            out.extend(found)
            if found:
                break
        return out

    chained = [q for q in queries if q.rules.brand_chain]
    if chained:
        await say("brands", {"message": f"Procurando marcas preferidas de {len(chained)} itens"})
        for found in await asyncio.gather(*(chain(q) for q in chained)):
            offers.extend(found)

    # deduplica (o mesmo SKU pode vir na fase 1 e na fase 2) e mantém só candidatos
    seen: set[tuple[int, str]] = set()
    unique = []
    for o in offers:
        key = (o.item_id, o.sku_id)
        if key in seen or o.merchant_id not in candidates:
            continue
        seen.add(key)
        unique.append(o)
    return DiscoveryResult(offers=unique, merchants={m: merchants[m] for m in candidates if m in merchants},
                           coverage={m: len(v) for m, v in coverage.items()}, errors=errors)
