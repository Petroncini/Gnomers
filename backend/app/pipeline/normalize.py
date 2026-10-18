"""Normalização dos SKUs e montagem dos candidatos do solver (seção 7.3 do plano)."""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from sqlmodel import Session, select

from app import heuristics, llm
from app.models import Product, now
from app.pipeline.discovery import Offer
from app.pipeline.optimize import Candidate, Item, packs_for
from app.rules import Resolved, norm

TOP_PER_MERCHANT = 5
BATCH = 30
OVERSIZE = 2.0  # embalagem com mais que o dobro do pedido só entra se não houver outra opção


@dataclass
class ItemSpec:
    id: int
    raw_text: str
    query: str
    category: str
    brand: str | None
    variety_terms: list[str]
    pack_size: float | None
    pack_unit: str | None
    quantity: float
    rules: Resolved
    pin_size: float | None = None  # escolha do usuário num carrinho anterior: só embalagens deste tamanho
    required_terms: list[str] = field(default_factory=list)  # da regra "product": o que o usuário disse querer


@dataclass
class SkuInfo:
    sku_id: str
    item_id: int
    merchant_id: str
    name: str
    brand: str | None
    variety_terms: list[str]
    pack_size: float | None
    pack_unit: str | None
    quality_tier: int
    price: float
    list_price: float
    size: float
    image: str | None
    reasons: list[str] = field(default_factory=list)  # por que é substituição
    size_known: bool = True
    quality_penalty: float = 0.0
    substitution_penalty: float = 0.0
    note_penalty: float = 0.0

    def to_dict(self) -> dict:
        return {"sku_id": self.sku_id, "item_id": self.item_id, "merchant_id": self.merchant_id, "name": self.name,
                "brand": self.brand, "variety_terms": self.variety_terms, "pack_size": self.pack_size,
                "pack_unit": self.pack_unit, "quality_tier": self.quality_tier, "price": self.price,
                "list_price": self.list_price, "size": self.size, "image": self.image, "reasons": self.reasons,
                "size_known": self.size_known, "quality_penalty": self.quality_penalty,
                "substitution_penalty": self.substitution_penalty, "note_penalty": self.note_penalty}


@dataclass
class NormalizedSet:
    items: list[Item]
    candidates: list[Candidate]
    skus: dict[str, SkuInfo]  # chave: sku_key(item_id, sku_id)
    rejected: dict[int, dict[str, int]]


def sku_key(item_id: int, sku_id: str) -> str:
    # o mesmo SKU pode atender dois itens da lista
    return f"{item_id}|{sku_id}"


def required_amount(spec: ItemSpec) -> tuple[float, str]:
    """Quanto o item pede e em que unidade: g, ml, un ou 'pack' (embalagens)."""
    if spec.pack_size and spec.pack_unit:
        return spec.quantity * spec.pack_size, spec.pack_unit
    return spec.quantity, "pack"


def size_in_item_units(spec: ItemSpec, unit_mode: str, sku_size: float | None, sku_unit: str | None) -> float:
    if unit_mode == "pack":
        # item sem tamanho: conta unidades quando o SKU vem em unidades ("ovos 12 un"), senão embalagens
        if sku_unit == "un" and sku_size and spec.quantity > 1:
            return sku_size
        return 1.0
    if sku_size and sku_unit == unit_mode:
        return sku_size
    # tamanho desconhecido ou unidade diferente: assume a embalagem que o usuário pediu
    return spec.pack_size or 1.0


async def _ensure_products(s: Session, specs: dict[int, ItemSpec], offers: list[Offer]) -> dict[str, Product]:
    ids = list({o.sku_id for o in offers})
    cached = {p.ifood_item_id: p for p in s.exec(select(Product).where(Product.ifood_item_id.in_(ids))).all()} if ids else {}
    by_ean: dict[str, Product] = {}
    eans = [o.ean for o in offers if o.ean]
    if eans:
        for p in s.exec(select(Product).where(Product.ean.in_(eans))).all():
            by_ean.setdefault(p.ean, p)

    pending: dict[int, list[Offer]] = {}
    for o in offers:
        spec = specs[o.item_id]
        p = cached.get(o.sku_id)
        if p and norm(p.matched_category) == norm(spec.category):
            continue
        twin = by_ean.get(o.ean) if o.ean else None
        if twin and norm(twin.matched_category) == norm(spec.category):
            # mesmo EAN em outro mercado: mesmo produto, reaproveita a normalização
            p = p or Product(ifood_item_id=o.sku_id, merchant_id=o.merchant_id, name=o.name)
            for f in ("brand", "category", "variety_terms", "pack_size", "pack_unit", "quality_tier",
                      "matched_category", "matched"):
                setattr(p, f, getattr(twin, f))
            p.ean = o.ean
            s.add(p)
            cached[o.sku_id] = p
            continue
        pending.setdefault(o.item_id, []).append(o)

    for item_id, group in pending.items():
        spec = specs[item_id]
        unique: dict[str, Offer] = {}
        for o in group:
            unique.setdefault(o.sku_id, o)
        group = list(unique.values())
        for start in range(0, len(group), BATCH):
            chunk = group[start:start + BATCH]
            parsed = await llm.normalize_products(spec.category, spec.query, [o.name for o in chunk])
            for o, info in zip(chunk, parsed):
                p = cached.get(o.sku_id) or Product(ifood_item_id=o.sku_id, merchant_id=o.merchant_id, name=o.name)
                p.ean = o.ean
                p.name = o.name
                p.category = spec.category
                p.brand = info.get("brand")
                p.variety_terms = info.get("variety_terms") or []
                p.pack_size = info.get("pack_size")
                p.pack_unit = info.get("pack_unit")
                p.quality_tier = int(info.get("quality_tier") or 2)
                p.matched_category = spec.category
                # o LLM às vezes aceita "leite em pó" como "leite"; a trava de derivado vale sempre
                p.matched = bool(info.get("matches")) and not heuristics.is_derivative(spec.category, o.name, spec.query)
                p.parsed_at = now()
                s.add(p)
                cached[o.sku_id] = p
    s.commit()
    return cached


