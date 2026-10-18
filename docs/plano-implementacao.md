# Plano de implementação: listas de compras com agente

Este é o plano da ideia escolhida pelo time para o hackathon SEMCOMP × iFood. O usuário mantém listas de compras de mercado. O sistema monta o carrinho otimizado no iFood respeitando preferências estruturadas, mostra o preço final real e usa o chat só para resolver decisões pontuais. O plano substitui a ideia de pedido em grupo descrita no `HANDOFF.md`, que continua valendo como contexto sobre o MCP, o setup e a pesquisa.

## 1. Decisões de arquitetura

**O LLM entende e o solver decide.** O LLM faz o que é ambíguo: transformar o texto de um item em uma busca bem formada, extrair marca, variedade e tamanho do nome de cada produto do catálogo, encaixar respostas do chat em regras e redigir as perguntas. A escolha de produtos e mercados é um modelo de programação linear inteira resolvido por código (PuLP com CBC). O LLM não gera código de otimização em tempo de execução, porque isso seria lento, não determinístico e difícil de depurar.

**Preferências estruturadas, texto livre só como sobra.** O usuário pode dizer a preferência em linguagem natural, mas o sistema guarda como regra tipada, porque cada preferência tem um destino concreto na chamada ao MCP ou no solver (ver seção 4). Só o que não tem campo e não descreve o produto ("banana mais verde") fica como nota de texto, presa a uma categoria e a um escopo.

**O back-end é dono das chamadas ao MCP.** O LLM nunca chama as tools do iFood diretamente. Ele aciona funções do back-end, que validam os parâmetros e fazem as chamadas. Isso mantém o fluxo auditável e evita que o modelo se perca nos UUIDs.

**Toda resposta do MCP é gravada.** Um modo de gravação e reprodução guarda as respostas em `fixtures/`. Isso permite testar o solver sem rede, apresentar a demo mesmo se o sandbox cair e documentar o comportamento real da tool para o feedback.

## 2. Stack e estrutura do repositório

O back-end é Python com FastAPI, SQLModel sobre SQLite, PuLP para o solver, o SDK `mcp` para o cliente MCP e APScheduler para os jobs. O front-end é um PWA em React com Vite, que dá acesso à geolocalização do navegador. O LLM é chamado por API com saída em JSON validada por esquema.

```text
backend/
  app/
    main.py              rotas FastAPI
    config.py            leitura do .env
    db.py, models.py     SQLModel
    rules.py             modelos Pydantic das regras e resolução por escopo
    llm.py               tarefas do LLM com esquema de saída
    mcp/
      client.py          sessão MCP com OAuth, chamada genérica, normalização de erros
      recorder.py        modos live, record e replay
      ifood.py           wrappers tipados de cada tool usada
    pipeline/
      address.py         endereço a partir da geolocalização
      discovery.py       busca em duas fases
      normalize.py       parsing de produtos e cache
      optimize.py        modelo MILP
      materialize.py     carrinhos reais, cupom, entrega e total
      decisions.py       geração dos cartões de decisão
    learning.py          sinais implícitos e regras aprendidas
    scheduler.py         recorrência e promoções
  fixtures/              respostas gravadas do MCP
  tests/                 testes do solver e da resolução de regras
frontend/
  src/pages/             Listas, Lista, Preferências, Carrinho, Pedido
  src/components/        ItemRow, RuleCard, DecisionCard, ChatDrawer, SuggestionCard
scripts/setup_ifood_mcp.py
docs/
```

## 3. Modelo de dados

```text
User(id, name, quality_profile: float 0..1, max_merchants: int,
     split_min_savings: float | null, ifood_token: json)
Address(id, user_id, ifood_address_id, alias, lat, lng, last_synced)

ShoppingList(id, owner_id, name, recurrence_days: int | null, next_due: date | null, shared: bool)
ListMember(list_id, user_id)                                          # grupo, opcional
ListItem(id, list_id, raw_text, quantity: float,
         category, brand | null, variety_terms: list[str],
         pack_size | null, pack_unit | null,
         query, intention: "product" | "product_exact", taxonomy_id | null,
         recurrence_days | null, next_due | null, requested_by | null)

Rule(id, user_id, scope: "global" | list_id, category | null,
     kind, payload: json, hardness: "hard" | "soft",
     source: "user" | "chat" | "learned", confidence: float, active: bool, created_at)

Product(ifood_item_id, merchant_id, name, brand, category, variety_terms,
        pack_size, pack_unit, quality_tier: 1..3, parsed_at)            # cache da normalização

Plan(id, user_id, list_id, status, address_id, result: json, created_at)
Order(id, plan_id, merchant_id, ifood_cart_id, ifood_order_id, total, status, created_at)
OrderLine(order_id, list_item_id, ifood_item_id, brand, unit_price, qty,
          was_substitute: bool, user_action: "accepted" | "swapped" | "removed" | null)
Signal(id, user_id, category, brand, kind: "chosen" | "accepted" | "swapped_to" |
       "swapped_from" | "rejected", created_at)
```

