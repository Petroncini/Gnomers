# Feedback sobre o MCP do iFood

Pontos encontrados na documentação, a confirmar na prática:

- `getItem` devolve `optionGroups[].options[].optionId`, mas `createCart`/`addItemToCart` pedem `options[].customizations[].customizationId`, sem explicar o mapeamento.
- `listPaymentMethods` sempre devolve `cardTokenId` nulo, mas `setCartPayment` exige esse campo para cartão.
- Os horários de `getCartDeliveryMethods` não têm ID na doc, mas `setCartDelivery` pede `selectedTimeSlotId`. **No sandbox eles vêm com `timeslotId`**; o problema é só a documentação.
- O formato de erro muda entre MCPs: `errors` (Offer Discovery, User), `error` (Cart) e campos na raiz (Order Tracking).
- As contagens de `listAddresses` vêm como string.
- Não existe tool para listar pedidos passados nem tool de pós-venda (reclamação, reembolso, cupom).
- Produtos de mercado não têm peso nem tamanho de embalagem.
- `submitOrder` com Pix: a documentação não diz em qual campo vem o código Pix.
- `searchOffers`, `browseOffers` e `getRecommendations`: a descrição de `refinements` mapeia "bem avaliado" para `minRating + sort:RATING`, mas o enum de `sort` não tem `RATING` (só RELEVANCE, DELIVERY_FEE, DELIVERY_TIME, DISTANCE, PRICE).

## Divergências entre a documentação e o `searchOffers` real

- A doc chama o filtro de dieta de `refinements.dietary` (VEGETARIAN, VEGAN, GLUTEN_FREE). Na prática ele se chama `itemRestrictions` e aceita também LAC_FREE, SUGAR_FREE e ORGANIC.
- O parâmetro `intention` (product, product_exact, merchant, merchant_exact) existe na tool mas não aparece na doc.
- `refinements` tem `minRating`, `maxMinimumOrderAmount` e `onlyWithPromotion`, que a doc não lista.
- `sort` aceita DISTANCE e DELIVERY_FEE, além dos três valores documentados.

## Observado no sandbox em 17/10 (São Carlos, conta de teste)

