# Front-end do Gnomers

PWA em React + Vite + TypeScript. Visão geral do produto e do back-end no `CLAUDE.md` da raiz e em `docs/plano-implementacao.md` (seções 8 e 13 descrevem as telas).

## Rodar sem depender de ninguém

O back-end tem um simulador do iFood (modo `fake`): não precisa de login no iFood nem de chave de LLM.

```bash
# terminal 1, na raiz do repo
cd backend && uv sync
export GNOMERS_MCP_MODE=fake GNOMERS_DB=sqlite:///$PWD/gnomers-fake.db
uv run python -m app.seed      # lista "Compra do mês", regras e histórico de demo (apaga listas e regras)
uv run uvicorn app.main:app --port 8080

# terminal 2
cd frontend && npm install && npm run dev   # http://localhost:5173
```

Back-end em outra porta: `GNOMERS_API=http://localhost:8090 npm run dev`. O `/docs` do back-end (http://localhost:8080/docs) mostra todas as rotas com os schemas.

## Contrato com o back-end

- `contract/openapi.json` (na raiz) é o contrato. Ele é gerado pelo back-end a partir de `backend/app/schemas.py`.
- `src/api-types.ts` é gerado desse arquivo com `npm run gen:api`. **Nunca edite à mão.**
- `src/api.ts` só reexporta os tipos gerados com nomes curtos (`Plan`, `Cart`, `Decision`…) e tem o `fetch`. Não declare interfaces de resposta em componentes; importe de `../api`.
- `npm run build` roda `check:api` antes: falha se `api-types.ts` estiver desatualizado em relação ao contrato.
- Precisa de um campo que a API não devolve? Não invente no front. O campo tem de entrar em `backend/app/schemas.py` (o back descarta campos fora do schema). Combine com quem cuida do back; depois do `git pull`, rode `npm run gen:api`.
- O progresso do "Criar carrinho" chega por SSE em `GET /api/plans/{id}/events`: eventos `progress` com o JSON de `ProgressEvent`, `ping` a cada 15 s e `end` no fim. Depois do `end`, busque `GET /api/plans/{id}` para o resultado.

## Mapa

- `src/pages/`: `Home` (endereço, sugestões, listas), `ListPage` (itens com chips, horário de entrega, "Criar carrinho"), `Preferences` (regras e perfil, global ou por lista), `CartPage` (progresso, carrinhos por mercado, cartões de decisão, checkout), `OrderPage` (Pix e rastreio).
- `src/components/`: `ItemRow`/`Chips`, `RuleCard`/`RuleProposal`, `DecisionCard`, `ChatDrawer`, `SuggestionCard`, `AddressPicker`.
- `src/styles.css`: um arquivo só, com tokens de cor em `:root` e tema escuro por `prefers-color-scheme`. Sem biblioteca de UI.

## Convenções

- Texto da interface em português do Brasil. Valores em reais com `brl()` de `api.ts`.
- Layout pensado para celular primeiro (largura de 360 px sem rolagem horizontal).
- Estado durável fica no back-end. `localStorage` só para conveniências do navegador (endereço escolhido, horário de entrega), sempre dentro de `try/catch`.
- O total mostrado é sempre o `total` real do carrinho vindo da API; o front não recalcula preço.
- Erros da API chegam como `ApiError` com `message` em português pronto para mostrar.
