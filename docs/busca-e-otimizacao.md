# Como funcionam a busca e a otimização

Este documento descreve o que acontece entre o clique em "Criar carrinho" e o carrinho pronto: onde ficam as preferências, como as buscas no MCP do iFood são montadas, como os resultados são filtrados e como o solver escolhe produtos e mercados. Descreve o código como ele está; o desenho original está em `plano-implementacao.md`.

## Visão geral

```text
Lista + preferências
  │
  ├─ 1. Resolução de regras por item ............ app/rules.py
  ├─ 2. Endereço ................................ app/pipeline/address.py
  ├─ 3. Busca, fase 1 (até 3 buscas por item) .... app/pipeline/discovery.py
  ├─ 4. Escolha de até 5 mercados + getMerchant
  ├─ 5. Busca, fase 2 (lacunas e marcas preferidas)
  ├─ 6. Normalização e filtros .................. app/pipeline/normalize.py
  ├─ 7. Otimização (MILP) ....................... app/pipeline/optimize.py
  ├─ 8. Carrinhos reais: cupom, entrega, Pix .... app/pipeline/materialize.py
  └─ 9. Cartões de decisão ...................... app/pipeline/decisions.py
```

O `app/pipeline/runner.py` orquestra tudo e publica o progresso por SSE. Aqui o LLM nunca chama o MCP: ele só interpreta texto, e quem decide produtos e mercados é o solver. O único loop agêntico é o chat sobre o carrinho pronto (`app/agent.py`).

## 1. Onde ficam as preferências

### Itens da lista

Cada item (`ListItem`) é interpretado uma vez, quando é salvo. O texto livre ("2 leite integral Italac 1L") vira campos:

| Campo | Exemplo | Para que serve |
|---|---|---|
| `query` | `leite integral Italac` | texto enviado ao `searchOffers` |
| `intention` | `product_exact` | `product_exact` quando há marca, senão `product` |
| `category` | `leite` | casa regras e reconhece o produto no catálogo |
| `brand` | `Italac` | marca pedida |
| `variety_terms` | `["integral"]` | variedade que o SKU precisa ter |
| `pack_size`, `pack_unit` | `1000`, `ml` | tamanho de uma embalagem (g, ml ou un) |
| `quantity` | `2` | quantas embalagens |

A interpretação vem do LLM (`app/llm.py`, tarefa `parse_item`) ou, sem LLM, das heurísticas de `app/heuristics.py`. O campo `parser_version` marca a versão: itens salvos por um parser antigo são reinterpretados no próximo carrinho, mantendo a marca escolhida nos chips.

### Regras (`Rule`)

Preferências ficam como regras tipadas, nunca como texto solto, porque cada tipo tem um destino concreto:

| `kind` | `payload` | Onde age |
|---|---|---|
| `dietary` | `{"tags": ["LAC_FREE"]}` | `refinements.itemRestrictions` da busca |
| `variety` | `{"terms": ["integral"]}` | dura: entra na `query`; suave: penalidade no solver |
| `brand_chain` | `{"order": ["Ypê", "Limpol"], "allow_others": true}` | busca extra por marca e penalidade por posição |
| `brand_block` | `{"brands": ["X"]}` | o SKU é descartado |
| `merchant` | `{"prefer": [id], "block": [id]}` | entra ou sai da lista de mercados candidatos |
| `delivery` | `{"max_fee": 8, "max_minutes": 60}` | `maxDeliveryFee` e `maxDeliveryTimeMinutes` |
| `quantity` | `{"exact": true, "max_overbuy": 1.1}` | descarta embalagem que passa do pedido |
| `note` | `{"text": "banana mais verde"}` | o LLM pontua cada SKU contra a nota |

Cada regra tem ainda `scope` (`global` ou o id de uma lista), `category` (ou nula, valendo para tudo), `hardness`, `source` (`user`, `chat`, `learned`), `confidence` e `active`.

**Resolução** (`rules.resolve`): para cada item, valem as regras ativas cuja categoria bate (ou é nula) e cujo escopo é global ou a lista atual. Entre duas regras do mesmo tipo vence a de maior prioridade, nesta ordem:

1. escopo da lista ganha do global;
2. regra de categoria ganha da regra geral;
3. origem: `user` > `chat` > `learned`;
4. maior confiança, depois a mais nova.

`note` e `brand_block` não competem: elas se somam. Uma nota que fala em "quantidade exata" também liga a regra de quantidade.

### Perfil

No `User` (e, se definido, sobrescrito na `ShoppingList`):

- `quality_profile` (0 a 1): o peso α da qualidade no solver. 0 é só preço.
- `max_merchants`: quantos mercados no máximo numa compra.
- `split_min_savings`: quanto a divisão entre mercados precisa economizar para valer.

### Regras aprendidas

