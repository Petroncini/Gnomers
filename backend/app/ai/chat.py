"""Chat do carrinho: responde perguntas, aplica trocas ditas em texto e propõe regras (seção 7.6).

Mensagem livre vai para o agente com tools (`agent.py`); sem LLM, ou se ele falhar, o chat segue
o fluxo sem tools deste módulo.
"""

from __future__ import annotations

import copy
import logging

from sqlmodel import Session

from app.ai import llm
from app.cart.changes import ActionError, apply_action, plan_view
from app.core.models import Plan
from app.mcp.client import McpToolError
from app.mcp.ifood import Ifood
from app.preferences.store import rule_proposal

log = logging.getLogger(__name__)


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
                "proposed_rule": rule_proposal(extracted["rule"], None, plan), "plan": plan_view(plan)}
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

    proposal = rule_proposal(extracted["rule"], card, plan) if extracted.get("rule") else None
    if proposal and proposal["kind"] == "product" and act["type"] == "none":
        reply = "Entendi. Quer guardar isso e refazer o carrinho?"
    return {"reply": reply or "Anotado.", "proposed_rule": proposal, "plan": plan_view(plan)}


async def _agent_reply(s: Session, api: Ifood, plan: Plan, message: str, card: dict | None = None) -> dict | None:
    from app.ai import agent

    try:
        out = await agent.run(s, api, plan, message, card)
    except llm.LLMError as exc:
        log.warning("agente do chat falhou, usando o fluxo sem tools: %s", exc)
        return None
    return {**out, "plan": plan_view(plan)}


def _plan_brief(result: dict) -> dict:
    """O pedaço do resultado que o `explain` precisa, sem a lista inteira de candidatos."""
    return {k: result.get(k) for k in ("address", "chosen", "alternatives", "single_total", "savings_vs_single",
                                        "merchants", "missing", "decisions")}
