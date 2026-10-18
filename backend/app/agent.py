"""Chat com tools: o LLM consulta o iFood e mexe no carrinho já montado, num loop curto.

É o único lugar do app com loop agêntico. O pipeline que monta o carrinho continua
determinístico; aqui o usuário faz pedidos abertos sobre o carrinho pronto ("troca o café
por um mais forte de até R$ 25", "tem leite sem lactose no Extra?").

Limites: as tools do iFood passam pelos wrappers de `app/mcp/ifood.py` (nada de ID inventado
nem qualificador na busca), no máximo MAX_IFOOD_CALLS chamadas ao iFood por mensagem, só
trocas dentro dos mercados do carrinho e nada de fechar pedido: pagar continua sendo um botão.
"""

from __future__ import annotations

import copy
import json
import logging

from sqlmodel import Session

from app import actions, heuristics, llm
from app.mcp.client import McpToolError
from app.mcp.ifood import Ifood
from app.models import Address, Plan

log = logging.getLogger(__name__)

MAX_IFOOD_CALLS = 6
MAX_TURNS = 8
MAX_TURNS_HISTORY = 10   # falas anteriores que vão junto com a mensagem nova
MAX_SEEN = 40            # produtos vistos guardados no plano
MAX_SEEN_SHOWN = 15      # e mostrados ao modelo

SYSTEM = """Você é o assistente do Cesta, um app que monta carrinhos de mercado no iFood. O carrinho
do cliente já está montado; você responde perguntas sobre ele e faz ajustes pedidos.

Como trabalhar:
- Comece com verCarrinho quando precisar saber o que está no carrinho.
- Para achar produtos, use searchOffers. A query tem só o produto e a variedade ("café extraforte",
  "leite sem lactose"); nunca "barato", "perto", "rápido" nem preço. Para promoções use
  onlyWithPromotion. Para olhar um mercado do carrinho, passe o merchantId dele.
- A busca só usa sinônimos quando nada casa literalmente. Se vier pouco ou nada do que o cliente
  quer, tente a outra forma de rótulo antes de concluir que não tem ("zero lactose" para "sem
  lactose", "zero açúcar" para "sem açúcar", "torrado e moído" para "moído").
- Use só IDs que vieram de uma tool nesta conversa. Nunca invente IDs.
- trocarItem só funciona com produtos de mercados que já estão no carrinho. Se o produto bom estiver
  em outro mercado, diga isso e não troque.
- Troque ou remova só quando o cliente pediu para mexer. Se ele só perguntou, responda e ofereça.
- Se o cliente disser uma preferência que vale para as próximas compras ("prefiro Pilão", "nunca
  Qualitá", "quero peso exato"), chame proporPreferencia; o app pede a confirmação dele.
- Você tem no máximo 6 chamadas ao iFood (searchOffers e getItem) por mensagem.
- Você não fecha o pedido nem paga.

A conversa anterior e os produtos já vistos vêm antes da mensagem do cliente. Respostas curtas
("sim", "pode", "o primeiro") se referem à sua última pergunta: faça o que você ofereceu. Para trocar
por um produto já visto, use o itemId dele direto, sem buscar de novo.

Responda em português, em até 3 frases curtas, com valores em R$. Escreva nomes de produtos e marcas
entre ** (negrito), como **Leite Integral Italac 1l**. Não use outra formatação. Diga o que fez e o novo total
quando mexer no carrinho. Não fale em "plano", "otimizador" ou IDs."""

