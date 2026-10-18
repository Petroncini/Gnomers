# Back-end do Gnomers

FastAPI + SQLModel (SQLite) + PuLP 2.9/CBC + SDK `mcp`. Visão geral no `CLAUDE.md` da raiz e em `docs/plano-implementacao.md`.

## Contrato com o front-end

- Todo formato de resposta está em `app/schemas.py`, e cada rota em `app/main.py` declara `response_model`. O FastAPI valida a saída: campo fora do schema é descartado, campo faltando vira erro 500.
- Mudou `schemas.py` ou uma rota? Rode `uv run python -m app.contract` para regenerar `contract/openapi.json`, commite junto e avise quem cuida do front (ele roda `npm run gen:api`).
- `tests/test_contract.py` falha se o contrato commitado estiver desatualizado e passa por todas as rotas no simulador validando as respostas. Rota nova entra nesse teste.
- Mudança que quebra o front (renomear ou remover campo, mudar tipo) é combinada antes. Acrescentar campo opcional é seguro.

## Rodar e testar

- `uv run pytest`: rápido, roda no simulador e sem LLM.
- `GNOMERS_MCP_MODE`: `live` (padrão), `record` (grava em `fixtures/`), `replay` (só lê `fixtures/`), `fake` (simulador em `app/mcp/fake.py`).
- `GNOMERS_ONLY_OPEN=0` inclui mercados fechados na busca. Com `deliver_at` no plano isso já acontece, porque o iFood agenda a entrega.
- O token do iFood fica em `gnomers.db` (fora do git). O login é em http://localhost:8080/auth/login.

## Mapa

- `app/mcp/`: `client.py` (sessão, OAuth, erros normalizados, envelope `{success, data}`), `recorder.py`, `ifood.py` (wrappers tipados; toda chamada passa por eles), `fake.py`.
- `app/pipeline/`: `address`, `discovery` (duas fases), `normalize`, `optimize` (MILP puro, sem I/O), `materialize` (carrinhos reais, cupom, entrega e horário, Pix), `decisions`, `runner` (orquestra e emite SSE).
- `app/rules.py` (regras tipadas e resolução por escopo), `app/llm.py` (tarefas com esquema via OpenRouter ou API da Anthropic, cai em `app/heuristics.py`), `app/actions.py` (cartões, chat, checkout, rastreio), `app/learning.py`, `app/scheduler.py`.

## Cuidados

- O sandbox do iFood diverge da documentação em vários pontos. Antes de confiar na doc, olhe uma resposta gravada em `fixtures/` e registre toda divergência em `docs/mcp-feedback.md`.
- Nunca invente IDs: todo UUID passado ao MCP vem de uma resposta anterior. `ifood.py` valida isso.
- `optimize.py` não chama nada externo; mantenha assim e cubra com teste em `tests/test_optimize.py`.
