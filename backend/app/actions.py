"""Ações sobre um plano pronto: cartões de decisão, chat, checkout e rastreio (seções 7.6 a 8)."""

from __future__ import annotations

import asyncio
import copy
import logging
import re
import statistics
from datetime import datetime, timedelta, timezone

from sqlmodel import Session, select

from app import learning, llm
from app.pipeline import optimize
from app.db import today
from app.mcp.client import McpToolError
from app.mcp.ifood import Ifood
from app.models import ListItem, Order, OrderLine, Plan, Rule, ShoppingList, User
from app.pipeline.materialize import _blocking, _summary
from app.rules import brl, describe, validate_payload


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
    quality, max_merchants, split_min = effective(user, lst)
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
                                          c["size"], c.get("quality_penalty", 0.0), c.get("substitution_penalty", 0.0),
                                          c.get("note_penalty", 0.0)) for c in cands]
    merchants = [optimize.Merchant(m_id, m["name"], m["delivery_fee"], m["minimum_order"])
                 for m_id, m in result.get("merchants", {}).items()]
    problem = optimize.Problem(items=items, merchants=merchants, candidates=candidates, alpha=quality,
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


def save_rule(s: Session, user_id: int, data: dict, *, source: str = "user") -> Rule:
    payload = validate_payload(data["kind"], data.get("payload", {}))
    rule = Rule(user_id=user_id, scope=str(data.get("scope", "global")), category=data.get("category") or None,
                kind=data["kind"], payload=payload, hardness=data.get("hardness", "soft"), source=source,
                confidence=1.0 if source == "user" else 0.8)
    s.add(rule)
    s.commit()
    s.refresh(rule)
    return rule


def rule_view(rule: Rule) -> dict:
    text = describe(rule.kind, rule.payload, rule.category)
    if rule.source == "learned" and rule.payload.get("evidence"):
        text += f" (aprendido de {rule.payload['evidence']} compras)"
    return {**rule.model_dump(mode="json"), "text": text}


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


async def chat(s: Session, api: Ifood, plan: Plan, card_id: int | None, message: str) -> dict:
    card = next((c for c in plan.result.get("decisions", []) if c["id"] == card_id), None) if card_id is not None else None
    if card is None and llm.enabled():
        # mensagem livre: o agente com tools consulta o iFood e mexe no carrinho; sem LLM, segue o fluxo abaixo
        out = await _agent_reply(s, api, plan, message)
        if out:
            return out
    if card is None:
        # sem cartão: pode ser pergunta ou correção de um item ("na verdade quero café em pó")
        items = [{"id": i["id"], "raw_text": i["raw_text"], "category": i["category"]} for i in plan.result.get("items", [])]
        context = {"type": "geral", "items": items, "alternatives": []}
    else:
        context = {k: card.get(k) for k in ("type", "item_id", "category", "chosen", "alternatives", "text")}
    extracted = await llm.extract_rule(message, context)
    if card is None and extracted.get("rule") and not extracted.get("is_question"):
        # sem cartão, uma preferência dita no chat vira proposta de regra (a tela oferece refazer o carrinho)
        return {"reply": "Entendi. Quer guardar isso e refazer o carrinho?",
                "proposed_rule": _proposal(extracted["rule"], None, plan), "plan": plan_view(plan)}
    if card is not None and extracted.get("is_question") and llm.enabled():
        # pergunta sobre um cartão ("tem outra marca mais barata?") pode precisar consultar o iFood
        out = await _agent_reply(s, api, plan, message, card)
        if out:
            return out
    if card is None or extracted.get("is_question"):
        answer = await llm.explain(message, _plan_brief(plan.result))
        return {"reply": answer, "proposed_rule": None, "plan": plan_view(plan)}

    act = extracted["action"]
    reply = extracted.get("reply") or ""
    if act["type"] in ("swap", "remove", "set_quantity", "accept") and card.get("item_id") is not None:
        sku_id = None
        if act["type"] == "swap":
            alts = card.get("alternatives", [])
            idx = act.get("candidate_index")
            if idx is None or not (0 <= idx < len(alts)):
                return {"reply": "Não entendi para qual produto trocar.", "proposed_rule": None, "plan": plan_view(plan)}
            sku_id = alts[idx]["sku_id"]
        try:
            await apply_action(s, api, plan, card["item_id"], act["type"], sku_id=sku_id, quantity=act.get("quantity"))
        except ActionError as exc:
            return {"reply": str(exc), "proposed_rule": None, "plan": plan_view(plan)}
        except McpToolError as exc:
            return {"reply": exc.user_message, "proposed_rule": None, "plan": plan_view(plan)}
        result = copy.deepcopy(plan.result)
        for c in result["decisions"]:
            if c["id"] == card_id:
                c["status"] = "resolved"
                c["resolution"] = act["type"]
        plan.result = result
        s.add(plan)
        s.commit()
        reply = reply or {"swap": "Troquei.", "remove": "Tirei do carrinho.", "set_quantity": "Ajustei a quantidade.",
                          "accept": "Combinado, mantive."}[act["type"]]

    proposal = _proposal(extracted["rule"], card, plan) if extracted.get("rule") else None
    if proposal and proposal["kind"] == "product" and act["type"] == "none":
        reply = "Entendi. Quer guardar isso e refazer o carrinho?"
    return {"reply": reply or "Anotado.", "proposed_rule": proposal, "plan": plan_view(plan)}


async def _agent_reply(s: Session, api: Ifood, plan: Plan, message: str, card: dict | None = None) -> dict | None:
    from app import agent

    try:
        out = await agent.run(s, api, plan, message, card)
    except llm.LLMError as exc:
        log.warning("agente do chat falhou, usando o fluxo sem tools: %s", exc)
        return None
    return {**out, "plan": plan_view(plan)}


def _proposal(rule: dict, card: dict | None, plan: Plan) -> dict | None:
    """Regra que o chat propõe; a tela confirma o escopo e pode refazer o carrinho com ela."""
    category = rule.get("category") or (card or {}).get("category")
    try:
        payload = validate_payload(rule["kind"], rule["payload"])
    except ValueError:
        return None
    return {"kind": rule["kind"], "category": category, "hardness": rule.get("hardness", "soft"), "payload": payload,
            "text": describe(rule["kind"], payload, category), "scope_options": ["global", str(plan.list_id)]}


def _plan_brief(result: dict) -> dict:
    """O pedaço do resultado que o `explain` precisa, sem a lista inteira de candidatos."""
    return {k: result.get(k) for k in ("address", "chosen", "alternatives", "single_total", "savings_vs_single",
                                        "merchants", "missing", "decisions")}


def plan_view(plan: Plan) -> dict:
    data = plan.model_dump(mode="json")
    result = dict(data.get("result") or {})
    result.pop("candidates", None)
    result.pop("discovery_errors", None)
    data["result"] = result
    return data


# ------------------------------------------------------------------ checkout

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
    user = s.get(User, plan.user_id)
    learning.adjust_quality_profile(s, user)
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
