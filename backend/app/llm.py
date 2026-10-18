"""Tarefas do LLM com entrada e saída em esquema (seção 5 do plano).

Cada tarefa valida a saída com Pydantic, tenta de novo uma vez mandando o erro e, se
ainda assim falhar (ou se não houver LLM configurado), cai na versão heurística. O
pipeline nunca segue com uma saída inválida.

Provedores: OpenRouter (`OPENROUTER_API_KEY`, saída estruturada via `response_format`)
ou a API da Anthropic (`ANTHROPIC_API_KEY`, SDK oficial). Os dois usam o Claude.

Teste rápido da chave: `uv run python -m app.llm "2 leites integral italac"`.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import re
import time
from typing import Any, Literal, TypeVar

import anthropic
import httpx
from pydantic import BaseModel, Field, ValidationError

from app import heuristics, metrics
from app.config import settings

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODELS = {
    "openrouter": ("anthropic/claude-haiku-5.5", "anthropic/claude-opus-5.5"),
    "anthropic": ("claude-haiku-5-5", "claude-opus-5-5"),
}

_anthropic: anthropic.AsyncAnthropic | None = None
_http: httpx.AsyncClient | None = None


class LLMError(RuntimeError):
    """Falha do provedor ou saída inválida; a tarefa cai na heurística."""


def provider() -> str | None:
    if not settings.llm_enabled:
        return None
    chosen = settings.llm_provider.strip().lower()
    if chosen in ("openrouter", "anthropic"):
        return chosen
    if settings.openrouter_api_key:
        return "openrouter"
    # GNOMERS_LLM=on usa o perfil do `ant auth login` mesmo sem chave no .env
    if settings.anthropic_api_key or os.environ.get("GNOMERS_LLM") == "on":
        return "anthropic"
    return None


def enabled() -> bool:
    return provider() is not None


def model_for(strong: bool) -> str:
    fast, strong_default = DEFAULT_MODELS[provider() or "anthropic"]
    if strong:
        return settings.llm_strong_model or strong_default
    return settings.llm_fast_model or fast


def strict_schema(schema: type[BaseModel]) -> dict:
    """JSON Schema no modo estrito: todo objeto fecha `additionalProperties` e lista todos os campos em `required`."""
    def fix(node: Any, is_properties: bool = False) -> Any:
        if isinstance(node, dict):
            if is_properties:  # nomes de campo: não confundir um campo "title" com metadado
                return {k: fix(v) for k, v in node.items()}
            node = {k: fix(v, k in ("properties", "$defs")) for k, v in node.items() if k not in ("default", "title")}
            if node.get("type") == "object" and "properties" in node:
                node["additionalProperties"] = False
                node["required"] = list(node["properties"])
            return node
        if isinstance(node, list):
            return [fix(v) for v in node]
        return node

    return fix(copy.deepcopy(schema.model_json_schema()))


async def _openrouter(schema: type[T], system: str, messages: list[dict], model: str, max_tokens: int) -> str:
    global _http
    if _http is None:
        _http = httpx.AsyncClient(timeout=httpx.Timeout(90.0, connect=10.0))
    body = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "system", "content": system}, *messages],
        "response_format": {"type": "json_schema", "json_schema": {
            "name": schema.__name__, "strict": True, "schema": strict_schema(schema)}},
        # só provedores que respeitam o response_format
        "provider": {"require_parameters": True},
        "usage": {"include": True},  # devolve tokens e custo em USD, para medir o custo por carrinho
    }
    headers = {"Authorization": f"Bearer {settings.openrouter_api_key}",
               "HTTP-Referer": "https://github.com/Petroncini/Gnomers", "X-Title": "Gnomers"}
    started = time.perf_counter()
    try:
        res = await _http.post(OPENROUTER_URL, json=body, headers=headers)
    except httpx.HTTPError as exc:
        raise LLMError(f"OpenRouter inacessível: {exc!r}") from exc
    if res.status_code != 200:
        raise LLMError(f"OpenRouter {res.status_code}: {res.text[:300]}")
    data = res.json()
    if data.get("error"):
        raise LLMError(f"OpenRouter: {data['error']}")
    usage = data.get("usage") or {}
    metrics.record_llm(model, (time.perf_counter() - started) * 1000, int(usage.get("prompt_tokens") or 0),
                       int(usage.get("completion_tokens") or 0), usage.get("cost"))
    choice = (data.get("choices") or [{}])[0]
    if choice.get("finish_reason") in ("content_filter", "refusal"):
        raise LLMError("LLM recusou a tarefa")
    content = (choice.get("message") or {}).get("content")
    if isinstance(content, list):  # alguns provedores devolvem blocos
        content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
    if not content:
        raise LLMError("OpenRouter devolveu resposta vazia")
    return content


async def _anthropic_call(schema: type[T], system: str, messages: list[dict], model: str, strong: bool,
                          max_tokens: int) -> str:
    global _anthropic
    if _anthropic is None:
        _anthropic = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key or None, max_retries=2)
    started = time.perf_counter()
    try:
        response = await _anthropic.messages.parse(
            model=model, max_tokens=max_tokens, system=system, messages=messages, output_format=schema,
            output_config={"effort": "medium" if strong else "low"},
        )
    except (anthropic.APIConnectionError, anthropic.RateLimitError, anthropic.APIStatusError) as exc:
        raise LLMError(f"Anthropic: {exc}") from exc
    metrics.record_llm(model, (time.perf_counter() - started) * 1000, response.usage.input_tokens,
                       response.usage.output_tokens, None)
    if response.stop_reason == "refusal":
        raise LLMError("LLM recusou a tarefa")
    return "".join(b.text for b in response.content if b.type == "text")


async def _run(schema: type[T], system: str, user: str, *, strong: bool = False, max_tokens: int = 4000) -> T:
    model = model_for(strong)
    messages: list[dict] = [{"role": "user", "content": user}]
    last_error: Exception | None = None
    for _ in range(2):
        if provider() == "openrouter":
            text = await _openrouter(schema, system, messages, model, max_tokens)
        else:
            text = await _anthropic_call(schema, system, messages, model, strong, max_tokens)
        try:
            return schema.model_validate_json(text)
        except ValidationError as exc:
            last_error = exc
            messages = messages + [
                {"role": "assistant", "content": text},
                {"role": "user", "content": f"A saída não passou na validação: {exc}. Responda de novo seguindo o esquema."},
            ]
    raise LLMError(f"LLM devolveu JSON inválido duas vezes: {last_error}")


async def _with_fallback(task: str, coro_factory, fallback):
    if not enabled():
        metrics.record_fallback(task)
        return fallback()
    token = metrics.current_task.set(task)
    try:
        return await coro_factory()
    except (LLMError, RuntimeError) as exc:
        log.warning("tarefa %s caiu na heurística: %s", task, exc)
        metrics.record_fallback(task)
        return fallback()
    finally:
        metrics.current_task.reset(token)


# ---------------------------------------------------------------- parse_item

class ParsedItem(BaseModel):
    query: str = Field(description="só termos descritivos do produto, sem 'barato', 'perto' ou quantidade")
    intention: Literal["product", "product_exact"]
    category: str = Field(description="nome genérico do produto no singular, minúsculo, sem acento, ex.: 'leite', 'papel higienico'")
    brand: str | None
    variety_terms: list[str]
    pack_size: float | None = Field(description="tamanho da embalagem em g, ml ou unidades")
    pack_unit: Literal["g", "ml", "un"] | None
    quantity: float = Field(description="quantas embalagens ou unidades o usuário quer")


PARSE_ITEM_SYSTEM = """Você interpreta itens de uma lista de compras de mercado para buscar no iFood.
Regras para a query: só termos descritivos (o produto e a variedade, como "integral", "caipira",
"extraforte"); nunca "barato", "perto", "rápido" nem quantidades. Se o usuário citar uma marca
específica, inclua a marca na query e use intention "product_exact"; senão "product".
pack_size é o tamanho de UMA embalagem convertido para g, ml ou un (1 kg = 1000 g, 1 L = 1000 ml).
quantity é o número de embalagens (ou unidades, quando não há embalagem, como "6 ovos" ou "3 bananas")."""


async def parse_item(raw_text: str) -> dict:
    async def call():
        out = await _run(ParsedItem, PARSE_ITEM_SYSTEM, f"Item: {raw_text}")
        return out.model_dump()

    return await _with_fallback("parse_item", call, lambda: heuristics.parse_item(raw_text))


# ---------------------------------------------------------- normalize_products

class NormalizedSku(BaseModel):
    index: int
    brand: str | None
    variety_terms: list[str]
    pack_size: float | None
    pack_unit: Literal["g", "ml", "un"] | None
    quality_tier: int = Field(ge=1, le=3, description="1 = marca própria ou econômica, 2 = comum, 3 = premium")
    matches: bool = Field(description="o SKU é o produto pedido (não um derivado, como 'leite de coco' para 'leite')")


class NormalizedBatch(BaseModel):
    skus: list[NormalizedSku]


NORMALIZE_SYSTEM = """Você normaliza nomes de produtos de catálogo de supermercado.
Para cada SKU, extraia a marca, os termos de variedade, o tamanho de uma embalagem em g, ml ou
unidades (multiplique packs: "6x200ml" = 1200 ml; "12 rolos" = 12 un), o nível de qualidade e se
o SKU realmente é o produto pedido. Um derivado ou outro produto que só cita a palavra
("biscoito sabor leite" para "leite") não casa. Responda um objeto por SKU, com o mesmo index."""


async def normalize_products(category: str, item_query: str, names: list[str]) -> list[dict]:
    def fallback():
        return [heuristics.normalize_product(category, n, item_query) for n in names]

    async def call():
        lines = "\n".join(f"{i}. {n}" for i, n in enumerate(names))
        out = await _run(NormalizedBatch, NORMALIZE_SYSTEM,
                         f"Produto pedido: {item_query} (categoria: {category})\nSKUs:\n{lines}",
                         max_tokens=8000)
        by_index = {s.index: s for s in out.skus}
        if set(by_index) != set(range(len(names))):
            raise RuntimeError("normalize_products não devolveu todos os SKUs")
        return [by_index[i].model_dump(exclude={"index"}) for i in range(len(names))]

    return await _with_fallback("normalize_products", call, fallback)


# --------------------------------------------------------------- plan_searches

class SearchPlan(BaseModel):
    specific: str = Field(description="a busca principal: o produto como aparece no rótulo, com a variedade pedida")
    broad: str = Field(description="só o nome do produto (e a marca, se pedida): a rede de segurança")
    alternatives: list[str] = Field(description="no máximo 1 outra forma de escrever a específica no rótulo; [] se não houver")
    must_have: list[str] = Field(description="características que o produto precisa ter (ex.: 'sem açúcar'); [] se nenhuma")


PLAN_SEARCHES_SYSTEM = """Você monta as buscas de um item de lista de compras no catálogo de mercados do iFood.
A busca do iFood é por palavras: exige TODAS as palavras da busca no nome do produto, tolera plural e
acento, e não conhece sinônimos ("granola sem açúcar" não acha "Granola Zero Açúcar").
Devolva:
- specific: 2 a 4 palavras como estariam no rótulo (produto + variedade/marca pedida). Tire quantidade,
  tamanho, e palavras que não vão em rótulo ("cru", "em grão", "pra filtro", "do bom").