`app/learning.py` transforma escolhas em sinais (`chosen`, `accepted`, `swapped_to`, `swapped_from`, `rejected`). Por categoria, quando uma marca tem pelo menos 3 sinais positivos e mais de 70% deles, nasce uma `brand_chain` suave com `source: learned`. Duas rejeições da mesma marca viram `brand_block` suave. Os sinais também ajustam a qualidade percebida de cada marca (±0,5 nível) na penalidade de qualidade.

## 2. Endereço

`listAddresses` é sempre a primeira chamada. A posição do navegador escolhe o endereço salvo mais próximo, dentro de 200 m ou da margem de erro do GPS (até 1,5 km). Na tela, o endereço escolhido pelo usuário vale mais que o GPS.

## 3. Busca, fase 1

**Como a busca do iFood se comporta** (testado no sandbox em 18/10): é por palavras, não semântica, e **exige todas as palavras** da busca. Tolera plural e acento ("Açúcares" casa com "açúcar", "Grãos" com "grão"), parece olhar também a descrição do produto e não conhece sinônimos: "granola sem açúcar" não traz as granolas "Zero Açúcar". Cada palavra a mais na busca elimina resultados.

Por isso cada item gera até três buscas, em paralelo. Quem decide as buscas é o LLM (tarefa
`plan_searches`, uma vez por item, guardada em `ListItem.search_plan` e refeita quando o texto ou a marca
mudam), porque traduzir "1 granola sem açucar (>500g)" para o vocabulário de rótulo é bom senso:

| Busca | Exemplo ("1 granola sem açucar (>500g)") | Para quê |
|---|---|---|
| específica | `granola sem açúcar` | o produto como está no rótulo, com a variedade pedida, sem quantidade nem palavras de uso |
| curta | `granola` | só o produto, mais a marca se houver ("leite Italac"); rede de segurança |
| alternativa | `granola zero açúcar` | outra forma de rótulo para a mesma variedade, quando existe |

O LLM também devolve as características exigidas (`must_have`, ex.: "sem açúcar"). O código impõe o
formato: no máximo uma alternativa, sempre com o nome do produto, e nada de tamanho ou número nas
exigências. Sem LLM, um plano B faz a limpeza com uma lista fixa de palavras e uma tabela pequena de
sinônimos (`app/heuristics.py`).

**Juiz por item:** a pergunta "este produto serve para o que o usuário pediu?" é uma decisão só, do
LLM (`judge_products`), com o pedido inteiro ("1 granola sem açucar (>500g)") e todos os produtos
encontrados. Para cada produto ele responde `exato`, `substituto` (mesmo produto sem alguma
característica pedida, com o motivo) ou `não serve` (outro produto, ou outra forma: em pó, condensado,
solúvel, cápsula, pronto quando o pedido é cru). "Não serve" sai; "substituto" entra no solver com
penalidade e só vira pergunta se havia opção exata nos mercados da compra.

O juiz não decide marca, preço nem tamanho: a marca sai do pedido antes de ir para ele (é preferência,
tratada como penalidade no solver), e o tamanho é filtrado no código (sem tamanho no nome, embalagem
grande demais ou menor que 10% do pedido). O veredito fica em cache por (pedido, produto), na tabela
`judgment`: a mesma lista não pergunta de novo. Sem LLM, as regras antigas (palavra da categoria no
começo do nome, lista de derivados, sinônimos fixos) viram o plano B do juiz.

Antes, essa decisão estava espalhada em quatro peças (o LLM dizendo se "é leite", uma lista de
derivados que podia vetá-lo, a comparação literal da variedade e uma segunda chamada ao LLM para os
casos duvidosos), e os erros que vimos caíam entre elas.

```json
{
  "query": "granola sem açúcar",
  "intention": "product",
  "resultKind": "ITEM",
  "merchantType": "GROCERIES",
  "userLocation": {"latitude": -22.01318, "longitude": -47.89623},
  "pageSize": 25,
  "refinements": {"onlyOpen": true, "maxDistanceKm": 8, "itemRestrictions": ["SUGAR_FREE"], "maxDeliveryFee": 8}
}
```

- **Ordenação por relevância** (o padrão; o app não manda `sort`). Com `sort: PRICE`, "feijão" trazia tempero, lámen e macarrão instantâneo antes de qualquer pacote de feijão. Quem compara preço é o solver.
- **Sem `itemTaxonomy`.** A busca não mostra a categoria de cada produto, então não dá para saber quantos estão sem categoria ou na errada, e um filtro errado esconde tudo (aconteceu com `LEITE_CONDENSADO` para leite). Quem decide o que é o produto é o app, pelo nome.
- **Qualificadores nunca vão na `query`.** O wrapper (`app/mcp/ifood.py`) recusa "barato", "perto" e afins.
- **`onlyOpen`:** desligado quando o carrinho tem horário de entrega (`deliver_at`), porque o iFood aceita carrinho em mercado fechado e agenda a entrega. Também desliga com `GNOMERS_ONLY_OPEN=0`.
- **Campos opcionais:** `itemRestrictions`, `maxDeliveryFee` e `maxDeliveryTimeMinutes` só entram se houver regra.
- **Preferência "product":** quando há uma regra de produto para a categoria (criada pelo chat, como "café torrado e moído"), a descrição dela vira a busca específica.

