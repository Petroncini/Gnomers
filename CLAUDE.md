# Gnomers

Projeto do Hackathon SEMCOMP × iFood 2026 ("Vendendo com Agentes"): um app de listas de compras de mercado em que o agente monta o carrinho otimizado no iFood a partir de preferências estruturadas, mostra o preço final real (frete e melhor cupom) e usa o chat só para decisões pontuais. O plano de implementação está em `docs/plano-implementacao.md` e é a referência principal. O `HANDOFF.md` (local) traz o contexto do hackathon, do MCP e a ideia anterior de pedido em grupo, que foi substituída.

## MCP do iFood

O servidor `ifood` é configurado por pessoa com `python3 scripts/setup_ifood_mcp.py`, que lê as chaves do `.env` (escopo local, o secret fica no keychain). Para reautenticar, rode `/mcp` ou `claude mcp login ifood`.

- Sempre use contas de teste do portal. Pedidos delas não chegam ao restaurante.
- `listAddresses` é sempre a primeira chamada do fluxo de compra.
- Qualificadores ("barato", "rápido", "perto") vão em `refinements`, nunca em `query`.
- Chame `getMerchant` antes de apresentar um restaurante e `getItem` antes de adicionar ao carrinho.
- Um carrinho pertence a um único estabelecimento e a um único pagamento.
- Nunca invente IDs nem construa `nextPageToken`.
- No `trackOrder`, respeite `agentHints.retryMessage` e pare quando `pollingStop` for `true`.

## Como rodar

- Back-end (porta 8080, a mesma da redirect do OAuth): `cd backend && uv run uvicorn app.main:app --port 8080`. Login no iFood em `http://localhost:8080/auth/login` (o back-end tem o próprio OAuth, separado do `/mcp` do Claude Code).
- Front-end (porta 5173, proxy `/api` para a 8080): `cd frontend && npm install && npm run dev`.
- `GNOMERS_MCP_MODE`: `live` (padrão), `record` (grava cada chamada em `backend/fixtures/`), `replay` (só lê as gravações) e `fake` (simulador local em `app/mcp/fake.py`).
- Demo sem rede: `GNOMERS_MCP_MODE=fake GNOMERS_DB=sqlite:///$PWD/gnomers-fake.db uv run python -m app.seed`, depois suba o back-end com as mesmas variáveis.
- Testes: `cd backend && uv run pytest`. O solver é PuLP 2.9 com CBC; o PuLP 4 não traz solver e mudou a API.
- Sem `ANTHROPIC_API_KEY`, as tarefas do LLM caem nas heurísticas de `app/heuristics.py`.

## Trabalho em paralelo

Back-end e front-end são desenvolvidos por pessoas diferentes. Cada pasta tem o próprio `CLAUDE.md` com os detalhes.

- O contrato entre os dois é `contract/openapi.json`, gerado de `backend/app/schemas.py` (`uv run python -m app.contract`). O front gera os tipos dele com `npm run gen:api`.
- Mexa só na sua pasta. Mudança de contrato vai no mesmo commit que o `openapi.json` regenerado e é combinada com a outra pessoa antes, se renomear ou remover campo.
- `uv run pytest` (back) e `npm run build` (front) falham quando o contrato está fora de sincronia.

## Feedback do MCP (entregável 6)

Contexto completo das decisões até aqui em `HANDOFF.md` (local, fora do git).

Registre em `docs/mcp-feedback.md` toda inconsistência ou dor encontrada ao usar o MCP, com a tool, o que se esperava e o que aconteceu.