Os campos `query`, `intention`, `variety_terms`, `pack_size` e `taxonomy_id` do `ListItem` são preenchidos pelo LLM quando o item é salvo, e não a cada compra. O `taxonomy_id` é preenchido na primeira busca real, a partir dos facets.

## 4. Preferências

### Tipos de regra

| kind | payload | hardness típica | destino |
|---|---|---|---|
| `dietary` | `{"tags": ["LAC_FREE", "ORGANIC"]}` | hard | `refinements.itemRestrictions` |
| `variety` | `{"terms": ["integral"]}` | hard ou soft | termos da `query` (hard) ou penalidade (soft) |
| `brand_chain` | `{"order": ["Ypê", "Limpol"], "allow_others": false}` | soft | ordem das buscas `product_exact` e penalidade no solver |
| `brand_block` | `{"brands": ["X"]}` | hard | exclusão no solver |
| `merchant` | `{"prefer": [...], "block": [...]}` | hard ou soft | `refinements.merchantId`, exclusão ou penalidade |
| `delivery` | `{"max_fee": 8, "max_minutes": 60}` | hard | `maxDeliveryFee`, `maxDeliveryTimeMinutes` |
| `note` | `{"text": "banana mais verde"}` | soft | pontuação dos candidatos pelo LLM |

O perfil de economia ou qualidade, o limite de mercados e a tolerância a dividir ficam no `User`, com possibilidade de sobrescrever por lista.

### Resolução

Para cada item, o sistema reúne as regras ativas cuja categoria bate com a do item (ou nula) e cujo escopo é global ou a lista atual. Quando duas regras do mesmo tipo conflitam, vence a mais específica (lista sobre global, categoria sobre nula) e, em empate, a de origem mais forte (user sobre chat sobre learned). Essa função fica em `rules.py` e é a mais testada do projeto, já que tudo depende dela.

### Compilação de texto livre

Quando o usuário escreve uma nota ou responde no chat, o LLM tenta encaixar o conteúdo num dos tipos acima e mostra a proposta para confirmação ("Salvar como regra? Detergente: Ypê, depois Limpol. Vale para todas as listas ou só esta?"). O que não encaixar fica como `note`.

## 5. Tarefas do LLM

Todas têm entrada e saída com esquema JSON validado. Para as tarefas em lote, um modelo rápido basta. Para a extração de regras do chat, vale um modelo mais forte.

| Tarefa | Entrada | Saída | Quando roda |
|---|---|---|---|
| `parse_item` | texto do item | `query`, `intention`, `category`, `brand`, `variety_terms`, `pack_size`, `pack_unit`, `quantity` | ao salvar o item |
| `normalize_products` | categoria do item e nomes dos SKUs | marca, variedade, tamanho, unidade, `quality_tier`, se casa com o item | uma vez por SKU, com cache |
| `pick_taxonomy` | categoria e facets da busca | ID de taxonomia ou nulo | primeira busca da categoria |
| `score_notes` | notas da categoria e candidatos | nota de 0 a 1 por SKU | só quando existe `note` |
| `extract_rule` | mensagem do usuário e decisão em aberto | ação no carrinho e regra proposta | chat |
| `phrase_decision` | decisão estruturada | texto curto para o cartão | geração dos cartões |
| `explain` | resultado do plano e pergunta | resposta | chat |

Regras para a `query` que o `parse_item` deve seguir: só termos descritivos, nada de "barato" ou "perto"; marca específica leva `intention: product_exact`; variedade descritiva ("integral", "caipira", "extraforte") entra na `query`.

## 6. Cliente MCP

O back-end usa o SDK `mcp` com transporte streamable HTTP e o provedor de OAuth do próprio SDK (confirmar a API na versão instalada). O armazenamento de tokens grava no `User.ifood_token`. O back-end roda na porta 8080 e serve `GET /callback`, o que reaproveita a redirect `http://localhost:8080/callback` já cadastrada no agente. O front-end roda na 5173 com proxy para a 8080.

