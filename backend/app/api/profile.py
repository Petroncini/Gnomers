"""Perfil do usuário e importação do histórico de pedidos do iFood."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

from app.api import schemas as S
from app.core.db import current_user, get_session, today
from app.mcp.ifood import ifood
from app.pipeline import address as address_mod
from app.preferences import onboarding

router = APIRouter()


class ProfileIn(BaseModel):
    max_merchants: Optional[int] = None
    split_min_savings: Optional[float] = None
    name: Optional[str] = None


@router.get("/me", response_model=S.UserView)
async def me(s: Session = Depends(get_session)):
    u = current_user(s)
    return u.model_dump(exclude={"ifood_token"}) | {"today": today().isoformat()}


@router.patch("/me", response_model=S.UserView)
async def patch_me(body: ProfileIn, s: Session = Depends(get_session)):
    u = current_user(s)
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(u, k, v)
    s.add(u)
    s.commit()
    return u.model_dump(exclude={"ifood_token"}) | {"today": today().isoformat()}


@router.post("/onboarding/import", response_model=S.ImportResult)
async def import_history(s: Session = Depends(get_session)):
    user = current_user(s)
    addrs = await address_mod.sync_addresses(s, ifood(), user.id)
    if not addrs:
        raise HTTPException(409, "cadastre um endereço antes de importar o histórico")
    a = next((x for x in addrs if x.is_last_used), addrs[0])
    return await onboarding.import_history(s, ifood(), user, a.lat, a.lng)
