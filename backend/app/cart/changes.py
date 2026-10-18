"""Alterações num carrinho já montado: cartões de decisão, trocas e recálculo (seção 7.6).

O chat fica em `app/ai/chat.py` e o fechamento do pedido em `checkout.py`."""

from __future__ import annotations

import asyncio
import copy
import logging
import statistics

from sqlmodel import Session

from app.core.models import Plan, ShoppingList, User
from app.mcp.ifood import Ifood
from app.pipeline import optimize
from app.pipeline.materialize import _blocking, _summary
from app.preferences import learning
from app.preferences.rules import brl
from app.preferences.store import rule_view, save_rule

log = logging.getLogger(__name__)


_plan_locks: dict[int, asyncio.Lock] = {}


def plan_lock(plan_id: int) -> asyncio.Lock:
    """Uma alteração por vez em cada plano. Cada ação copia o resultado, chama o iFood e salva; duas ao
    mesmo tempo (um cartão e o agente do chat, por exemplo) fariam a segunda apagar a primeira."""
    return _plan_locks.setdefault(plan_id, asyncio.Lock())


class ActionError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _chosen(plan: Plan) -> dict:
    chosen = (plan.result or {}).get("chosen")
    if not chosen:
        raise ActionError("o plano ainda não tem carrinho")
    return chosen


def _find_line(chosen: dict, item_id: int) -> tuple[dict, dict] | tuple[None, None]:
    for cart in chosen["carts"]:
        for ln in cart["lines"]:
            if ln["item_id"] == item_id:
                return cart, ln
    return None, None


def _refresh_totals(result: dict) -> None:
    chosen = result["chosen"]
    chosen["total"] = round(sum(c["summary"]["total"] for c in chosen["carts"]), 2)
    chosen["ok"] = bool(chosen["carts"]) and not any(c["blocking"] for c in chosen["carts"])
    single = result.get("single_total")
    result["savings_vs_single"] = (round(single - chosen["total"], 2)
                                   if single and len(chosen["carts"]) > 1 else None)


def _apply_cart(cart: dict, body: dict) -> None:
    """Atualiza o carrinho guardado com a resposta (CartDetailsResponse) do MCP."""
    cart["summary"] = _summary(body)
    cart["warning"] = body.get("warning")
    cart["blocking"] = _blocking(body.get("warning")) or not cart.get("payment") or not cart.get("delivery")
    by_sku = {ci["itemId"]: ci for ci in body.get("items", [])}
    for ln in cart["lines"]:
        ci = by_sku.get(ln["sku_id"])
        if ci:
            ln["cart_item_id"] = ci.get("cartItemId")
            ln["quantity"] = ci.get("quantity", ln["quantity"])
            ln["unit_price"] = ci.get("unitPrice", ln["unit_price"])


def _candidate(result: dict, item_id: int, sku_id: str) -> dict | None:
    for c in result.get("candidates", {}).get(str(item_id), []):
        if c["sku_id"] == sku_id:
            return c
    return None