O wrapper `call_tool` normaliza os três formatos de erro do MCP (`errors`, `error` e campos na raiz) num único tipo, respeita `retryable`, aplica timeout e registra cada chamada no `recorder`. Nos modos `record` e `replay`, a chave é o nome da tool mais o hash dos argumentos.

## 7. Pipeline "Criar carrinho"

`POST /lists/{id}/plan` cria um `Plan` e roda o pipeline em segundo plano, publicando o progresso por SSE para a tela mostrar as etapas.

### 7.1 Endereço

O front-end manda as coordenadas do navegador. O back-end sincroniza o `listAddresses` (cache de alguns minutos), calcula a distância até cada endereço salvo e usa o mais próximo se estiver a menos de 200 m. Em empate ou ambiguidade, usa a contagem de pedidos de GROCERIES do próprio `listAddresses` e o `isLastUsedAddress`. Se nenhum endereço estiver perto, a tela pede o endereço uma única vez e o back-end chama `createAddress` no fluxo de duas etapas.

### 7.2 Descoberta em duas fases

Na fase 1, para cada item, o sistema chama `searchOffers` com a `query` e a `intention` do item, `resultKind: ITEM`, `merchantType: GROCERIES`, `sort: PRICE`, `pageSize: 25` e `refinements` com `onlyOpen`, `maxDistanceKm`, `maxMinimumOrderAmount`, `itemRestrictions` das regras `dietary`, `itemTaxonomy` quando já conhecido, e `maxDeliveryFee` ou `maxDeliveryTimeMinutes` das regras `delivery`. As chamadas rodam em paralelo com limite de concorrência.

Com os resultados, o sistema conta a cobertura de cada mercado, ou seja, quantos itens da lista apareceram nele, e escolhe até cinco mercados candidatos por cobertura, frete e pedido mínimo. Mercados bloqueados saem e preferidos entram. Para cada candidato, chama `getMerchant` para ter `minimumOrderAmount` e os métodos de entrega.

Na fase 2, as lacunas são preenchidas. Para cada par de item e mercado candidato sem resultado, o sistema chama `searchOffers` com `refinements.merchantId`. Para itens com `brand_chain`, se a primeira marca não apareceu, busca a próxima com `product_exact` antes de cair numa busca genérica.

Se os facets da fase 1 trouxerem a categoria do item, o `pick_taxonomy` grava o ID para as próximas compras.

### 7.3 Normalização

SKUs ainda fora da tabela `Product` vão em lote para o `normalize_products`. Com marca e tamanho, o sistema calcula preço por unidade e o número de embalagens necessário, descarta os que não casam com o item e aplica as regras duras (`brand_block`, `variety` hard, `merchant` block). Para cada item e mercado, ficam os cinco melhores candidatos por preço unitário ajustado pelas penalidades, o que mantém o modelo pequeno.

### 7.4 Otimização

```text
Conjuntos
  I itens, M mercados candidatos, S(i,m) candidatos do item i no mercado m

Variáveis
  x[s] ∈ {0,1}   SKU s escolhido
  n[s] ∈ ℤ≥0     embalagens do SKU s
  y[m] ∈ {0,1}   mercado m usado
  u[i] ∈ {0,1}   item i não atendido

Objetivo
  min  Σ preço[s]·n[s] + Σ frete[m]·y[m]
     + α · Σ pen_qualidade[s]·x[s]
     + Σ pen_substituição[s]·x[s] + Σ pen_nota[s]·x[s]
     + Σ PENAL_FALTA·u[i]

Restrições
  Σ_{s∈S(i)} x[s] + u[i] = 1                  cada item tem um SKU ou fica em falta
  tam[s]·n[s] ≥ qtd[i]·x[s]                    quantidade coberta
  tam[s]·n[s] ≤ 1.3·qtd[i] + tam[s]            sem exagero de embalagens
  n[s] ≤ N_MAX·x[s],  x[s] ≤ y[m(s)]          ligações
  Σ_{s∈m} preço[s]·n[s] ≥ mínimo[m]·y[m]      pedido mínimo
  Σ y[m] ≤ max_merchants
```