- **OAuth (descoberta do servidor de autorização).** Esperado: o `issuer` dos metadados em `https://id.ifood.com.br/.well-known/...` igual ao servidor indicado nos metadados do recurso protegido (RFC 8414 §3.3). Aconteceu: o recurso aponta para `https://id.ifood.com.br` e os metadados dizem `issuer: https://id.ifood.com.br/3p`. O SDK oficial `mcp` (Python, 2.3) recusa com "Authorization server metadata issuer mismatch". O back-end aceita a divergência só para esse host.
- **`searchOffers` / `browseOffers`, campos não documentados.** Os itens trazem `itemEan`, `itemSkuId` e `merchant.minimumOrderAmount`/`ratingCount`, que a doc não mostra. O EAN é o que permite reconhecer o mesmo produto em mercados diferentes, então vale documentar e garantir.
- **Facets.** Esperado (doc): `facets.itemTaxonomy` com `id`, `name` em português e `availability`. Aconteceu: `name` repete o código (`"LEITE_TRADICIONAL"`), vem `count` no lugar de `availability`, e chegam buckets não documentados (`maxDeliveryFee`, `maxDistanceKm`, `maxItemPrice`, `minRating`, `onlyOpen` com contagem de abertos e fechados, `merchantType`).
- **`browseOffers` com mercado fechado.** O `deliveryMethod` vem com `deliveryMethodId: ""`, `deliveryMethodMode: ""`, `fee: 0` e `estimatedMinutes {min: 0, max: 0}`. Isso parece frete grátis e entrega instantânea. Esperado: `null` ou ausência do campo.
- **`promotionalFee: 0` é ambíguo.** Há mercados com `fee: 8.49` e `promotionalFee: 0`, e outros com `fee: 9.49` e `promotionalFee: 9.49`. A doc não diz se 0 é frete grátis ou "sem promoção". **Confirmado em 18/10 montando carrinhos:** o Extra, com `fee: 6.49, promotionalFee: 0` na busca, cobrou frete R$ 0,00, e o Savegnago, com `promotionalFee: 10.99`, cobrou R$ 10,99. O `promotionalFee` é o frete efetivo. Num mercado fechado, porém, o `browseOffers` também devolve 0 com o método vazio, então o mesmo valor significa duas coisas.
- **`promotionalUnitPrice` sempre presente.** A doc diz que só aparece com promoção. Na prática vem igual ao `unitPrice` quando não há desconto.
- **Relevância com `sort: PRICE`.** `searchOffers("leite integral", resultKind: ITEM, merchantType: GROCERIES, sort: PRICE)` devolve "Leite de Coco Integral" e "Biscoito Integral Sabor Leite" entre os primeiros. Com ordenação por preço, derivados baratos ocupam a página. O agente precisa filtrar por conta própria ou usar `itemTaxonomy`.
- **`merchantType: GROCERIES` cruza verticais.** A busca acima devolve `facets.merchantType: ["GROCERIES", "LIQUORSTORE"]`.
- **Horário.** Às 22h40 de um sábado, 24 de 25 mercados de São Carlos estavam fechados (`onlyOpen` com `closed: 24, open: 1`). Como `createCart` recusa mercado fechado, a demo noturna depende do modo replay.
- **Notas como float32.** `reviewRating: 4.900000095367432`.
- **`listItemTaxonomy`.** Está na documentação, mas não aparece entre as tools expostas pelo servidor.
- **Carrinhos abandonados.** O fluxo cria carrinhos para comparar o total real com frete e cupom de até três soluções, e não existe tool para apagar os que não forem usados.
- **`createAddress`, envelope não documentado.** Esperado (doc): `matchConfidence` e `candidates` na raiz da resposta. Aconteceu: `{"success": true, "data": {"matchConfidence": ..., "candidates": [...]}, "agentHints": {...}}`. Um cliente que segue a doc acha zero candidatos. `listAddresses` não usa esse envelope, então o formato muda dentro do próprio User MCP.
- **`createAddress`, candidatos do Brasil inteiro.** "Rua São Sebastião 1455" devolveu 5 candidatos (São Carlos, Passo Fundo, Codó, Ribeirão Preto e Raposa), sem levar em conta a localização do usuário. A tool não aceita `userLocation` para priorizar, então o back-end ordena pela distância.
- **Endereços duplicados sem como apagar.** Cada confirmação do `createAddress` com um `candidateId` novo cria outro endereço, mesmo que seja igual a um já salvo (a idempotência vale só para o mesmo `candidateId`). Num teste ficaram duas cópias da mesma rua e número, e o User MCP não tem tool para apagar ou editar endereço.
- **`createAddress` muda o apelido.** Enviamos `alias: "Casa"`; a resposta do `createAddress` traz `"CASA"`, mas o `listAddresses` depois mostra `"Casa"`.
- **`createAddress`, apelido restrito sem aviso na doc.** A doc trata `alias` como texto livre ("Casa", "Trabalho"). Aconteceu: `alias: "Apartamento"` volta `INVALID_FIELD_VALUE: alias must be omitted, empty, "CASA", or "TRABALHO"`.
- **`createAddress`, erro opaco.** Confirmar o candidato de "Onovolab" voltou `ADDRESS_CREATION_FAILED: unexpected status: 400 Bad Request (code=ADR-2001)` com `retryable: true`, sem dizer o que estava errado.
- **`createCart` em mercado fechado.** Esperado (doc): erro `MERCHANT_CLOSED`. Aconteceu às 23h40 de sábado, com o Carrefour Bairro fechado: o carrinho foi criado e o `deliveryMethod` veio pré-preenchido com o primeiro horário agendado (segunda, 9h–11h). O `getCartDeliveryMethods` oferece horários para dois dias e ainda lista "Rápida" (25–35 min) sem horário, mesmo com a loja fechada. O comportamento é útil, mas contradiz a doc e o `isOpen: false` da busca.
- **`getCartDeliveryMethods`, IDs fora da doc.** Os IDs reais são `FAST_DELIVERY`, `DEFAULT` e `TAKEOUT` (a doc usa `EXPRESS` e `SCHEDULED`). Os horários vêm em hora local sem fuso (`"2026-10-19T09:00:00"`), enquanto a doc mostra UTC com `Z`.
- **`summary.benefitsTotalAmount` sem explicação.** Carrinho com 6 leites a R$ 3,82: subtotal R$ 22,93, taxa de serviço R$ 1,99 (que a doc mostra como 0), benefícios R$ 19,82 e total R$ 24,92. As contas fecham sem os benefícios (22,93 + 1,99 = 24,92), então não fica claro o que esse campo representa nem se ele deve ser descontado.
- **`searchOffers`, relevância com `sort: PRICE`, segundo caso.** "arroz branco" (`product`, GROCERIES) traz como primeiros resultados "Tempero Pronto Kisabor Tempera Fácil Arroz Branco 60g" e "Tempero para Arroz Branco Zilio 50g", à frente dos pacotes de arroz. Com ordenação por preço, qualquer produto barato que só cite os termos ocupa o topo da página.
- **`searchOffers` com `itemTaxonomy`, sem aviso de filtro vazio.** Com `itemTaxonomy: ["LEITE_CONDENSADO"]`, a busca "leite integral italac" devolve só leite condensado, sem nenhum `agentHints` indicando que a taxonomia restringiu os resultados. O agente precisa refazer a busca sem a taxonomia para perceber.
- **`createAddress`, `ADR-2001` persistente.** Em 18/10, na confirmação de "Rua Aquidaban, 1, Centro, São Carlos" (candidato `SINGLE`, bairro resolvido), o erro `ADDRESS_CREATION_FAILED (ADR-2001)` com `retryable: true` voltou em todas as variações: endereço digitado pelo nome do lugar ("Onovolab") e pelo endereço formatado, com apelido `TRABALHO`, `CASA` e sem apelido, e com e sem `complement`/`reference`. Na mesma conta, "Rua São Sebastião, 1455" tinha sido criado duas vezes sem problema. Um endereço totalmente diferente ("Avenida Trabalhador São-carlense, 400", ICMC) falha do mesmo jeito, então o problema é da conta, não do endereço: depois de 2 endereços salvos, nenhum outro é aceito. Pode ser um limite das contas de teste ou um bloqueio depois de cadastros repetidos. A mensagem não diz qual, e a tool sugere repetir algo que nunca dá certo.