def _variety_ok(terms: list[str], product: Product) -> list[str]:
    """Termos pedidos que faltam no SKU; sinônimo de rótulo conta ("Zero Açúcar" atende "sem açúcar")."""
    have = " ".join(product.variety_terms or []) + " " + product.name
    return [t for t in terms if not heuristics.has_term(have, t)]


async def build(s: Session, specs: list[ItemSpec], offers: list[Offer], *,
                affinity: dict[str, dict[str, float]] | None = None) -> NormalizedSet:
    spec_by_id = {sp.id: sp for sp in specs}
    products = await _ensure_products(s, spec_by_id, offers)
    affinity = affinity or {}

    rejected: dict[int, dict[str, int]] = {sp.id: {} for sp in specs}
    pool: dict[int, list[tuple[float, SkuInfo, Candidate]]] = {sp.id: [] for sp in specs}

    def reject(item_id: int, why: str) -> None:
        rejected[item_id][why] = rejected[item_id].get(why, 0) + 1

    # Conferência literal da variedade erra com sinônimos ("Zero Açúcar" para "sem açúcar"). Para os
    # produtos em que ela diz que falta algo, o LLM decide com bom senso, em lote por item.
    variety_override: dict[tuple[int, str], list[str]] = {}
    doubtful: dict[int, list[str]] = {}
    for o in offers:
        spec = spec_by_id[o.item_id]
        p = products.get(o.sku_id)
        terms = list(dict.fromkeys(list(spec.variety_terms) + list(spec.rules.variety_terms) + list(spec.required_terms)))
        if p is not None and p.matched and terms and _variety_ok(terms, p):
            doubtful.setdefault(o.item_id, [])
            if o.name not in doubtful[o.item_id]:
                doubtful[o.item_id].append(o.name)
    for item_id, names in doubtful.items():
        spec = spec_by_id[item_id]
        terms = list(dict.fromkeys(list(spec.variety_terms) + list(spec.rules.variety_terms) + list(spec.required_terms)))
        for name, missing in zip(names, await llm.check_variety(terms, names)):
            variety_override[(item_id, name)] = missing

    for o in offers:
        spec = spec_by_id[o.item_id]
        p = products.get(o.sku_id)
        r = spec.rules
        # a trava de derivado vale também para o que veio do cache (normalizado antes dela existir)
        if p is None or not p.matched or heuristics.is_derivative(spec.category, o.name, spec.query):
            reject(o.item_id, "não é o produto")
            continue
        if r.is_blocked_brand(p.brand):
            reject(o.item_id, "marca bloqueada")
            continue
        if o.merchant_id in r.block_merchants:
            reject(o.item_id, "mercado bloqueado")
            continue
        wanted_terms = list(dict.fromkeys(list(spec.variety_terms) + list(r.variety_terms)))
        missing_terms = variety_override.get((o.item_id, o.name), _variety_ok(wanted_terms, p))
        missing_terms = [t for t in missing_terms if t in wanted_terms]
        hard_terms = r.variety_terms if r.variety_hard else []
        if any(t in missing_terms for t in hard_terms):
            reject(o.item_id, "variedade obrigatória ausente")
            continue

        required, unit_mode = required_amount(spec)
        size = size_in_item_units(spec, unit_mode, p.pack_size, p.pack_unit)
        size_known = unit_mode == "pack" or (bool(p.pack_size) and p.pack_unit == unit_mode)
        est_cost = o.price * packs_for(required, size)

        reasons: list[str] = []
        sub_pen = 0.0
        chain = list(r.brand_chain)
        allow_others = r.allow_other_brands
        if spec.brand and not chain:
            chain, allow_others = [spec.brand], True
        if chain:
            pos = r.brand_position(p.brand) if r.brand_chain else (0 if norm(p.brand) == norm(spec.brand) else None)
            if pos is None:
                reasons.append("marca fora da preferência")
                sub_pen += (60.0 + est_cost) if not allow_others else (2.0 + 0.25 * est_cost)
            elif pos > 0:
                reasons.append(f"marca {pos + 1}ª da cadeia")
                sub_pen += 0.5 + 0.08 * pos * est_cost
        if missing_terms:
            reasons.append("variedade diferente: falta " + ", ".join(missing_terms))
            sub_pen += len(missing_terms) * (1.0 + 0.15 * est_cost)

        brand_aff = affinity.get(norm(spec.category), {}).get(norm(p.brand), 0.0)
        tier = min(3.0, max(1.0, p.quality_tier + brand_aff))
        quality_pen = (3.0 - tier) / 2.0 * 0.3 * est_cost

        info = SkuInfo(sku_id=o.sku_id, item_id=o.item_id, merchant_id=o.merchant_id, name=o.name, brand=p.brand,
                       variety_terms=list(p.variety_terms or []), pack_size=p.pack_size, pack_unit=p.pack_unit,
                       quality_tier=p.quality_tier, price=o.price, list_price=o.list_price, size=size, image=o.image,
                       reasons=reasons, size_known=size_known, quality_penalty=round(quality_pen, 2),
                       substitution_penalty=round(sub_pen, 2))
        cand = Candidate(sku_id=o.sku_id, item_id=o.item_id, merchant_id=o.merchant_id, name=o.name, brand=p.brand,
                         price=o.price, size=size, quality_penalty=round(quality_pen, 2),
                         substitution_penalty=round(sub_pen, 2))
        score = o.price / size + (sub_pen + 0.5 * quality_pen) / max(required, 1e-6)
        pool[o.item_id].append((score, info, cand))

    candidates: list[Candidate] = []
    skus: dict[str, SkuInfo] = {}
    items: list[Item] = []
    for spec in specs:
        entries = pool[spec.id]
        required, unit_mode = required_amount(spec)

        def keep(entries: list, ok, reason: str) -> list:
            """Aplica o filtro só se sobrar alguma opção; senão mantém tudo (melhor que deixar o item de fora)."""
            fits = [e for e in entries if ok(e[1])]
            if fits and len(fits) < len(entries):
                rejected[spec.id][reason] = rejected[spec.id].get(reason, 0) + len(entries) - len(fits)
                return fits
            return entries

        if spec.pin_size:
            entries = keep(entries, lambda i: abs(i.size - spec.pin_size) <= 0.02 * spec.pin_size,
                           "tamanho diferente do escolhido")
        if spec.required_terms:
            def has_terms(i: SkuInfo) -> bool:
                missing = variety_override.get((spec.id, i.name))
                if missing is not None:
                    return not any(t in missing for t in spec.required_terms)
                text = " ".join(i.variety_terms) + " " + i.name
                return all(heuristics.has_term(text, t) for t in spec.required_terms)
            entries = keep(entries, has_terms, "fora da preferência declarada")
        if unit_mode != "pack":
            # sem tamanho no nome, o SKU seria tratado como se tivesse o tamanho pedido
            entries = keep(entries, lambda i: i.size_known, "tamanho desconhecido")
            entries = keep(entries, lambda i: i.size <= OVERSIZE * required, "embalagem grande demais")
            if spec.rules.quantity_exact:
                limit = required * spec.rules.max_overbuy
                entries = keep(entries, lambda i: packs_for(required, i.size) * i.size <= limit, "maior que o pedido")
        # só os cinco melhores por mercado entram no modelo
        by_merchant: dict[str, list] = {}
        for e in sorted(entries, key=lambda e: e[0]):
            by_merchant.setdefault(e[1].merchant_id, []).append(e)
        kept = [e for lst in by_merchant.values() for e in lst[:TOP_PER_MERCHANT]]

        if spec.rules.notes and kept:
            scores = await llm.score_notes(spec.rules.notes, [e[1].name for e in kept])
            rescored = []
            for (score, info, cand), sc in zip(kept, scores):
                est = cand.price * packs_for(required_amount(spec)[0], cand.size)
                pen = round((1.0 - sc) * 0.2 * est, 2)
                rescored.append((score, info, Candidate(**{**cand.__dict__, "note_penalty": pen})))
            kept = rescored

        required, _ = required_amount(spec)
        costs = [c.price * packs_for(required, c.size) for _, _, c in kept]
        missing_pen = max(25.0, 2.5 * statistics.median(costs)) if costs else 25.0
        items.append(Item(id=spec.id, required=required, missing_penalty=round(missing_pen, 2)))
        for _, info, cand in kept:
            candidates.append(cand)
            skus[sku_key(spec.id, info.sku_id)] = info
    return NormalizedSet(items=items, candidates=candidates, skus=skus, rejected=rejected)