O `α` vem do `quality_profile`. A penalidade de qualidade usa a `quality_tier` do LLM, ajustada pelo histórico de sinais da categoria. A penalidade de substituição cresce com a posição da marca na `brand_chain` e é alta para marcas fora da cadeia quando `allow_others` é falso. A penalidade de nota vem do `score_notes`.

A tolerância a dividir a compra é resolvida com duas execuções, uma com `Σ y[m] ≤ 1` e outra com o limite do usuário, e a versão com vários mercados só é aceita se economizar mais que `split_min_savings`. Para ter alternativas para a etapa seguinte, o solver roda mais duas vezes adicionando um corte que proíbe o mesmo conjunto de mercados da solução anterior, gerando até três soluções.

O módulo `optimize.py` é puro (entrada e saída em dataclasses, sem chamada externa) e tem testes com fixtures.

### 7.5 Carrinhos reais e preço final

Para cada uma das até três soluções e cada mercado dela, o sistema chama `createCart` com o `addressId` e os itens. Itens com `optionGroups` obrigatórios passam por `getItem` antes, o que deve ser raro em mercado. Depois, chama `listVouchers` e testa cada cupom com `setVoucher`, lendo o total com `getCart`; chama `getCartDeliveryMethods` e escolhe o método conforme o perfil (o mais barato por padrão); e chama `listPaymentMethods` e pré-seleciona PIX com `setCartPayment`.

A solução vencedora é a de menor soma de `totalAmount` real. O preço mostrado ao usuário é esse, com frete e cupom já aplicados. Os carrinhos das soluções perdedoras são abandonados. Como o MCP não tem tool para apagar carrinho, isso vai para o feedback.

### 7.6 Cartões de decisão

O `decisions.py` compara a solução escolhida com o que a lista pediu e gera um cartão para cada caso relevante. Ele gera cartão quando o SKU escolhido é de marca fora da cadeia do usuário ou quando não havia regra para a marca pedida, quando a variedade é diferente da pedida e a regra era suave, quando um item ficou em falta, quando a compra foi dividida entre mercados ou quando a quantidade ficou bem diferente por causa das embalagens. Substituições dentro de uma `brand_chain` que o próprio usuário definiu já estão autorizadas, então aparecem só como informação, sem pergunta.

Cada cartão já mostra a escolha aplicada e oferece aceitar, trocar ou remover, com a opção "salvar como regra" e o seletor de escopo.

### 7.7 Pedido e acompanhamento

`POST /plans/{id}/checkout` chama `getCart` em cada carrinho para confirmar que nada mudou e depois `submitOrder`. O código Pix de cada pedido aparece na tela. O `GET /orders/{id}/track` chama `trackOrder` respeitando o intervalo do `retryMessage` e para quando `pollingStop` é verdadeiro. Cada escolha final vira `OrderLine` e `Signal`.

## 8. Chat

O chat abre ancorado num cartão de decisão. A mensagem vai para o `extract_rule` junto com o contexto do cartão, e a resposta tem uma ação no carrinho (trocar SKU, remover, mudar quantidade), que o back-end executa com `updateCartItem` ou `removeItemFromCart` seguido de `getCart` para atualizar o total, e uma regra proposta, que a tela mostra para confirmação. Perguntas abertas ("por que esse mercado?") vão para o `explain`, que recebe o resultado do solver e explica sem refazer a conta.

## 9. Aprendizado implícito

Cada escolha gera sinais. Escolher um SKU gera `chosen`, aceitar uma substituição gera `accepted`, trocar um produto gera `swapped_from` para o antigo e `swapped_to` para o novo, e recusar gera `rejected`. Trocas manuais pesam mais que aceitações passivas.

O `learning.py` calcula, por categoria, uma pontuação por marca. Quando uma marca tem pelo menos três sinais positivos e mais de 70% da categoria, o sistema cria ou atualiza uma `brand_chain` suave com `source: learned` e confiança proporcional. Um `rejected` repetido cria `brand_block` suave. O mesmo módulo ajusta o `quality_profile` quando o usuário troca sistematicamente para produtos mais caros ou mais baratos.

A periodicidade sai das datas de compra de cada item. Com duas compras ou mais, a mediana dos intervalos preenche o `next_due` quando o usuário não definiu recorrência.

Para não começar do zero, o primeiro login importa `getRecommendations(contentMode: RECENT_ORDERS, resultKind: ITEM, merchantType: GROCERIES)`, que traz `timesOrdered` e `lastOrderedAt`, e pode pedir ao LLM preferências extraídas do `userBehaviorDescription` do `getUser`. Tudo isso entra como `learned` com confiança baixa. Para a demo, um script semeia um histórico fictício de algumas compras.