TOOLS = [
    {"name": "verCarrinho",
     "description": "Mostra o carrinho atual: mercados, itens da lista com o produto escolhido, preços, "
                    "totais, itens em falta e o total que daria comprando tudo num mercado só.",
     "parameters": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "searchOffers",
     "description": "Tool do MCP do iFood: busca produtos de mercado perto do cliente. Casa as palavras "
                    "literalmente e só usa sinônimos quando nada casa; prefira queries curtas.",
     "parameters": {"type": "object", "properties": {
         "query": {"type": "string", "description": "produto e variedade, sem qualificadores"},
         "merchantId": {"type": "string", "description": "opcional: restringe a um mercado (ID vindo de verCarrinho)"},
         "onlyWithPromotion": {"type": "boolean", "description": "opcional: só produtos em promoção"},
     }, "required": ["query"], "additionalProperties": False}},
    {"name": "getItem",
     "description": "Tool do MCP do iFood: detalhes de um produto (descrição, preço, opções).",
     "parameters": {"type": "object", "properties": {
         "merchantId": {"type": "string"}, "itemId": {"type": "string"},
     }, "required": ["merchantId", "itemId"], "additionalProperties": False}},
    {"name": "trocarItem",
     "description": "Troca o produto de um item da lista por outro, no carrinho real do iFood. O produto "
                    "novo precisa ter vindo de searchOffers e ser de um mercado do carrinho.",
     "parameters": {"type": "object", "properties": {
         "itemDaLista": {"type": "integer", "description": "id do item da lista (de verCarrinho)"},
         "itemId": {"type": "string", "description": "itemId do produto novo (de searchOffers)"},
         "quantidade": {"type": "integer", "description": "quantas embalagens do produto novo"},
     }, "required": ["itemDaLista", "itemId", "quantidade"], "additionalProperties": False}},
    {"name": "ajustarQuantidade",
     "description": "Muda quantas embalagens de um item vão no carrinho.",
     "parameters": {"type": "object", "properties": {
         "itemDaLista": {"type": "integer"}, "quantidade": {"type": "integer", "minimum": 1},
     }, "required": ["itemDaLista", "quantidade"], "additionalProperties": False}},
    {"name": "removerItem",
     "description": "Tira um item da lista do carrinho.",
     "parameters": {"type": "object", "properties": {"itemDaLista": {"type": "integer"}},
                    "required": ["itemDaLista"], "additionalProperties": False}},
    {"name": "proporPreferencia",
     "description": "Propõe guardar uma preferência para as próximas compras; o cliente confirma na tela. "
                    "Tipos e payload: brand_chain {\"order\": [\"Ypê\", \"Limpol\"], \"allow_others\": true}; "
                    "brand_block {\"brands\": [\"X\"]}; variety {\"terms\": [\"integral\"]}; "
                    "product {\"description\": \"café torrado e moído\"}; "
                    "quantity {\"exact\": true, \"max_overbuy\": 1.1}; note {\"text\": \"banana mais verde\"}.",
     "parameters": {"type": "object", "properties": {
         "kind": {"type": "string", "enum": ["brand_chain", "brand_block", "variety", "product", "quantity", "note"]},
         "category": {"type": "string", "description": "categoria do item, como em verCarrinho (ex.: 'cafe')"},
         "payload_json": {"type": "string", "description": "o payload do tipo, em JSON"},
     }, "required": ["kind", "category", "payload_json"], "additionalProperties": False}},
]