async def apply_action(s: Session, api: Ifood, plan: Plan, item_id: int, action: str, *,
                       sku_id: str | None = None, quantity: int | None = None) -> dict:
    """Executa a ação no carrinho real e devolve o resultado atualizado do plano."""
    _chosen(plan)
    result = copy.deepcopy(plan.result)
    chosen = result["chosen"]
    cart, line = _find_line(chosen, item_id)
    user_id = plan.user_id
    category = next((i["category"] for i in result["items"] if i["id"] == item_id), "")

    if action == "accept":
        if line:
            learning.record(s, user_id, category, line.get("brand"), "accepted", unit_price=line["unit_price"],
                            list_item_id=item_id)
            line["user_action"] = "accepted"
    elif action == "remove":
        if not line:
            raise ActionError("item não está no carrinho")
        body = await api.remove_item(cart["cart_id"], line["cart_item_id"])
        learning.record(s, user_id, category, line.get("brand"), "rejected", unit_price=line["unit_price"],
                        list_item_id=item_id)
        cart["lines"].remove(line)
        result.setdefault("removed_items", []).append(item_id)
        if "cartId" not in body:
            # o MCP apaga o carrinho quando sai o último item
            chosen["carts"].remove(cart)
        else:
            _apply_cart(cart, body)
    elif action == "set_quantity":
        if not line or not quantity or quantity < 1:
            raise ActionError("quantidade inválida")
        body = await api.update_item(cart["cart_id"], line["cart_item_id"], line["sku_id"], quantity)
        _apply_cart(cart, body)
    elif action == "swap":
        cand = _candidate(result, item_id, sku_id or "")
        if not cand:
            raise ActionError("alternativa desconhecida para este item")
        target = next((c for c in chosen["carts"] if c["merchant_id"] == cand["merchant_id"]), None)
        if target is None:
            raise ActionError("a alternativa é de um mercado fora deste carrinho; recrie o carrinho para trocar de mercado")
        required = next(i["required"] for i in result["items"] if i["id"] == item_id)
        packs = quantity or max(1, -int(-required // max(cand["size"], 1e-9)))
        # adiciona antes de remover: tirar o último item apagaria o carrinho
        body = await api.add_item(target["cart_id"], cand["sku_id"], packs)
        target["lines"].append({"item_id": item_id, "sku_id": cand["sku_id"], "packs": packs, "name": cand["name"],
                                "brand": cand["brand"], "unit_price": cand["price"], "image": cand.get("image"),
                                "quantity": packs, "cart_item_id": None, "user_action": "swapped"})
        _apply_cart(target, body)
        learning.record(s, user_id, category, cand.get("brand"), "swapped_to", unit_price=cand["price"],
                        list_item_id=item_id)
        if line:
            body = await api.remove_item(cart["cart_id"], line["cart_item_id"])
            learning.record(s, user_id, category, line.get("brand"), "swapped_from", unit_price=line["unit_price"],
                            list_item_id=item_id)
            cart["lines"].remove(line)
            if "cartId" not in body:
                chosen["carts"].remove(cart)
            else:
                _apply_cart(cart, body)
    else:
        raise ActionError(f"ação desconhecida: {action}")

    _refresh_totals(result)
    if action in ("swap", "remove", "set_quantity"):
        _update_replan_card(s, plan, result)
    plan.result = result
    s.add(plan)
    s.commit()
    return result


# ------------------------------------------------------- recálculo depois de trocas

def pins_from(result: dict) -> dict:
    """Escolhas do usuário no carrinho que um novo cálculo precisa manter."""
    pins: dict = {}
    sizes = {(c["item_id"], c["sku_id"]): c["size"] for cands in result.get("candidates", {}).values() for c in cands}
    for cart in result["chosen"]["carts"]:
        for ln in cart["lines"]:
            if ln.get("user_action") == "swapped" and (ln["item_id"], ln["sku_id"]) in sizes:
                pins[str(ln["item_id"])] = {"size": sizes[(ln["item_id"], ln["sku_id"])]}
    for item_id in result.get("removed_items", []):
        pins[str(item_id)] = {"removed": True}
    return pins


def estimate_replan(s: Session, plan: Plan, result: dict) -> optimize.Solution | None:
    """Refaz a otimização em memória com as escolhas do usuário, sem chamar o iFood."""
    from app.pipeline.runner import effective

    pins = pins_from(result)
    user = s.get(User, plan.user_id)
    lst = s.get(ShoppingList, plan.list_id)
    max_merchants, split_min = effective(user, lst)
    items, candidates = [], []
    for it in result["items"]:
        pin = pins.get(str(it["id"]), {})
        if pin.get("removed"):
            continue
        cands = result.get("candidates", {}).get(str(it["id"]), [])
        if pin.get("size"):
            cands = [c for c in cands if abs(c["size"] - pin["size"]) <= 0.02 * pin["size"]]
        costs = [c["price"] * optimize.packs_for(it["required"], c["size"]) for c in cands]
        items.append(optimize.Item(it["id"], it["required"], max(25.0, 2.5 * statistics.median(costs)) if costs else 25.0))
        candidates += [optimize.Candidate(c["sku_id"], it["id"], c["merchant_id"], c["name"], c.get("brand"), c["price"],
                                          c["size"], c.get("substitution_penalty", 0.0),
                                          c.get("note_penalty", 0.0)) for c in cands]
    merchants = [optimize.Merchant(m_id, m["name"], m["delivery_fee"], m["minimum_order"])
                 for m_id, m in result.get("merchants", {}).items()]
    problem = optimize.Problem(items=items, merchants=merchants, candidates=candidates,
                               max_merchants=max_merchants)
    solutions = optimize.solve_with_alternatives(problem, split_min, n_alternatives=1)
    return solutions[0] if solutions and solutions[0].merchants else None


def _update_replan_card(s: Session, plan: Plan, result: dict) -> None:
    """Depois de uma troca, avisa se refazer o carrinho do zero sai bem mais barato."""
    chosen = result["chosen"]
    cards = [c for c in result.get("decisions", []) if c["type"] != "replan"]
    current = {c["merchant_id"] for c in chosen["carts"]}
    best = estimate_replan(s, plan, result)
    if best is not None:
        gain = round(chosen["total"] - best.estimated_total, 2)
        if gain > max(3.0, 0.05 * chosen["total"]) and set(best.merchants) != current:
            names = " e ".join(result["merchants"][m]["name"] for m in best.merchants)
            cards.append({
                "id": max([c["id"] for c in cards], default=-1) + 1, "type": "replan", "item_id": None,
                "category": None, "title": "Dá para pagar menos", "question": True, "status": "open",
                "text": (f"Com as suas trocas, comprar tudo em {names} sai por volta de {brl(best.estimated_total)}, "
                         f"contra {brl(chosen['total'])} agora. Quer que eu refaça o carrinho mantendo suas escolhas?"),
                "default_text": "", "alternatives": [], "saving": gain,
            })
    # o cartão da divisão precisa refletir o total de agora
    for card in cards:
        if card["type"] == "split" and card["status"] == "open":
            single = result.get("single_total")
            saving = round(single - chosen["total"], 2) if single else None
            card["saving"] = saving
            if len(chosen["carts"]) < 2:
                card["status"], card["text"] = "resolved", "A compra não está mais dividida."
            elif saving is not None and saving <= 0:
                card["text"] = "Com as trocas, dividir a compra deixou de compensar."
    result["decisions"] = cards


async def answer_decision(s: Session, api: Ifood, plan: Plan, card_id: int, action: str, *,
                          sku_id: str | None = None, quantity: int | None = None,
                          rule: dict | None = None) -> dict:
    cards = plan.result.get("decisions", [])
    card = next((c for c in cards if c["id"] == card_id), None)
    if card is None:
        raise ActionError("cartão não encontrado", 404)
    result = plan.result
    if card.get("item_id") is not None and action != "dismiss":
        result = await apply_action(s, api, plan, card["item_id"], action, sku_id=sku_id, quantity=quantity)
    saved = save_rule(s, plan.user_id, rule, source="user") if rule else None
    result = copy.deepcopy(plan.result)
    for c in result["decisions"]:
        if c["id"] == card_id:
            c["status"] = "resolved"
            c["resolution"] = action
    plan.result = result
    s.add(plan)
    s.commit()
    if saved:
        learning.update_rules(s, plan.user_id)
    return {"plan": plan_view(plan), "rule": rule_view(saved) if saved else None}


def plan_view(plan: Plan) -> dict:
    data = plan.model_dump(mode="json")
    result = dict(data.get("result") or {})
    result.pop("candidates", None)
    result.pop("discovery_errors", None)
    data["result"] = result
    return data
