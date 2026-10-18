"""Fechamento do pedido: confere o total, envia ao iFood, guarda o Pix e acompanha a entrega (seção 8)."""

from __future__ import annotations

import copy
import re
from datetime import datetime, timedelta, timezone

from sqlmodel import Session, select

from app.cart.changes import ActionError, _apply_cart, _refresh_totals, plan_view
from app.core.db import today
from app.core.models import ListItem, Order, OrderLine, Plan, ShoppingList
from app.mcp.client import McpToolError
from app.mcp.ifood import Ifood
from app.pipeline.materialize import _summary
from app.preferences import learning

PIX_KEY = re.compile(r"pix|qr|brcode|emv|copy|paste|copia|cola", re.I)


def _find_pix(body) -> str | None:
    if isinstance(body, dict):
        for k, v in body.items():
            if isinstance(v, str) and PIX_KEY.search(k) and len(v) > 20:
                return v
            found = _find_pix(v)
            if found:
                return found
    elif isinstance(body, list):
        for v in body:
            found = _find_pix(v)
            if found:
                return found
    elif isinstance(body, str) and body.startswith("000201"):
        return body
    return None


async def checkout(s: Session, api: Ifood, plan: Plan, *, accept_changes: bool = False) -> dict:
    if plan.status == "ordered":
        raise ActionError("este carrinho já virou pedido", 409)
    result = copy.deepcopy(plan.result)
    chosen = result["chosen"]
    if not chosen["carts"]:
        raise ActionError("o carrinho está vazio")

    # confirma que nada mudou desde que o total foi mostrado
    changes = []
    for cart in chosen["carts"]:
        body = await api.get_cart(cart["cart_id"])
        new = _summary(body)
        if abs(new["total"] - cart["summary"]["total"]) > 0.009:
            changes.append({"merchant_name": cart["merchant_name"], "before": cart["summary"]["total"],
                            "after": new["total"]})
        _apply_cart(cart, body)
        if cart["blocking"]:
            message = (body.get("warning") or {}).get("userFriendlyMessage") or "carrinho não está pronto"
            raise ActionError(f"{cart['merchant_name']}: {message}", 409)
    _refresh_totals(result)
    if changes and not accept_changes:
        plan.result = result
        s.add(plan)
        s.commit()
        return {"status": "changed", "changes": changes, "plan": plan_view(plan)}

    orders = []
    lst = s.get(ShoppingList, plan.list_id)
    categories = {i["id"]: i["category"] for i in result["items"]}
    for cart in chosen["carts"]:
        body = await api.submit_order(cart["cart_id"])
        order_id = body.get("orderId")
        pix = _find_pix(body)
        if order_id and not pix:
            try:
                pix = _find_pix(await api.get_order(order_id))
            except McpToolError:
                pix = None
        order = Order(plan_id=plan.id, merchant_id=cart["merchant_id"], merchant_name=cart["merchant_name"],
                      ifood_cart_id=cart["cart_id"], ifood_order_id=order_id, total=cart["summary"]["total"],
                      status="submitted", pix={"code": pix} if pix else None)
        s.add(order)
        s.commit()
        s.refresh(order)
        for ln in cart["lines"]:
            sub = any(c.get("item_id") == ln["item_id"] and c["type"] in ("brand_substitution", "variety", "brand_chain_info")
                      for c in result.get("decisions", []))
            s.add(OrderLine(order_id=order.id, list_item_id=ln["item_id"], ifood_item_id=ln["sku_id"], name=ln["name"],
                            brand=ln.get("brand"), unit_price=ln["unit_price"], qty=ln["quantity"],
                            was_substitute=sub, user_action=ln.get("user_action")))
            if not ln.get("user_action"):
                learning.record(s, plan.user_id, categories.get(ln["item_id"], ""), ln.get("brand"), "chosen",
                                unit_price=ln["unit_price"], list_item_id=ln["item_id"])
        orders.append({"id": order.id, "ifood_order_id": order_id, "merchant_name": cart["merchant_name"],
                       "total": order.total, "pix": pix})
    plan.status = "ordered"
    plan.result = result
    s.add(plan)
    if lst and lst.recurrence_days:
        lst.next_due = today() + timedelta(days=lst.recurrence_days)
        s.add(lst)
    for item in s.exec(select(ListItem).where(ListItem.list_id == plan.list_id)).all():
        if item.recurrence_days:
            item.next_due = today() + timedelta(days=item.recurrence_days)
            s.add(item)
    s.commit()
    learning.update_rules(s, plan.user_id)
    learning.update_periodicity(s, plan.list_id)
    return {"status": "ordered", "orders": orders, "plan": plan_view(plan)}


_RETRY = re.compile(r"(\d+)\s*(second|segundo|s\b)", re.I)


async def track(s: Session, api: Ifood, order: Order) -> dict:
    """trackOrder respeitando o intervalo do retryMessage e parando no pollingStop."""
    t = dict(order.tracking or {})
    now = datetime.now(timezone.utc).timestamp()
    if t.get("stopped") or (t.get("next_poll_at") and now < t["next_poll_at"]):
        return {"order_id": order.id, **t, "cached": True}
    if not order.ifood_order_id:
        raise ActionError("pedido sem orderId")
    body = await api.track_order(order.ifood_order_id)
    hints = body.get("agentHints") or {}
    retry = hints.get("retryMessage")
    wait = None
    if retry:
        m = _RETRY.search(retry)
        wait = int(m.group(1)) if m else 30
    t = {"status": body.get("status"), "history": body.get("statusHistory", []), "retry_message": retry,
         "stopped": bool(hints.get("pollingStop")), "stop_message": hints.get("stopMessage"),
         "next_poll_at": (now + wait) if wait and not hints.get("pollingStop") else None,
         "retry_after_s": wait if not hints.get("pollingStop") else None}
    order.tracking = t
    order.status = (body.get("status") or {}).get("code", order.status)
    s.add(order)
    s.commit()
    return {"order_id": order.id, **t, "cached": False}