**O que se aproveita da resposta:** de cada resultado, `itemId`, `itemName`, `itemEan`, `unitPrice`, `promotionalUnitPrice` (o preço efetivo; no sandbox ele vem sempre, igual ao `unitPrice` quando não há promoção) e o resumo do `merchant` (`merchantId`, nome, `minimumOrderAmount`, distância, `deliveryMethod`). Algumas tools embrulham a resposta em `{"success": true, "data": {...}}`, e o cliente desembrulha.

## 4. Escolha dos mercados

Com os resultados da fase 1, conta-se a **cobertura** de cada mercado (quantos itens da lista apareceram nele). Os mercados são ordenados por cobertura e, no empate, por `frete + 0,1 × pedido mínimo`. Os bloqueados saem, os preferidos entram, e ficam até 5 candidatos.

Para cada candidato há um `getMerchant`, que traz o pedido mínimo e os métodos de entrega.

**Frete estimado:** `promotionalFee` quando existe, senão `fee`. Foi confirmado montando carrinhos em 18/10 que `fee: 6.49, promotionalFee: 0` sai de fato com frete grátis. Exceção: mercado fechado vem com o método de entrega vazio e tudo zerado, e aí não há informação.

## 5. Busca, fase 2

- **Lacunas:** para cada par (item, mercado) sem resultado, nos 3 mercados que mais cobrem a lista, um `searchOffers` com a **busca curta** restrito ao mercado (`refinements.merchantId`, `pageSize: 10`, sem `onlyOpen`). A busca curta traz as opções daquele mercado, e o app confere a variedade depois.
- **Marcas preferidas:** para itens com `brand_chain`, se uma marca da cadeia não apareceu, busca `"<produto> <marca>"` com `product_exact`, na ordem da cadeia, até achar.

No fim, as ofertas são deduplicadas e só ficam as dos mercados candidatos.

## 6. Normalização e filtros

O catálogo de mercado não traz marca, variedade nem tamanho como campos, só o nome ("Leite Uht Integral com Tampa Italac 1l"). A normalização extrai isso.

**Cache:** a tabela `Product` guarda a normalização por `itemId`. Um SKU com o mesmo **EAN** de outro já normalizado (o mesmo produto em outro mercado) reaproveita a normalização sem chamar o LLM.

**Extração:** o LLM (`normalize_products`, em lotes de 30) ou a heurística devolve marca, variedade, tamanho, unidade e nível de qualidade (1 marca própria, 2 comum, 3 premium).

**Se o produto serve** é decisão do juiz (seção 3): "não serve" é descartado, "substituto" recebe penalidade.

**Descartes por oferta:**

- o juiz disse que não serve (outro produto ou outra forma, como em pó ou pronto);
- marca bloqueada (`brand_block`);
- mercado bloqueado;
- substituto quando a variedade é obrigatória (`variety` dura).

**Tamanho na unidade do item:** "arroz 5kg" pede 5000 g, e um SKU de 1 kg cobre 1000 g por embalagem. Itens sem tamanho ("pão de forma") contam embalagens.

**Penalidades,** em reais, calculadas sobre o custo estimado do item naquele SKU:

| Penalidade | Quando | Valor |
|---|---|---|
| substituição | marca fora da cadeia, com outras permitidas | R$ 2 + 25% do custo |
| substituição | marca fora da cadeia, sem outras permitidas | R$ 60 + 100% do custo |
| substituição | k-ésima marca da cadeia (k > 1) | R$ 0,50 + 8% × posição × custo |
| substituição | o juiz classificou como substituto | R$ 1 + 15% do custo |
| qualidade | nível baixo (antes do α) | (3 − nível) / 2 × 30% do custo |
| nota | regra `note` | (1 − nota do LLM) × 20% do custo |

**Filtros por item.** Cada um só é aplicado se sobrar alguma opção; senão é melhor manter tudo do que deixar o item de fora:

- tamanho escolhido pelo usuário num carrinho anterior ("refazer carrinho");
- SKU sem tamanho no nome sai quando existe outro com tamanho conhecido;
- embalagem com mais que o dobro do pedido sai quando há opção menor;
- embalagem com menos de 10% do pedido (sachê de 10 g para "café 500 g") sai quando há opção maior;
- com uma preferência de produto ("na verdade quero X"), só fica o que o juiz considerou exato;
- com a regra `quantity`, sai o que passa de `max_overbuy` (110%) do pedido.

