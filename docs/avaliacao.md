# Avaliação

Medições feitas em 18/10/2026 com preços e produtos reais do iFood (sandbox, São Carlos), gravados em `backend/fixtures/`. Os scripts estão em `backend/evals/` e os resultados completos em `backend/evals/results/`.

## 1. O juiz acerta o que serve?

O juiz é a chamada ao LLM que decide, para cada produto encontrado, se ele serve para o pedido: **exato**, **substituto** (o mesmo produto sem alguma característica pedida) ou **não serve** (outro produto, ou o mesmo alimento em outra forma: em pó, condensado, pronto, solúvel, cápsula, sachê individual).

**Conjunto:** 275 pares (pedido, produto) marcados à mão antes de rodar o juiz. São 6 pedidos ("1 kilo de arroz branco", "1 kilo de feijão cru em grão", "1 granola sem açucar (>500g)", "500g café moído de filtro", "2 leite integral" e "1 leite em pó integral 400g") contra os produtos que o iFood devolveu nas buscas deles. O resultado é 153 exatos, 26 substitutos e 96 que não servem. Marca e tamanho não contam. 6 casos ambíguos ficaram de fora, como uma granola "Keto Low Carb" que não diz se tem açúcar.

O pedido de leite em pó está lá de propósito: nele o leite em pó é exato e o leite UHT não serve, o contrário do pedido de leite integral.

| | Juiz (Claude Haiku 5.5) | Regras fixas (versão anterior, hoje plano B) |
|---|---|---|
| Acerto nas 3 classes | 84,4% (232/275) | 84,4% (232/275) |
| **Aceitou o que não serve** | **6** | **35** |
| **Descartou um exato** | **0** | **5** |
| Custo e tempo | 10 chamadas, 16 s, US$ 0,008 | nenhum |

O acerto total empata, mas os erros não são iguais:
- **As regras erram para o lado perigoso.** Aceitam como exato 20 leites UHT no pedido de leite em pó; 6 itens como "café moído" (cápsula, café solúvel, café pronto em garrafa, sachê drip e até filtro de papel); e 5 feijões prontos ou enlatados como "feijão cru". Esses produtos entrariam no carrinho como se fossem o que o usuário pediu.
- **O juiz erra para o lado cauteloso.** 33 dos 43 erros dele são exatos marcados como substituto: feijão preto ou fradinho quando o pedido era só "feijão" (ele supõe carioca), e "Café Pilão Tradicional", cujo nome não diz "moído". Um substituto continua elegível e só recebe uma penalidade no solver.
- **Os 6 aceites indevidos do juiz** são 5 sachês de café "drip" e 1 feijão cozido. No app, os sachês ainda passam pelo filtro de tamanho (embalagem menor que 10% do pedido sai quando há outra opção).

O prompt não foi ajustado com base nesse conjunto, para os números não ficarem otimistas.

## 2. Quanto o Cesta economiza?

**Conjunto:** os 6 itens acima, com todas as combinações de 3 a 6 itens (42 listas), sobre 403 ofertas reais de 19 mercados. O código é o mesmo do app (juiz, filtros e solver), sem chamar o iFood. Os totais são estimados (produtos mais frete) e calculados do mesmo jeito para todas as estratégias, porque cupom e taxa de serviço só aparecem no carrinho real. O perfil é "economia" (só preço).

O Cesta é comparado com três pessoas de referência. Elas já sabem quais produtos servem (usam os mesmos que o juiz aprovou, exatos antes de substitutos) e levam o mais barato. Se a compra não chega ao pedido mínimo do mercado, a pessoa completa até ele.

| A pessoa | Economia média do Cesta | Maior economia | Cesta mais barato ou igual |
|---|---|---|---|
| compra tudo no mercado mais perto | R$ 11,46 (19,0%) | R$ 29,98 | 40 de 42 listas |
| compra tudo no mercado que tem mais itens | R$ 13,79 (18,9%) | R$ 118,77 | 41 de 42 |
| leva cada item onde ele é mais barato | R$ 67,41 (54,0%) | R$ 118,33 | 41 de 42 |

