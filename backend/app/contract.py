"""Gera o contrato da API em contract/openapi.json.

Uso: `uv run python -m app.contract` depois de mudar `app/api/schemas.py` ou uma rota em `app/api/`.
O teste `tests/test_contract.py` falha se o arquivo commitado estiver desatualizado,
e o front gera os tipos TypeScript a partir dele (`npm run gen:api`).
"""

from __future__ import annotations

import json

from app.core.config import ROOT

CONTRACT = ROOT / "contract" / "openapi.json"


def render() -> str:
    from app.main import app

    return json.dumps(app.openapi(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


if __name__ == "__main__":
    CONTRACT.parent.mkdir(exist_ok=True)
    CONTRACT.write_text(render(), encoding="utf-8")
    print(f"contrato escrito em {CONTRACT.relative_to(ROOT)}")