class _Session:
    """Estado de uma mensagem: o que o agente já viu, o que fez e quanto do iFood já gastou."""

    def __init__(self, s: Session, api: Ifood, plan: Plan):
        self.s, self.api, self.plan = s, api, plan
        self.ifood_calls = 0
        # itemId -> oferta vista em searchOffers, nesta mensagem ou nas anteriores do mesmo carrinho
        self.seen: dict[str, dict] = dict((plan.result or {}).get("chat_seen") or {})
        self.steps: list[str] = []
        self.proposal: dict | None = None
        self.lat, self.lng = plan.lat, plan.lng
        if self.lat is None and plan.address_id:
            # endereço escolhido na tela, sem GPS: usa as coordenadas do endereço salvo
            addr = s.get(Address, plan.address_id)
            if addr:
                self.lat, self.lng = addr.lat, addr.lng

    # ------------------------------------------------------------------ helpers

    def _result(self) -> dict:
        return self.plan.result or {}

    def _merchant_name(self, merchant_id: str) -> str:
        m = self._result().get("merchants", {}).get(merchant_id)
        if m:
            return m["name"]
        return next((o["merchant"] for o in self.seen.values() if o["merchantId"] == merchant_id), "outro mercado")

    def _item(self, item_id: int) -> dict | None:
        return next((i for i in self._result().get("items", []) if i["id"] == item_id), None)

    def _budget(self) -> str | None:
        if self.ifood_calls >= MAX_IFOOD_CALLS:
            return "Erro: limite de chamadas ao iFood desta mensagem atingido. Responda com o que já sabe."
        self.ifood_calls += 1
        return None

    # -------------------------------------------------------------------- tools

    def ver_carrinho(self) -> dict:
        r = self._result()
        chosen = r.get("chosen") or {}
        items = {i["id"]: i for i in r.get("items", [])}
        carts = [{
            "mercado": c["merchant_name"], "merchantId": c["merchant_id"], "total": c["summary"]["total"],
            "frete": c["summary"].get("delivery"), "pedido_minimo": c.get("minimum_order"),
            "itens": [{"itemDaLista": ln["item_id"], "pedido": items.get(ln["item_id"], {}).get("raw_text"),
                       "categoria": items.get(ln["item_id"], {}).get("category"), "produto": ln["name"],
                       "quantidade": ln["quantity"], "preco_unitario": ln["unit_price"]} for ln in c["lines"]],
        } for c in chosen.get("carts", [])]
        in_cart = {ln["itemDaLista"] for c in carts for ln in c["itens"]}
        return {
            "carrinhos": carts, "total": chosen.get("total"),
            "fora_do_carrinho": [{"itemDaLista": i["id"], "pedido": i["raw_text"]}
                                 for i in r.get("items", []) if i["id"] not in in_cart],
            "total_num_mercado_so": r.get("single_total"),
            "outras_opcoes": [{"mercados": [self._merchant_name(m) for m in a.get("merchants", [])], "total": a.get("total")}
                              for a in r.get("alternatives", [])],
        }

    async def search(self, query: str, merchant_id: str | None, only_promo: bool) -> dict | str:
        if (err := self._budget()):
            return err
        refinements: dict = {"onlyOpen": not self._result().get("deliver_at")} if not merchant_id else {}
        if merchant_id:
            refinements["merchantId"] = merchant_id
        if only_promo:
            refinements["onlyWithPromotion"] = True
        body = await self.api.search_items(query, self.lat, self.lng, refinements=refinements,
                                           page_size=15 if merchant_id else 25)
        in_cart = {c["merchant_id"] for c in (self._result().get("chosen") or {}).get("carts", [])}
        out = []
        for r in body.get("results", []):
            m = r.get("merchant") or {}
            price = r.get("promotionalUnitPrice") or r.get("unitPrice")
            if not r.get("itemId") or not m.get("merchantId") or price is None:
                continue
            offer = {"itemId": r["itemId"], "nome": r.get("itemName", ""), "preco": float(price),
                     "preco_sem_promocao": float(r.get("unitPrice") or price), "merchantId": m["merchantId"],
                     "merchant": m.get("merchantName") or m.get("name", ""), "mercado_no_carrinho": m["merchantId"] in in_cart,
                     "image": r.get("itemImageUrl")}
            self.seen[r["itemId"]] = offer
            out.append({k: v for k, v in offer.items() if k != "image"})
        where = f" no {self._merchant_name(merchant_id)}" if merchant_id else ""
        self.steps.append(f"Busquei “{query}”{where}" + (" em promoção" if only_promo else ""))
        return {"resultados": out[:20]} if out else "Nenhum resultado. Tente uma query mais curta."

    async def get_item(self, merchant_id: str, item_id: str) -> dict | str:
        if (err := self._budget()):
            return err
        body = await self.api.get_item(merchant_id, item_id, self.lat, self.lng)
        item = body.get("item") or body
        return {k: item.get(k) for k in ("itemName", "description", "unitPrice", "promotionalUnitPrice", "itemEan")
                if item.get(k) is not None}

    async def swap(self, list_item: int, sku_id: str, quantity: int) -> dict | str:
        offer = self.seen.get(sku_id)
        if offer is None:
            return "Erro: esse itemId não veio de um searchOffers nesta conversa."
        if self._item(list_item) is None:
            return "Erro: item da lista desconhecido; veja os ids em verCarrinho."
        if not offer["mercado_no_carrinho"]:
            return (f"Erro: {offer['merchant']} não está no carrinho. Trocar de mercado exige refazer o "
                    "carrinho; explique isso ao cliente.")
        # o produto novo entra como candidato do item, e a troca segue o mesmo caminho do cartão.
        # O tamanho vem do nome, como no pipeline: o recálculo de "dá para pagar menos" depende dele.
        item = self._item(list_item)
        parsed = heuristics.normalize_product(item["category"], offer["nome"], item.get("query", ""))
        if item["unit"] == "pack":
            size = 1.0
        elif parsed["pack_size"] and parsed["pack_unit"] == item["unit"]:
            size = float(parsed["pack_size"])
        else:
            size = item["required"] / quantity  # sem tamanho no nome: a quantidade escolhida cobre o pedido
        result = copy.deepcopy(self.plan.result)
        cands = result.setdefault("candidates", {}).setdefault(str(list_item), [])
        if not any(c["sku_id"] == sku_id for c in cands):
            cands.append({"sku_id": sku_id, "item_id": list_item, "merchant_id": offer["merchantId"],
                          "name": offer["nome"], "brand": parsed["brand"], "price": offer["preco"], "size": size,
                          "image": offer.get("image"), "reasons": ["escolhido no chat"]})
        self.plan.result = result
        old = next((ln["name"] for c in result["chosen"]["carts"] for ln in c["lines"] if ln["item_id"] == list_item), None)
        await actions.apply_action(self.s, self.api, self.plan, list_item, "swap", sku_id=sku_id, quantity=quantity)
        self.steps.append(f"Troquei {old or 'o item'} por {offer['nome']}" if old else f"Coloquei {offer['nome']}")
        return {"ok": True, "novo_total": self.plan.result["chosen"]["total"]}

    async def set_quantity(self, list_item: int, quantity: int) -> dict:
        await actions.apply_action(self.s, self.api, self.plan, list_item, "set_quantity", quantity=quantity)
        name = (self._item(list_item) or {}).get("raw_text", "o item")
        self.steps.append(f"Mudei {name} para {quantity}")
        return {"ok": True, "novo_total": self.plan.result["chosen"]["total"]}

    async def remove(self, list_item: int) -> dict:
        await actions.apply_action(self.s, self.api, self.plan, list_item, "remove")
        self.steps.append(f"Tirei {(self._item(list_item) or {}).get('raw_text', 'o item')} do carrinho")
        return {"ok": True, "novo_total": (self.plan.result.get("chosen") or {}).get("total")}

    def propose(self, kind: str, category: str, payload_json: str) -> dict | str:
        try:
            payload = json.loads(payload_json)
        except json.JSONDecodeError:
            return "Erro: payload_json não é JSON válido."
        proposal = actions._proposal({"kind": kind, "category": category, "payload": payload,
                                      "hardness": "hard" if kind in ("brand_block", "quantity") else "soft"},
                                     None, self.plan)
        if proposal is None:
            return "Erro: payload inválido para esse tipo."
        self.proposal = proposal
        return {"ok": True, "o_cliente_vai_confirmar": proposal["text"]}

    async def execute(self, name: str, args: dict) -> str:
        try:
            if name == "verCarrinho":
                out = self.ver_carrinho()
            elif name == "searchOffers":
                out = await self.search(args["query"], args.get("merchantId"), bool(args.get("onlyWithPromotion")))
            elif name == "getItem":
                out = await self.get_item(args["merchantId"], args["itemId"])
            elif name == "trocarItem":
                out = await self.swap(int(args["itemDaLista"]), args["itemId"], max(1, int(args["quantidade"])))
            elif name == "ajustarQuantidade":
                out = await self.set_quantity(int(args["itemDaLista"]), int(args["quantidade"]))
            elif name == "removerItem":
                out = await self.remove(int(args["itemDaLista"]))
            elif name == "proporPreferencia":
                out = self.propose(args["kind"], args["category"], args["payload_json"])
            else:
                out = f"Erro: tool desconhecida {name}."
        except (KeyError, TypeError, ValueError) as exc:
            # inclui o que os wrappers recusam (ID que não é UUID, qualificador na query)
            out = f"Erro nos argumentos: {exc}"
        except actions.ActionError as exc:
            out = f"Erro: {exc}"
        except McpToolError as exc:
            out = f"Erro do iFood: {exc.user_message}"
        log.info("agente %s %s -> %s", name, args, str(out)[:200])
        return out if isinstance(out, str) else json.dumps(out, ensure_ascii=False)