- **A economia vem de escolher o mercado considerando o pedido mínimo e o frete juntos.** O Cesta dividiu a compra em só 1 das 42 listas.
- **Comprar cada item onde é mais barato é a pior estratégia:** cada mercado cobra frete e exige o próprio pedido mínimo.
- **Nas 2 listas em que a pessoa sai mais barata,** a diferença é de R$ 0,07 e R$ 0,85. Ela vem da regra de completar o mínimo: a pessoa paga exatamente o mínimo (R$ 60) sem levar nada a mais, enquanto o Cesta chega a ele com produtos de verdade. A regra favorece a pessoa.

**Limites:**
- os preços foram gravados em horários diferentes de 17 e 18/10;
- as 42 listas são combinações dos mesmos 6 itens, não listas independentes;
- o frete de um mercado fechado vem zerado na busca, então foi usado o primeiro registro com método de entrega.

## 3. Robustez

| Problema | Como ficou | Teste |
|---|---|---|
| Se o servidor reiniciar no meio da montagem, o plano fica "montando" para sempre | ao iniciar, planos interrompidos viram falha com "tente de novo" | `test_interrupted_plans_fail_on_startup` |
| Duas alterações no mesmo carrinho ao mesmo tempo (um cartão e o agente do chat): a segunda apagava a primeira | uma alteração por vez em cada plano, relendo o plano do banco | `test_concurrent_changes_to_a_plan_are_not_lost`, mais um teste que mostra a perda sem a trava |
| O sandbox bloqueia o token por alguns minutos depois de ~60 chamadas em 1 a 2 min, e responde `INTERNAL` | no máximo 30 chamadas ao iFood por minuto (`GNOMERS_MCP_CALLS_PER_MIN`); cache, simulador e reprodução não contam | `test_rate_limiter_waits_for_the_window` |

## 4. Robustez do agente do chat

**Protegido:**
- no máximo 6 chamadas ao iFood e 8 rodadas por mensagem; na última rodada as tools ficam desligadas, então sempre há resposta;
- a troca só aceita produtos que vieram de uma busca na mesma conversa, e os wrappers recusam ID que não seja UUID de uma resposta do iFood;
- troca só dentro dos mercados do carrinho; o agente não paga nem fecha pedido;
- falhas do iFood e argumentos inválidos voltam ao modelo como resultado da tool, e ele explica ao usuário;
- o chat roda com a trava do plano, então não sobrescreve um clique num cartão;
- sem LLM, ou se ele falhar antes de agir, o chat volta ao fluxo sem tools.

**Corrigido:** um produto achado pelo agente entrava no carrinho com tamanho 1. O recálculo de "dá para pagar menos" via "café 500 g" como 500 pacotes, deixava o item de fora e podia mostrar uma economia falsa. Agora o tamanho vem do nome do produto, como no pipeline (testes `test_agent_searches_and_swaps_in_the_real_cart` e `test_product_found_by_the_agent_gets_its_size_from_the_name`).

**Pendente:**
1. **Falha no meio da conversa.** Se o agente troca um item e o LLM falha na rodada seguinte, o chat responde pelo fluxo sem tools e não conta que o carrinho mudou. O certo é responder com os passos já feitos.
2. **Sem tempo máximo por mensagem.** Cada chamada ao LLM pode levar até 90 s, e a trava do plano bloqueia os cartões enquanto isso. Falta um limite (uns 45 s).
3. **Quantidade sem teto.** O agente pode pedir qualquer número de embalagens. Falta limitar, por exemplo a 3 vezes o que a lista pede.

**Risco aceito:** os nomes de produtos do iFood vão para o modelo, então um nome malicioso poderia tentar instruí-lo. O pior resultado possível é uma troca dentro do carrinho, que aparece nos passos e passa pela confirmação antes do pagamento.

## Como reproduzir

```bash
cd backend
uv run python evals/judge_eval.py           # juiz contra regras (LLM do .env; --rules para só as regras)
uv run python evals/savings_eval.py         # economia sobre os preços gravados
uv run pytest tests/test_robustness.py tests/test_agent.py
```
