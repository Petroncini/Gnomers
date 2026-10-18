# Cesta

Projeto do time Gnomers para o Hackathon SEMCOMP × iFood 2026 ("Vendendo com Agentes").

O Cesta é um app de listas de compras de mercado. O usuário mantém listas ("2 leite italac integral", "500g café moído") e preferências (marcas em ordem, marcas bloqueadas, frete máximo). Ao tocar em "Montar carrinho", o agente busca os produtos no iFood pelo MCP, escolhe o que comprar e em quais mercados, monta os carrinhos de verdade e mostra o **total real**, já com frete e o melhor cupom. O chat fica para decisões pontuais e para pedidos abertos sobre o carrinho pronto.

## Mapa do código

```
contract/openapi.json        contrato entre back e front, gerado de backend/app/api/schemas.py
backend/
  app/
    main.py                  monta o FastAPI: ciclo de vida, erros e routers
    contract.py, seed.py     geram o contrato e os dados da demonstração
    api/                     um router por recurso (listas, regras, planos, ...) e os schemas
    pipeline/                "Montar carrinho", uma etapa por arquivo (ver abaixo)
    cart/                    o carrinho depois de montado: cartões, trocas, checkout, Pix, rastreio
    ai/                      tarefas do LLM, heurísticas de reserva, chat e agente com tools
    preferences/             regras tipadas com escopo, aprendizado de marcas e periodicidade
    proactive/               sugestões por promoção e job diário
    mcp/                     cliente do MCP, wrappers com as regras de uso, gravação e simulador
    core/                    configuração, banco, tabelas e métricas
  tests/                     testes (rodam no simulador, sem rede e sem LLM)
  evals/                     avaliação do juiz e da economia (resultados em evals/results/)
  fixtures/                  respostas reais do iFood gravadas, usadas no modo replay
frontend/src/
  pages/                     uma tela por arquivo (início, lista, item, carrinho, pedido, ...)
  components/                cartões e linhas reutilizados entre telas
  api.ts, api-types.ts       cliente HTTP e tipos gerados do contrato
```

As etapas do pipeline, na ordem em que rodam (`app/pipeline/runner.py` orquestra e emite o progresso):

| Etapa | Arquivo | Usa |
|---|---|---|
| 1. Endereço (GPS cruzado com os endereços salvos) | `address.py` | iFood |
| 2. Busca no catálogo, com as buscas planejadas pelo LLM | `discovery.py` | iFood |
| 3. Juiz (exato, substituto ou não serve) e normalização | `normalize.py` | LLM |
| 4. Otimização (frete, pedido mínimo, dividir ou não) | `optimize.py` | solver |
| 5. Carrinhos reais (entrega, cupom, total) | `materialize.py` | iFood |
| 6. Cartões de decisão | `decisions.py` | — |

## Como rodar

Precisa de Python 3.12+ com [uv](https://docs.astral.sh/uv/) e de Node com npm.

**1. Configuração.** Na raiz do repositório, copie o exemplo e preencha:

```bash
cp .env.example .env
```

- `IFOOD_CLIENT_ID` e `IFOOD_CLIENT_SECRET`: credenciais do agente no portal do iFood. A redirect cadastrada para ele precisa ser `http://localhost:8080/callback`.
- `OPENROUTER_API_KEY` (opcional): sem ela, as tarefas do LLM usam as heurísticas de `app/ai/heuristics.py` e o app continua funcionando, com menos precisão.

**2. Back-end** (terminal 1):

```bash
cd backend
uv sync
uv run python -m app.seed                  # opcional, ver abaixo
uv run uvicorn app.main:app --port 8080
```

O seed cria a lista "Compra do mês" com 15 itens, uma preferência de marca e um histórico de compras fictício. O histórico serve para mostrar as preferências aprendidas e as sugestões proativas sem precisar fazer várias compras antes. Produtos, preços, frete e carrinhos continuam vindo do iFood. Ele apaga as listas e regras que existirem no banco.

**3. Front-end** (terminal 2):

```bash
cd frontend
npm install
npm run dev                                # http://localhost:5173
```

**4. Login.** Abra http://localhost:5173 e toque em "Entrar no iFood" (ou acesse http://localhost:8080/auth/login). Use uma conta de teste do portal: pedidos dela não chegam ao estabelecimento.

Para rodar sem credenciais, o back-end tem um simulador do iFood: suba os dois comandos do passo 2 com `GNOMERS_MCP_MODE=fake` e `GNOMERS_DB=sqlite:///$PWD/gnomers-fake.db` exportados, para não misturar com o banco real.