- broad: só o produto (1 ou 2 palavras) e a marca, se o usuário pediu marca.
- alternatives: no máximo 1 busca completa alternativa (produto + variedade) para quando a variedade
  costuma aparecer escrita de outro jeito no rótulo ("granola zero açúcar" para "granola sem açúcar",
  "café torrado e moído" para "café moído"). [] se não houver.
- must_have: só características que aparecem escritas no rótulo e que o usuário pediu ("sem açúcar",
  "integral", "sem lactose"). Nunca tamanho, peso, quantidade ou modo de uso ("filtro", "pra bolo").
Não invente variedade que o usuário não pediu (para "feijão", não escolha "carioca").
Nunca coloque "barato", "perto", "rápido" ou preço nas buscas."""


def _heuristic_plan(text: str, category: str, brand: str | None) -> dict:
    """Plano B sem LLM: limpeza de palavras que não vão em rótulo e a tabela fixa de sinônimos."""
    parsed = heuristics.parse_item(text)
    specific = heuristics.clean_query(parsed["query"] or text)
    return {"specific": specific, "broad": heuristics.core_query(category, specific, brand),
            "alternatives": heuristics.query_variants(specific)[:1], "must_have": list(parsed["variety_terms"])}


async def plan_searches(text: str, category: str, brand: str | None = None) -> dict:
    """Buscas de um item no vocabulário de rótulo; sem LLM, a limpeza e os sinônimos fixos."""
    async def call():
        out = await _run(SearchPlan, PLAN_SEARCHES_SYSTEM,
                         f"Item: {text}\nProduto: {category}\nMarca pedida: {brand or 'nenhuma'}", max_tokens=600)
        plan = out.model_dump()
        # o modelo não manda no formato: no máximo 3 buscas, todas com o produto, e nada de tamanho nas exigências
        head = heuristics.norm(plan["broad"]).split()[:1]
        plan["alternatives"] = [a for a in plan["alternatives"]
                                if a.strip() and (not head or head[0] in heuristics.norm(a))][:1]
        plan["must_have"] = [t for t in plan["must_have"] if not re.search(r"\d", t)][:3]
        for key in ("specific", "broad"):
            if not plan[key].strip():
                raise LLMError(f"plan_searches devolveu {key} vazio")
        return plan

    return await _with_fallback("plan_searches", call, lambda: _heuristic_plan(text, category, brand))


# ---------------------------------------------------------------- check_variety

class VarietyCheck(BaseModel):
    index: int
    missing: list[str] = Field(description="das características pedidas, as que o produto NÃO tem")


class VarietyBatch(BaseModel):
    products: list[VarietyCheck]


async def check_variety(terms: list[str], names: list[str]) -> list[list[str]]:
    """Para cada nome, as características pedidas que faltam, com bom senso ("Zero Açúcar" atende "sem açúcar")."""
    def fallback():
        return [[t for t in terms if not heuristics.has_term(n, t)] for n in names]

    async def call():
        lines = "\n".join(f"{i}. {n}" for i, n in enumerate(names))
        out = await _run(VarietyBatch,
                         "Você confere se produtos de supermercado têm as características pedidas pelo cliente. "
                         "Considere sinônimos e formas de rótulo ('Zero Açúcar' atende 'sem açúcar'; 'Torrado e "
                         "Moído' atende 'moído'). Na dúvida, considere que falta. Responda um objeto por produto.",
                         f"Características pedidas: {terms}\nProdutos:\n{lines}", max_tokens=2000)
        by_index = {c.index: c.missing for c in out.products}
        if set(by_index) != set(range(len(names))):
            raise LLMError("check_variety não respondeu todos os produtos")
        return [[t for t in by_index[i] if t in terms] for i in range(len(names))]

    return await _with_fallback("check_variety", call, fallback)


# ----------------------------------------------------------------- score_notes

class NoteScores(BaseModel):
    scores: list[float]


async def score_notes(notes: list[str], names: list[str]) -> list[float]:
    async def call():
        lines = "\n".join(f"{i}. {n}" for i, n in enumerate(names))
        out = await _run(NoteScores,
                         "Dê uma nota de 0 a 1 para cada SKU indicando o quanto ele atende às notas do usuário. "
                         "Devolva uma lista com uma nota por SKU, na ordem.",
                         f"Notas: {notes}\nSKUs:\n{lines}", max_tokens=1000)
        if len(out.scores) != len(names):
            raise RuntimeError("score_notes devolveu tamanho errado")
        return [min(1.0, max(0.0, x)) for x in out.scores]

    return await _with_fallback("score_notes", call, lambda: heuristics.score_notes(notes, names))


# ---------------------------------------------------------------- extract_rule

class CartAction(BaseModel):
    type: Literal["none", "swap", "remove", "set_quantity", "accept"]
    candidate_index: int | None = Field(None, description="índice da alternativa escolhida, para swap")
    quantity: int | None = None


class ProposedRule(BaseModel):
    kind: Literal["dietary", "variety", "brand_chain", "brand_block", "merchant", "delivery", "quantity", "product",
                  "note"]
    category: str | None
    hardness: Literal["hard", "soft"]
    payload_json: str = Field(description="payload da regra como JSON, no formato do tipo")


class ExtractedRule(BaseModel):
    action: CartAction
    rule: ProposedRule | None
    reply: str = Field(description="uma frase curta ao usuário, em português, sem jargão técnico")
    is_question: bool = Field(description="true se a mensagem é uma pergunta aberta e não um pedido de mudança")


EXTRACT_SYSTEM = """Você ajuda num app de compras de mercado. O usuário responde a um cartão de
decisão sobre um item do carrinho (ou fala do carrinho em geral). Transforme a mensagem em (1) uma
ação no carrinho e (2) uma regra de preferência para as próximas compras, quando a mensagem
expressar uma preferência durável.
Se o usuário disser que o produto escolhido não é o que ele quer e descrever o que quer de verdade
("na verdade quero café em pó pra filtro"), proponha a regra product com a descrição do produto
como ela aparece no rótulo de mercado, em 2 a 4 palavras ("café torrado e moído", "feijão carioca"),
sem quantidade, tamanho ou palavras que não vão no nome do produto ("cru", "em grão", "pra filtro"),
e com a categoria do item. Use a ação none.
Tipos de regra e payload:
- dietary {"tags": [LAC_FREE|SUGAR_FREE|VEGAN|VEGETARIAN|ORGANIC|GLUTEN_FREE]}
- variety {"terms": ["integral"]}
- brand_chain {"order": ["Ypê", "Limpol"], "allow_others": bool}
- brand_block {"brands": ["X"]}
- merchant {"prefer": [merchantId], "block": [merchantId]}
- delivery {"max_fee": número, "max_minutes": inteiro}
- product {"description": "café em pó para filtro"} quando o usuário diz que tipo de produto quer para a categoria
- quantity {"exact": true, "max_overbuy": 1.1} quando o usuário quer a quantidade pedida e não uma embalagem maior
  ("peso exato", "não quero 1 kg se pedi 500 g"); max_overbuy é o quanto pode passar (1.1 = 10%)