As regras aprendidas aparecem na tela de preferências com o selo "aprendido" e um botão para desligar.

## 10. Recorrência e proatividade

O job diário do `scheduler.py` pega itens e listas com `next_due` nos próximos sete dias. Para cada item, roda a `query` salva com `refinements.onlyWithPromotion: true` e compara o `promotionalUnitPrice` com o preço médio do histórico. Se o desconto passar de um limiar (15%, por exemplo), cria uma sugestão ("Leite integral 22% mais barato hoje no Mercado X, e você compra toda semana. Adiantar?"). Quando a lista inteira vence, a sugestão oferece criar o carrinho.

As sugestões aparecem como cartões no topo da tela inicial. Na demo, `POST /admin/simulate-days` avança o relógio e dispara o job.

## 11. Compras em grupo (opcional)

O modelo já tem `ListMember` e `ListItem.requested_by`. A divisão da conta é cada um pagando os próprios itens e uma fatia proporcional do frete e do cupom. O pagamento antecipado por Pix via PSP, desenhado no `HANDOFF.md`, encaixa aqui. Fica para depois do fluxo principal e, se não der tempo, entra no pitch como próximo passo.

## 12. API

| Método e rota | Função |
|---|---|
| `GET /auth/login`, `GET /callback` | OAuth com o iFood |
| `GET/POST /lists`, `GET/PATCH/DELETE /lists/{id}` | listas |
| `POST/PATCH/DELETE /lists/{id}/items[/{item}]` | itens (o POST roda `parse_item`) |
| `GET/POST/PATCH/DELETE /rules` | preferências, com filtro por escopo |
| `POST /rules/compile` | texto livre para regra proposta |
| `POST /location` | coordenadas para endereço resolvido |
| `POST /lists/{id}/plan`, `GET /plans/{id}`, `GET /plans/{id}/events` | criação do carrinho e progresso por SSE |
| `POST /plans/{id}/decisions/{d}` | resposta a um cartão, com regra opcional |
| `POST /plans/{id}/chat` | chat ancorado |
| `POST /plans/{id}/checkout`, `GET /orders/{id}/track` | pedido e rastreio |
| `GET /suggestions`, `POST /admin/simulate-days` | proatividade |

## 13. Telas

A **tela inicial** mostra as listas com a próxima data prevista e o último total, e no topo as sugestões proativas.

A **tela da lista** é uma lista editável em linha. Ao digitar um item, o `parse_item` mostra as interpretações como chips (marca, variedade, quantidade, recorrência) que o usuário pode corrigir. O botão principal é "Criar carrinho".

A **tela de preferências** tem abas Global e Esta lista, cartões de regra com o selo da origem (você, chat, aprendido) e um interruptor, um controle deslizante entre economia e qualidade e os campos de limite de mercados e economia mínima para dividir.

A **tela do carrinho** mostra o progresso do pipeline enquanto roda e depois os grupos por mercado, com os itens escolhidos, o detalhamento do total (produtos, frete, cupom) e os cartões de decisão. O chat abre como gaveta a partir de um cartão. O botão final é "Confirmar e pedir".

A **tela do pedido** mostra o código Pix de cada pedido e o rastreamento.

## 14. Fases e critérios de pronto

| Fase | Prazo | Entrega | Pronto quando |
|---|---|---|---|
| 0 | 23h30 | Teste do catálogo GROCERIES no sandbox, esqueleto do repo, cliente MCP com OAuth e gravação | `browseOffers` e `searchOffers` de mercado respondem pelo back-end e ficam gravados em fixtures; decisão sobre live ou replay |
| 1 | 3h | Listas, itens com `parse_item`, regras com resolução, endereço por geolocalização, descoberta em duas fases e normalização | uma lista de 15 itens gera candidatos normalizados por mercado |
| 2 | 7h | Solver com divisão e alternativas, carrinhos reais com cupom e total, cartões de decisão, checkout e rastreio | o botão "Criar carrinho" mostra total real e pelo menos um cartão; o pedido sai com Pix |
| 3 | 9h30 | Chat salvando regra, aprendizado implícito com histórico semeado, recorrência e sugestão por promoção | responder um cartão cria regra visível nas preferências; simular dias gera sugestão |
| 4 | 12h30 | Polimento, vídeo de backup da demo, entregáveis | demo roda do início ao fim em modo live e em replay |

