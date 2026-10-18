"""Cartões de decisão (seção 7.6 do plano).

Compara a solução escolhida com o que a lista pediu. Cada cartão já mostra a escolha
aplicada e oferece aceitar, trocar ou remover. Substituições dentro de uma cadeia de
marcas que o próprio usuário definiu aparecem só como informação.
"""

from __future__ import annotations

from app import llm
from app.pipeline.normalize import ItemSpec, SkuInfo, required_amount, sku_key
from app.rules import norm

QTY_DRIFT = 0.25  # comprar 25% a mais do que o pedido já merece aviso


def _label(spec: ItemSpec) -> str:
    """Nome legível do item ("Sabão em pó"), sem a marca."""
    text = spec.query
    if spec.brand:
        text = " ".join(w for w in text.split() if norm(w) not in norm(spec.brand).split())
    return (text or spec.category).strip().capitalize()


def _alternatives(spec: ItemSpec, chosen: SkuInfo, skus: dict[str, SkuInfo], merchant_ids: list[str]) -> list[dict]:
    """Outros SKUs do mesmo item nos mercados da solução, para o botão "trocar"."""
    alts = [s for s in skus.values() if s.item_id == spec.id and s.sku_id != chosen.sku_id
            and s.merchant_id in merchant_ids]
    required, _ = required_amount(spec)
    alts.sort(key=lambda s: (bool(s.reasons), s.price / s.size))
    out = []
    for s in alts[:6]:
        out.append({"sku_id": s.sku_id, "merchant_id": s.merchant_id, "name": s.name, "brand": s.brand,
                    "price": s.price, "packs": max(1, -int(-required // s.size)), "reasons": s.reasons})
    return out


async def build_cards(specs: list[ItemSpec], winner: dict, skus: dict[str, SkuInfo], merchants: dict,
                      single_total: float | None) -> list[dict]:
    cards: list[dict] = []
    spec_by_id = {s.id: s for s in specs}
    merchant_ids = [c["merchant_id"] for c in winner["carts"]]
    picked: dict[int, tuple[dict, dict]] = {}
    for cart in winner["carts"]:
        for ln in cart["lines"]:
            picked[ln["item_id"]] = (ln, cart)

    for spec in specs:
        r = spec.rules
        label = _label(spec)
        if spec.id not in picked:
            cards.append({"type": "missing", "item_id": spec.id, "category": spec.category,
                          "title": f"{spec.raw_text}: não encontrado",
                          "default_text": f"Não achei \"{spec.raw_text}\" nos mercados escolhidos, então ficou de fora.",
                          "question": True, "alternatives": []})
            continue
        ln, cart = picked[spec.id]
        info = skus[sku_key(spec.id, ln["sku_id"])]
        chosen = {"sku_id": info.sku_id, "name": info.name, "brand": info.brand, "price": ln["unit_price"],
                  "packs": ln["quantity"], "merchant_id": cart["merchant_id"], "merchant_name": cart["merchant_name"]}
        alts = _alternatives(spec, info, skus, merchant_ids)
        base = {"item_id": spec.id, "category": spec.category, "chosen": chosen, "alternatives": alts}

        position = r.brand_position(info.brand)
        wanted_brand = spec.brand or (r.brand_chain[0] if r.brand_chain else None)
        if wanted_brand and norm(info.brand) != norm(wanted_brand):
            if position is not None and r.brand_chain_source in ("user", "chat"):
                # substituição autorizada pela cadeia do usuário: só informa
                cards.append({**base, "type": "brand_chain_info", "question": False,
                              "title": f"{label}: {info.brand}",
                              "default_text": f"{wanted_brand} não estava disponível; usei {info.brand}, "
                                              f"que é a sua {position + 1}ª opção."})
            else:
                cards.append({**base, "type": "brand_substitution", "question": True,
                              "title": f"{label}: {info.brand or 'outra marca'} no lugar de {wanted_brand}",
                              "default_text": f"Não achei {wanted_brand} com bom preço; coloquei {info.name} "
                                              f"por R$ {ln['unit_price']:.2f}."})
        elif not wanted_brand and not r.brand_chain and info.brand:
            # sem regra para a marca: o sistema escolheu; vale perguntar uma vez por categoria
            # sem regra para a marca: só informa; vira pergunta se o usuário abrir o chat no cartão
            cards.append({**base, "type": "brand_choice", "question": False,
                          "title": f"{label}: escolhi {info.brand}",
                          "default_text": f"Sem marca preferida de {label.lower()}, escolhi {info.name} "
                                          f"por R$ {ln['unit_price']:.2f}, o melhor custo-benefício."})

        missing_terms = [x for x in info.reasons if x.startswith("variedade")]
        if missing_terms and not r.variety_hard:
            cards.append({**base, "type": "variety", "question": True,
                          "title": f"{label}: variedade diferente",
                          "default_text": f"Coloquei {info.name}; {missing_terms[0]}."})

        required, unit = required_amount(spec)
        bought = ln["quantity"] * info.size
        if required > 0 and bought > required * (1 + QTY_DRIFT):
            unit_label = {"g": "g", "ml": "ml", "un": "un", "pack": "emb."}[unit]
            cards.append({**base, "type": "quantity", "question": True,
                          "title": f"{label}: quantidade maior",
                          "default_text": f"Pediu {required:g} {unit_label}, mas a embalagem disponível leva a "
                                          f"{bought:g} {unit_label}."})

    if len(winner["carts"]) > 1:
        names = " e ".join(c["merchant_name"] for c in winner["carts"])
        saving = round(single_total - winner["total"], 2) if single_total else None
        text = f"Dividi a compra entre {names}"
        text += f" e economizei R$ {saving:.2f} em relação a um mercado só." if saving and saving > 0 else "."
        cards.append({"type": "split", "item_id": None, "category": None, "question": True, "title": "Compra dividida",
                      "default_text": text, "saving": saving, "alternatives": []})

    for i, card in enumerate(cards):
        card["id"] = i
        card["status"] = "open" if card["question"] else "info"
        card["text"] = await llm.phrase_decision({k: v for k, v in card.items() if k != "alternatives"})
        card["text"] = card["text"] or card["default_text"]
    return cards
