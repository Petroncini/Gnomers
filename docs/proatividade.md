# Módulo de proatividade

Contexto para quem (pessoa ou agente) for mexer na proatividade. Código em `backend/app/proactive.py`, testes em `backend/tests/test_proactive.py`. Complementa a seção 10 do `plano-implementacao.md`.

## O que faz

Quando uma promoção é publicada, o back-end reavalia todas as listas do usuário e decide, item a item, se vale sugerir a compra agora. A decisão depende do desconto e do **padrão de frequência de compra** do item.

```
POST /admin/promotions ──► simulate_promotion() ──► evento PromotionsPublished
                                                     │
                            publish() ──► _on_promotions() ──► reevaluate()
                                                     │
              para cada item de cada lista que casa com alguma oferta:
                 pattern() ─► melhor oferta permitida ─► decide() ─► Suggestion "promo"
              para cada lista com 2+ promoções ativas: Suggestion "list_promo"
```

O job diário (`scheduler.run_daily`, às 8h e no `POST /admin/simulate-days`) usa a mesma avaliação (`evaluate_item` e `aggregate_list`), só que com as ofertas reais do `searchOffers` com `onlyWithPromotion`.

## Eventos

- O barramento é em processo, sem fila: `subscribe(TipoDoEvento, handler)` e `await publish(session, evento)`. O `publish` espera os handlers para a rota devolver o resultado da reavaliação.
- Hoje o único evento é `PromotionsPublished(offers, source)`. Cada oferta é um dict no formato de `schemas.PromoOffer`: `sku_id`, `name`, `merchant_id`, `merchant`, `unit_price` (cheio), `price` (promocional) e `simulated`.
- O MCP do iFood não avisa quando entra promoção, então o evento é **mockado**. No modo `fake`, `FakeIfood.add_promotion` muda o catálogo e o carrinho criado depois tem o preço promocional de verdade. Nos modos `live` e `replay`, a busca real fornece os itens e só o preço promocional é simulado (`simulated: true`), e o carrinho vem com o preço real.
- Para ligar um gatilho real no futuro (webhook, polling do `getRecommendations(BEST_DEALS)`, promoções vistas no `discovery` do pipeline), basta montar as ofertas e chamar `publish(...)`.

## Padrão de compra (`pattern`)

O padrão sai da fonte mais forte que houver:

| `source` | quando | intervalo | regularidade |
|---|---|---|---|
| `history` | 2+ compras do item (`OrderLine` + `Order.created_at`) | mediana dos intervalos | 0,5 com um intervalo só; com mais, `1 - desvio/média` |
| `item_recurrence` | `ListItem.recurrence_days` | o declarado | 0,7 |
| `list_recurrence` | `ShoppingList.recurrence_days` | o da lista | 0,7 |
| `list_due` | só `next_due` na lista | nenhum | 0,5 |
| `none` | nada | nenhum | sem sugestão |

- **Próxima compra:** última compra + intervalo. Sem compra, vale o `next_due` do item ou da lista.
- **`progress`:** `1 - dias_até_vencer / intervalo`. 0 significa que acabou de comprar, 1 que vence hoje e acima de 1 que está atrasada.
- **Volume necessário:** zero ou uma compra já basta quando a lista tem recorrência. Duas compras já dão um padrão próprio do item.

## Decisão (`decide`)

| Ação | Condição | Texto |
|---|---|---|
| `advance` | `progress >= 0,5` ou vence em até 7 dias, e desconto `>= 15%` | "… Adiantar?" |
| `stock` | fora disso, desconto `>= 30%` e regularidade `>= 0,5` | "… Estocar agora?" |
| nenhuma | os demais casos; o motivo vai em `evaluations[].reason` | |

Os limiares são constantes no topo de `proactive.py`.

## Escolha da oferta (`evaluate_item`)

