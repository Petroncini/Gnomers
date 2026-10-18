"""Carrinhos reais, cupom, entrega e total final (seção 7.5 do plano).

O preço mostrado ao usuário sai daqui: para cada uma das até três soluções do
solver, o sistema monta os carrinhos de verdade, testa os cupons, escolhe a entrega
e pré-seleciona Pix. A solução vencedora é a de menor soma de `totalAmount` real.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from app.mcp.client import McpToolError
from app.mcp.ifood import Ifood
from app.pipeline.normalize import SkuInfo, sku_key
from app.pipeline.optimize import Solution

log = logging.getLogger(__name__)
ALT_MARGIN = 1.15


def _summary(cart: dict) -> dict:
    s = cart.get("summary") or {}
    return {
        "subtotal": s.get("subtotalAmount", 0.0), "delivery": s.get("deliveryFeeAmount", 0.0),
        "fees": s.get("feeTotalAmount", 0.0), "benefits": s.get("benefitsTotalAmount", 0.0),
        "voucher": s.get("voucherTotalAmount", 0.0), "total": s.get("totalAmount", 0.0),
    }


def _slot_start(slot: dict) -> datetime:
    return datetime.fromisoformat(slot["from"].replace("Z", ""))


def choose_delivery(methods: list[dict], deliver_at: str | None) -> tuple[dict | None, dict | None, str | None]:
    """Método de entrega e horário. Devolve (método, horário, motivo de falha).

    Com `deliver_at`, só servem métodos com um horário que cubra o momento pedido.
    Sem ele, vale o mais barato; se o método só tiver horários agendados (mercado
    fechado agora), usa o primeiro horário disponível.
    """
    delivery = [m for m in methods if m.get("deliveryMethodMode", "DELIVERY") == "DELIVERY"]
    if not delivery:
        return None, None, "o mercado não oferece entrega neste endereço"
    if deliver_at:
        target = datetime.fromisoformat(deliver_at)
        options = []
        for m in delivery:
            for slot in m.get("timeslots") or []:
                start, end = _slot_start(slot), datetime.fromisoformat(slot["to"].replace("Z", ""))
                if start <= target < end:
                    # horário que começa mais perto do pedido, depois o frete mais barato
                    options.append(((target - start).total_seconds(), m.get("fee") or 0, m, slot))
        if not options:
            return None, None, f"nenhum horário de entrega cobre {target:%d/%m %H:%M}"
        _, _, method, slot = min(options, key=lambda o: (o[0], o[1]))
        return method, slot, None
    method = min(delivery, key=lambda m: (m.get("fee") or 0, (m.get("estimatedMinutes") or {}).get("max", 999)))
    slots = method.get("timeslots") or []
    slot = min(slots, key=_slot_start) if slots else None
    return method, slot, None


def voucher_value(v: dict, subtotal: float) -> float:
    """Desconto estimado de um cupom do listVouchers, para escolher qual aplicar sem testar todos."""
    value = float(v.get("effectValue") or 0)
    if v.get("effectType") == "PERCENTAGE":
        value = subtotal * value / 100
        cap = v.get("maxDiscountValue")
        if cap:  # o sandbox manda 0 quando não há teto
            value = min(value, float(cap))
    return min(value, subtotal)


def _blocking(warning: dict | None) -> bool:
    if not warning:
        return False
    impact = warning.get("impact") or {}
    return impact.get("canProceedToCheckout") is False


async def _with_required_options(api: Ifood, merchant_id: str, item_id: str, lat: float, lng: float) -> list[dict]:
    """Escolhe a opção mais barata de cada grupo obrigatório (raro em mercado)."""
    detail = await api.get_item(merchant_id, item_id, lat, lng)
    options = []
    for group in detail.get("optionGroups") or []:
        need = int(group.get("minSelections") or 0)
        if need <= 0:
            continue
        cheapest = sorted(group.get("options", []), key=lambda o: o.get("additionalPrice") or 0)[:need]
        for opt in cheapest:
            options.append({"optionId": opt["optionId"], "quantity": 1, "customizations": []})
    return options


async def build_cart(api: Ifood, merchant_id: str, merchant_name: str, address_id: str,
                     lines: list[dict], lat: float, lng: float, emit=None, deliver_at: str | None = None) -> dict:
    """Cria e configura um carrinho. `lines` traz item_id (lista), sku_id e packs."""
    items = [{"itemId": ln["sku_id"], "quantity": ln["packs"], "options": []} for ln in lines]
    try:
        cart = await api.create_cart(merchant_id, address_id, items)
    except McpToolError as exc:
        if "OPTION" not in exc.code.upper() and "CUSTOMIZ" not in exc.code.upper():
            raise
        for it in items:
            it["options"] = await _with_required_options(api, merchant_id, it["itemId"], lat, lng)
        cart = await api.create_cart(merchant_id, address_id, items)
    cart_id = cart["cartId"]

    # entrega: método e horário (agendado quando o mercado está fechado ou quando pedido)
    methods = await api.delivery_methods(cart_id)
    delivery, slot, delivery_problem = choose_delivery(methods, deliver_at)
    current = cart.get("deliveryMethod") or {}
    already = (delivery is not None and current.get("deliveryMethodId") == delivery["deliveryMethodId"]
               and (current.get("timeSlot") or {}).get("from") == (slot or {}).get("from"))
    if delivery and not already:
        try:
            cart = await api.set_delivery(cart_id, delivery["deliveryMethodId"], slot.get("timeslotId") if slot else None)
        except McpToolError as exc:
            # o createCart já vem com a entrega preenchida (o primeiro horário); se trocar falhar, fica com ela
            current = cart.get("deliveryMethod")
            if not current:
                raise
            log.warning("setCartDelivery falhou (%s); mantendo a entrega que o carrinho já tem", exc.code)
            delivery = {"deliveryMethodId": current.get("deliveryMethodId") or delivery["deliveryMethodId"],
                        "deliveryMethodName": current.get("deliveryMethodName"), "fee": current.get("fee"),
                        "estimatedMinutes": current.get("estimatedMinutes")}
            slot = current.get("timeSlot")
            if deliver_at:
                delivery_problem = "o iFood não deixou escolher o horário; ficou o primeiro disponível"

    # cupom: o listVouchers já diz o desconto de cada um; aplica só o melhor (e o próximo, se ele falhar)
    try:
        vouchers = await api.list_vouchers(cart_id)
    except McpToolError as exc:
        log.warning("listVouchers falhou (%s); seguindo sem cupom", exc.code)
        vouchers = []
    base_total = _summary(cart)["total"]
    subtotal = _summary(cart)["subtotal"]
    best, tested, applied = None, [], False
    for v in sorted(vouchers, key=lambda v: -voucher_value(v, subtotal))[:2]:
        if voucher_value(v, subtotal) <= 0:
            break
        try:
            trial = await api.set_voucher(cart_id, v["code"], v["campaignId"])
        except McpToolError as exc:
            tested.append({"code": v["code"], "error": exc.code})
            continue
        applied = True
        total = _summary(trial)["total"]
        tested.append({"code": v["code"], "total": total})
        if total < base_total - 0.001:
            best, cart = v, trial
            break
    if best is None and applied:
        # cupom que não baixou o total não pode ficar no carrinho (o checkout reclama de cupom inaplicável)
        try:
            cart = await api.remove_voucher(cart_id)
        except McpToolError as exc:
            log.warning("removeVoucher falhou (%s)", exc.code)
    best_total = _summary(cart)["total"]

    # pagamento: Pix pré-selecionado (o cartão exige cardTokenId, que o MCP não expõe).
    # A resposta do setCartPayment já é o carrinho completo: não precisa de getCart no fim.
    payment = None
    try:
        methods = await api.payment_methods(merchant_id)
    except McpToolError as exc:
        log.warning("listPaymentMethods falhou (%s)", exc.code)
        methods = []
    pix = next((p for p in methods if p.get("kind") == "PIX"), None)
    if pix:
        cart = await api.set_payment(cart_id, pix["paymentMethodId"])
        payment = {"kind": "PIX", "paymentMethodId": pix["paymentMethodId"]}
    else:
        cart = await api.get_cart(cart_id)

    by_sku = {}
    for ci in cart.get("items", []):
        by_sku.setdefault(ci["itemId"], ci)
    out_lines = []
    for ln in lines:
        ci = by_sku.get(ln["sku_id"]) or {}
        out_lines.append({**ln, "cart_item_id": ci.get("cartItemId"),
                          "unit_price": ci.get("unitPrice", ln.get("unit_price")),
                          "quantity": ci.get("quantity", ln["packs"])})
    return {
        "merchant_id": merchant_id, "merchant_name": cart.get("merchant", {}).get("merchantName") or merchant_name,
        "cart_id": cart_id, "lines": out_lines, "summary": _summary(cart),
        "minimum_order": cart.get("merchant", {}).get("orderMinimumPrice"),
        "voucher": ({"code": best["code"], "description": best.get("description"),
                     "discount": round(base_total - best_total, 2)} if best else None),
        "vouchers_tested": tested,
        "delivery": ({"id": delivery["deliveryMethodId"], "name": delivery.get("deliveryMethodName"),
                      "fee": delivery.get("fee"), "eta": delivery.get("estimatedMinutes"),
                      "slot": {"from": slot["from"], "to": slot["to"]} if slot else None} if delivery else None),
        "delivery_problem": delivery_problem,
        "payment": payment,
        "warning": cart.get("warning"),
        "blocking": _blocking(cart.get("warning")) or payment is None or delivery is None,
        "deeplink": cart.get("appDeeplink"),
    }


def _lines_for(solution: Solution, merchant_id: str, skus: dict[str, SkuInfo]) -> list[dict]:
    lines = []
    for p in solution.picks:
        if p.merchant_id != merchant_id:
            continue
        info = skus[sku_key(p.item_id, p.sku_id)]
        lines.append({"item_id": p.item_id, "sku_id": p.sku_id, "packs": p.packs, "name": info.name,
                      "brand": info.brand, "unit_price": p.price, "image": info.image})
    return lines


async def materialize(api: Ifood, solutions: list[Solution], skus: dict[str, SkuInfo], merchants: dict,
                      address_id: str, lat: float, lng: float, emit=None, deliver_at: str | None = None) -> list[dict]:
    """Monta os carrinhos de cada solução; carrinhos idênticos entre soluções são reaproveitados."""
    cache: dict[tuple, asyncio.Task] = {}

    def cart_task(m_id: str, lines: list[dict]) -> asyncio.Task:
        key = (m_id, tuple(sorted((ln["sku_id"], ln["packs"]) for ln in lines)))
        if key not in cache:
            cache[key] = asyncio.create_task(
                build_cart(api, m_id, merchants[m_id].name, address_id, lines, lat, lng, emit, deliver_at))
        return cache[key]

    # alternativa com estimativa muito pior que a melhor não vira carrinho (cada uma custa ~6 chamadas)
    best_estimate = solutions[0].estimated_total if solutions else 0
    solutions = [sol for i, sol in enumerate(solutions) if i == 0 or sol.estimated_total <= ALT_MARGIN * best_estimate]
    plans = []
    for idx, sol in enumerate(solutions):
        tasks = [(m, cart_task(m, _lines_for(sol, m, skus))) for m in sol.merchants]
        plans.append((idx, sol, tasks))

    results = []
    for idx, sol, tasks in plans:
        carts, errors = [], []
        for m_id, task in tasks:
            try:
                carts.append(await task)
            except (McpToolError, ValueError) as exc:
                errors.append({"merchant_id": m_id, "merchant_name": merchants[m_id].name,
                               "error": exc.to_dict() if isinstance(exc, McpToolError) else str(exc)})
        total = round(sum(c["summary"]["total"] for c in carts), 2)
        ok = not errors and carts and not any(c["blocking"] for c in carts)
        results.append({
            "index": idx, "merchants": sol.merchants, "carts": carts, "errors": errors, "ok": bool(ok),
            "total": total, "estimated_total": sol.estimated_total, "objective": sol.objective,
            "missing": sol.missing,
        })
        if emit:
            await emit("cart", {"message": f"Opção {idx + 1}: R$ {total:.2f}" + ("" if ok else " (com problema)"),
                                "index": idx, "total": total, "ok": bool(ok)})
    return results


def pick_winner(results: list[dict]) -> dict | None:
    ok = [r for r in results if r["ok"]]
    pool = ok or [r for r in results if r["carts"]]
    if not pool:
        return None
    # menos faltas primeiro, depois o menor total real
    return min(pool, key=lambda r: (len(r["missing"]), r["total"]))