Divisão sugerida das quatro pessoas:

| Pessoa | Responsabilidade |
|---|---|
| A | cliente MCP, gravação, endereço, descoberta, carrinhos reais, checkout, rastreio |
| B | regras, normalização, solver, cartões de decisão, aprendizado |
| C | front-end inteiro e integração com SSE |
| D | tarefas do LLM com esquemas, chat, proatividade, entregáveis, roteiro da demo e pitch |

Se a fase 2 atrasar, a fase 3 encolhe na ordem inversa: primeiro sai a sugestão por promoção, depois o aprendizado, e o chat salvando regra é o último a sair porque é o que mais aparece no pitch.

## 15. Riscos

| Risco | Mitigação |
|---|---|
| Catálogo de mercado pobre no sandbox | descobrir na fase 0; se for o caso, demo em replay com fixtures gravadas, deixando isso claro para a banca |
| Produtos sem peso e tamanho | `normalize_products` com cache; quando não der para extrair, tratar como unidade |
| Muitas chamadas e latência | descoberta em duas fases, paralelismo com limite, cache por sessão, progresso por SSE |
| Cupom e total só existem no carrinho | materializar só as três melhores soluções |
| Carrinhos abandonados sem tool de exclusão | registrar no feedback; não afeta o usuário |
| Vários mercados geram vários pagamentos | mostrar um código Pix por pedido |
| LLM devolve JSON inválido | validação com esquema e uma nova tentativa com o erro; nunca seguir com saída inválida |
| OAuth falha na hora da demo | token salvo no banco e modo replay como plano B |

## 16. Diferenciais em relação à Zapia

A Zapia já integra com o iFood, compara preços entre mercados e escolhe produtos, então o pitch não deve negar isso. O diferencial está em como cada decisão acima resolve o que um assistente generalista de chat faz mal.

Compra recorrente é edição de lista, não conversa, e por isso a interface é por lista. Na Zapia, repetir a compra do mês passado exige achar a conversa antiga e ler muito texto. Aqui é abrir a lista e apertar um botão.

A memória da Zapia é o histórico da conversa. Aqui ela é um conjunto de regras com escopo, prioridade e origem, que o usuário vê, edita e desliga, e que é aplicado sempre do mesmo jeito porque quem decide é um solver. A mesma lista gera o mesmo carrinho.

O preço mostrado sai de carrinhos reais montados antes, com frete e o melhor cupom, então não muda no checkout.

O endereço vem do GPS cruzado com os endereços salvos que o MCP expõe, enquanto a Zapia pergunta a cada compra.

O sistema aprende marcas e periodicidade sem configuração e avisa quando algo que você compra sempre está em promoção, o que um chatbot só sabe se você contar.

Antes do pitch, vale passar para a Zapia uma lista de uns 20 itens com uma restrição de marca e conferir se a soma bate, se o frete entrou e se ela cogitou dividir a compra. Se algo falhar, vira um slide.

## 17. Roteiro da demo

1. Abrir o app, que já detecta o endereço pela localização.
2. Abrir a lista "Compra do mês", com itens genéricos e de marca e uma recorrência de 30 dias.
3. Mostrar as preferências, incluindo uma regra criada pelo usuário ("detergente: Ypê, depois Limpol") e uma aprendida ("café: Pilão, aprendido de 4 compras").
4. Criar o carrinho, mostrando as etapas do pipeline e o resultado com total real, a divisão entre dois mercados com a economia e um cartão de substituição.
5. Responder o cartão no chat e salvar como regra, mostrando a regra nova na tela de preferências.
6. Fechar o pedido com Pix e mostrar o rastreio.
7. Simular uma semana e mostrar a sugestão de leite em promoção.
8. Comparar com a mesma tarefa na Zapia.

## 18. Entregáveis

| Entregável | Fonte |
|---|---|
| Proposta de negócio | seções 1 e 16, mais a pesquisa de mercado do `HANDOFF.md` |
| Desenho de solução | seções 2, 7 e 8, com um diagrama do pipeline |
| Racional da experiência | seções 8 e 13 (lista como interface principal, chat ancorado, decisão já aplicada) |
| Interface visual | o próprio front-end e capturas de tela |
| Documento técnico | seções 3 a 7 e 12 |
| Feedback dos MCPs | `docs/mcp-feedback.md` |