- **Casamento (`matches`):** a categoria é a mesma do pipeline (`heuristics.normalize_product`), a marca do item vem no nome, os termos de variedade do item aparecem ("integral" não casa com "desnatado") e a embalagem fica dentro de ±20% ("arroz 5kg" não casa com o pacote de 1 kg).
- **Regras (`allowed`):** respeita marca bloqueada, cadeia de marcas com `allow_others: false` e mercado bloqueado, resolvidos por `rules.resolve` no escopo da lista.
- **Desconto:** é a promoção da própria oferta (`1 - price/unit_price`). O preço médio pago serve só de trava: se a promoção não sair mais barata que a média histórica, não há sugestão. Comparar com a média gerava descontos falsos entre marcas e tamanhos diferentes.
- **Deduplicação:** só há uma sugestão `promo` ativa por item e lista. Ela é atualizada quando chega um desconto maior. Uma oferta dispensada (mesmo `sku_id`, preço igual ou maior) não volta.
- **Agregação (`aggregate_list`):** com duas ou mais `promo` ativas na lista, cria ou atualiza uma `list_promo` com `data.count`, `data.items` e `data.savings` (economia estimada: `(cheio - promo) x quantidade`).

O `Suggestion.data` de uma `promo` traz `action`, `discount`, `pct`, `price`, `reference` (cheio), `avg_paid`, `sku_id`, `merchant_id`, `merchant`, `quantity`, `simulated` e os campos `pattern_*`.

## API (contrato em `contract/openapi.json`)

- `POST /admin/promotions` `{query, discount_pct=25, merchant_id?}` devolve `PromotionResult`: `offers` (o que entrou em promoção), `evaluations` (o que aconteceu com cada item que casou, com `action`, `reason` e `pattern`) e `suggestions` (criadas ou atualizadas agora).
- `GET /admin/patterns` devolve o padrão de cada item de cada lista, para conferir por que algo foi ou não sugerido.
- `SuggestionView.kind` ganhou o valor `list_promo`. Mudança aditiva: o `SuggestionCard` do front já trata qualquer tipo diferente de `promo` como "Montar carrinho", que é a ação certa para `list_promo`.

## Teste manual (sem cadastrar compras e sem passar o tempo)

O seed já semeia o histórico necessário: café e leite com 4 compras (vencem hoje), sabão em pó com 2 compras (a última há 5 dias), arroz com 1 compra e o resto só com a recorrência de 30 dias da lista.

```bash
cd backend
export GNOMERS_MCP_MODE=fake GNOMERS_DB=sqlite:///$PWD/gnomers-fake.db
uv run python -m app.seed
uv run uvicorn app.main:app --port 8080
```

Em outro terminal (ou pelo Swagger em http://localhost:8080/docs):

```bash
# padrões: sabão em 17% do ciclo, café e leite em 100%, o resto pela recorrência da lista
curl -s localhost:8080/admin/patterns | jq '.[] | {raw_text, p: .pattern}'

# comprado há 5 dias e desconto pequeno: não sugere (veja evaluations[].reason)
curl -s -X POST localhost:8080/admin/promotions -H 'content-type: application/json' \
  -d '{"query": "sabão em pó", "discount_pct": 20}' | jq '.evaluations'

# desconto grande: sugere estocar
curl -s -X POST localhost:8080/admin/promotions -H 'content-type: application/json' \
  -d '{"query": "sabão em pó", "discount_pct": 35}' | jq '.suggestions'

# item vencendo: sugere adiantar e, com 2+ itens em promoção, aparece a list_promo
curl -s -X POST localhost:8080/admin/promotions -H 'content-type: application/json' \
  -d '{"query": "café", "discount_pct": 25}' | jq '.suggestions[].text'

curl -s localhost:8080/suggestions | jq '.[].text'   # o mesmo que a tela inicial mostra
```

Os cartões aparecem no topo da tela inicial do front. "Adiantar" ou "Montar carrinho" cria o plano da lista, e no modo `fake` o carrinho vem com o preço promocional. As promoções do simulador ficam em memória: para zerar, reinicie o back-end (o banco continua; rode o seed de novo para limpar as sugestões).

## Limites conhecidos (é uma prova de conceito)

- Um usuário só (`user_id = 1`), como o resto do app.
- O barramento é síncrono e em processo, sem persistência nem retry.
- "Adiantar" monta o carrinho da lista inteira, não só do item em promoção.
- A regularidade com uma ou duas compras é um chute fixo (0,5 ou 0,7).
- As datas de compra usam `Order.created_at` em UTC, e `today()` usa a data local com o deslocamento do `simulate-days`.