Por fim, ficam os **5 melhores SKUs por item e por mercado** (preço por unidade mais penalidades), para o modelo continuar pequeno. Cada item recebe uma penalidade de falta de `max(R$ 25; 2,5 × custo mediano)`.

## 7. Otimização

Programação linear inteira com PuLP 2.9 e o solver CBC.

```text
Conjuntos   I itens, M mercados candidatos, S(i) SKUs candidatos do item i

Variáveis   x[s] ∈ {0,1}   SKU s escolhido
            n[s] ∈ ℤ≥0     embalagens do SKU s
            y[m] ∈ {0,1}   mercado m usado
            u[i] ∈ {0,1}   item i fica de fora

Objetivo    min  Σ preço[s]·n[s] + Σ frete[m]·y[m]
               + Σ (α·qualidade[s] + substituição[s] + nota[s])·x[s]
               + Σ falta[i]·u[i]

Restrições  Σ_{s∈S(i)} x[s] + u[i] = 1          cada item: um SKU ou falta
            n[s] = ⌈pedido[i] / tamanho[s]⌉·x[s]  exatamente as embalagens necessárias
            x[s] ≤ y[m(s)]                       só compra de mercado usado
            Σ_{s∈m} preço[s]·n[s] ≥ mínimo[m]·y[m]   pedido mínimo
            y[m] ≤ Σ_{s∈m} x[s]                  mercado sem item não é usado
            Σ y[m] ≤ max_merchants
```

- **α** é o `quality_profile` do usuário (ou da lista).
- **Quantidade fixa:** o número de embalagens é exatamente o necessário para cobrir o pedido. O solver nunca põe unidades a mais para bater o pedido mínimo; se a lista não chega ao mínimo, o item fica de fora e a tela explica com números.
- **Um mercado ou vários:** o modelo roda duas vezes, com no máximo 1 mercado e com `max_merchants`. A versão dividida só vale se o objetivo (preço, frete e penalidades) cair mais que `split_min_savings`.
- **Alternativas:** o modelo roda mais vezes com um corte que proíbe repetir o mesmo conjunto de mercados, gerando até 3 soluções.

## 8. Carrinhos reais

A estimativa não conhece cupons, taxas de serviço nem o frete final. Por isso cada uma das 3 soluções vira carrinho de verdade no iFood:

1. `createCart` com os SKUs e as quantidades. Carrinhos idênticos entre soluções são reaproveitados. Só se o iFood exigir opções do produto, `getItem` antes.
2. `getCartDeliveryMethods`: o método mais barato e, com horário pedido, o horário que cobre o momento. `setCartDelivery` só se a entrega que o `createCart` trouxe não for a escolhida.
3. `listVouchers`: os cupons são ordenados pelo valor, e no máximo 2 são testados com `setVoucher`. O cupom sai (`removeVoucher`) se não baixar o total.
4. `listPaymentMethods` e `setCartPayment` com Pix (cartão exige um `cardTokenId` que o MCP não expõe). A resposta já traz o total final, sem `getCart`.

Carrinhos alternativos só são montados quando a estimativa deles está a até 15% da melhor. Falhas de cupom, pagamento ou entrega não derrubam o carrinho.

**Vence a solução com menos itens de fora e, depois, o menor `totalAmount` real somado.** Os carrinhos das soluções perdedoras ficam abandonados, porque o MCP não tem tool para apagar carrinho.

## 9. Depois do carrinho

- **Cartões de decisão:** comparam o escolhido com o pedido (marca trocada, variedade diferente, item em falta, quantidade maior, compra dividida). Substituição dentro da cadeia de marcas do próprio usuário só informa.
- **Trocas pelo cartão ou pelo chat:** mexem no carrinho real (`addItemToCart`, `removeItemFromCart`, `updateCartItem`). Em seguida, o back-end refaz a otimização **em memória**, com os candidatos guardados no plano e a escolha do usuário fixada. Se refazer o carrinho sair mais de 5% (ou R$ 3) mais barato, aparece o cartão "Dá para pagar menos", e "Refazer carrinho" cria um plano novo mantendo endereço, horário e as escolhas.

## Limitações conhecidas

- **Cobertura limitada pelas buscas.** Um produto que nenhuma das 3 buscas traz, nos 5 mercados candidatos, não entra. Falta uma segunda rodada de busca quando o juiz acha poucas opções.
- **Plano B sem LLM mais fraco.** Sem LLM, a lista de derivados e a tabela de sinônimos (`LABEL_SYNONYMS` em `app/heuristics.py`) são mantidas à mão.
- **Só 3 soluções viram carrinho.** Uma combinação em 4º lugar na estimativa nunca tem cupons testados.
- **Frete de mercado fechado desconhecido** até o `getMerchant` ou o carrinho.