async def run(s: Session, api: Ifood, plan: Plan, message: str, card: dict | None = None) -> dict:
    """Responde uma mensagem do chat com tools. Levanta llm.LLMError se não houver LLM ou ele falhar."""
    sess = _Session(s, api, plan)
    context = _history(plan)
    if card:
        context += (f"O cliente está olhando este aviso sobre o item {card.get('item_id')}: "
                   f"{card.get('title')}. {card.get('text')}\n")
    reply = await llm.tool_loop(SYSTEM, f"{context}Cliente: {message}", TOOLS, sess.execute, max_turns=MAX_TURNS,
                                 strong=False)
    # os produtos vistos ficam no plano: a próxima mensagem ("sim") pode trocar por eles sem nova busca
    result = dict(plan.result or {})
    result["chat_seen"] = dict(list(sess.seen.items())[-MAX_SEEN:])
    plan.result = result
    s.add(plan)
    s.commit()
    return {"reply": reply.strip(), "proposed_rule": sess.proposal, "steps": sess.steps}


def _history(plan: Plan) -> str:
    """A conversa até aqui e os produtos já vistos, para o modelo entender respostas como "sim"."""
    result = plan.result or {}
    turns = (result.get("chat") or [])[-MAX_TURNS_HISTORY:]
    seen = list((result.get("chat_seen") or {}).values())[-MAX_SEEN_SHOWN:]
    parts = []
    if turns:
        lines = [f"{'Cliente' if t['role'] == 'user' else 'Você'}: {t['text']}" for t in turns]
        parts.append("Conversa até agora:\n" + "\n".join(lines))
    if seen:
        lines = [f"- {o['nome']} | itemId {o['itemId']} | {o['merchant']} | R$ {o['preco']:.2f}"
                 f"{' | mercado do carrinho' if o.get('mercado_no_carrinho') else ''}" for o in seen]
        parts.append("Produtos já vistos nesta conversa:\n" + "\n".join(lines))
    return "\n\n".join(parts) + "\n\n" if parts else ""