- note {"text": "..."} para o que não encaixa em nenhum tipo
Se a mensagem for só uma pergunta ("por que esse mercado?"), marque is_question e não proponha regra."""


async def extract_rule(message: str, card: dict) -> dict:
    def fallback():
        return _extract_rule_heuristic(message, card)

    async def call():
        from app.rules import validate_payload

        out = await _run(ExtractedRule, EXTRACT_SYSTEM,
                         f"Cartão: {json.dumps(card, ensure_ascii=False)}\nMensagem: {message}", strong=True)
        data = out.model_dump()
        rule = data.pop("rule")
        data["rule"] = None
        if rule:
            try:
                payload = validate_payload(rule["kind"], json.loads(rule["payload_json"]))
                data["rule"] = {"kind": rule["kind"], "category": rule["category"],
                                "hardness": rule["hardness"], "payload": payload}
            except (ValueError, ValidationError) as exc:
                log.warning("regra proposta pelo LLM inválida: %s", exc)
        return data

    return await _with_fallback("extract_rule", call, fallback)


_REFINE = re.compile(r"\b(?:na verdade|eu quero|queria|quero|preciso de|era pra ser|era para ser)\s+(?:um |uma |o |a )?", re.I)


def _refine_target(text: str, card: dict) -> tuple[int | None, str | None]:
    """'na verdade eu quero café em pó pra filtro' -> (item, 'café em pó pra filtro')."""
    from app.rules import norm

    marks = list(_REFINE.finditer(text))
    if not marks:
        return None, None
    # depois da última expressão: "na verdade eu quero café em pó" -> "café em pó"
    new_text = text[marks[-1].end():].strip(" .!")
    if len(new_text.split()) < 2:
        return None, None
    # só é correção do item se o texto novo ainda fala do produto ("café torrado e moído" para café);
    # "quero o peso exato" ou "quero Pilão" são preferências e seguem para as regras
    words = set(norm(new_text).split())
    words |= {heuristics.singular(w) for w in words}

    def mentions(category: str | None) -> bool:
        cat = norm(category)
        return bool(cat) and set(cat.split()) <= words

    if card.get("item_id") is not None:
        return (card["item_id"], new_text) if mentions(card.get("category")) else (None, None)
    for item in card.get("items", []):
        if mentions(item.get("category")):
            return item["id"], new_text
    return None, None


def _extract_rule_heuristic(message: str, card: dict) -> dict:
    from app.rules import norm

    text = norm(message)
    category = card.get("category")
    action: dict = {"type": "none", "candidate_index": None, "quantity": None}
    rule = None
    item_id, new_text = _refine_target(message, card)
    if new_text and not text.endswith("?"):
        item_category = category or next((i["category"] for i in card.get("items", []) if i["id"] == item_id), None)
        return {"action": action, "rule": {"kind": "product", "category": item_category, "hardness": "soft",
                                           "payload": {"description": new_text}},
                "reply": "", "is_question": False}
    if text.endswith("?") or text.startswith(("por que", "porque", "pq")):
        return {"action": action, "rule": None, "reply": "", "is_question": True}
    alternatives = card.get("alternatives", [])
    for i, alt in enumerate(alternatives):
        brand = norm(alt.get("brand"))
        if brand and brand in text:
            action = {"type": "swap", "candidate_index": i, "quantity": None}
            order = [alt["brand"]]
            if card.get("chosen", {}).get("brand") and ("depois" in text or "senao" in text):
                order.append(card["chosen"]["brand"])
            rule = {"kind": "brand_chain", "category": category, "hardness": "soft",
                    "payload": {"order": order, "allow_others": "so " not in text}}
            break
    if action["type"] == "none":
        if any(w in text for w in ("remove", "tira", "nao quero", "dispenso")):
            action["type"] = "remove"
        elif any(w in text for w in ("ok", "pode", "beleza", "aceito", "tanto faz", "serve")):
            action["type"] = "accept"
        brand = card.get("chosen", {}).get("brand")
        if any(w in text for w in ("peso exato", "quantidade exata", "tamanho exato", "embalagem menor", "nao quero maior",
                                   "so o que pedi")):
            rule = {"kind": "quantity", "category": category, "hardness": "hard",
                    "payload": {"exact": True, "max_overbuy": 1.1}}
        elif brand and any(w in text for w in ("nunca", "odeio", "nao gosto")) and norm(brand) in text:
            rule = {"kind": "brand_block", "category": category, "hardness": "hard", "payload": {"brands": [brand]}}
        elif action["type"] == "none":
            rule = {"kind": "note", "category": category, "hardness": "soft", "payload": {"text": message.strip()}}
    return {"action": action, "rule": rule, "reply": "", "is_question": False}


# ---------------------------------------------------------------------- explain

class Explanation(BaseModel):
    answer: str


async def explain(question: str, plan_result: dict) -> str:
    def fallback():
        return _explain_heuristic(plan_result)

    async def call():
        out = await _run(Explanation,
                         "Responda ao cliente de um app de compras em no máximo 2 frases curtas (até 40 palavras), "
                         "em português, usando só os dados do carrinho. Não fale em 'otimizador', 'plano' ou "
                         "'solver'; não refaça contas nem invente valores.",
                         f"Plano: {json.dumps(plan_result, ensure_ascii=False)[:60000]}\nPergunta: {question}",
                         strong=True)
        return out.answer

    return await _with_fallback("explain", call, fallback)


def _explain_heuristic(result: dict) -> str:
    chosen = result.get("chosen") or {}
    carts = chosen.get("carts", [])
    if not carts:
        return "Ainda não há um carrinho montado para esta lista."
    names = " e ".join(c["merchant_name"] for c in carts)
    total = chosen.get("total", 0)
    text = f"Escolhi {names} porque deu o menor total real (R$ {total:.2f}), já com frete e cupom."
    alternatives = result.get("alternatives", [])
    if alternatives:
        alt = alternatives[0]
        text += f" A segunda melhor opção ficava em R$ {alt['total']:.2f}."
    return text


if __name__ == "__main__":
    import asyncio
    import sys

    text = " ".join(sys.argv[1:]) or "2 leites integral italac"
    print(f"provedor: {provider() or 'nenhum (heurística)'} | modelo rápido: {model_for(False) if enabled() else '-'}")

    async def _main() -> None:
        # chama direto, sem cair na heurística, para mostrar o erro real se a chave falhar
        if enabled():
            out = await _run(ParsedItem, PARSE_ITEM_SYSTEM, f"Item: {text}")
            print("LLM:", out.model_dump())
        print("heurística:", heuristics.parse_item(text))

    asyncio.run(_main())
