"""Endereço de entrega: localização do aparelho cruzada com os endereços salvos no iFood."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlmodel import Session

from app.api import schemas as S
from app.core.db import current_user, get_session
from app.mcp.ifood import ifood
from app.pipeline import address as address_mod

router = APIRouter()


class LocationIn(BaseModel):
    lat: float
    lng: float
    accuracy: Optional[float] = None


@router.post("/location", response_model=S.LocationResult)
async def location(body: LocationIn, s: Session = Depends(get_session)):
    user = current_user(s)
    addrs = await address_mod.sync_addresses(s, ifood(), user.id)
    chosen, ranked = address_mod.pick_address(addrs, body.lat, body.lng, body.accuracy)
    # sem endereço perto: a tela deixa escolher entre os salvos; só pede cadastro se não houver nenhum
    return {"address": chosen.model_dump(mode="json") if chosen else None, "nearby": ranked,
            "needs_choice": chosen is None and bool(addrs), "needs_address": not addrs}


@router.get("/addresses", response_model=list[S.AddressView])
async def addresses(s: Session = Depends(get_session)):
    user = current_user(s)
    return [a.model_dump(mode="json") for a in await address_mod.sync_addresses(s, ifood(), user.id)]


class AddressIn(BaseModel):
    address: str
    candidate_id: Optional[str] = None
    alias: Optional[str] = None
    complement: Optional[str] = None
    neighborhood: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None


@router.post("/addresses", response_model=S.CreateAddressResult)
async def create_address(body: AddressIn, s: Session = Depends(get_session)):
    user = current_user(s)
    return await address_mod.create_address(s, ifood(), user.id, body.address, candidate_id=body.candidate_id,
                                            alias=body.alias, complement=body.complement,
                                            neighborhood=body.neighborhood,
                                            near=(body.lat, body.lng) if body.lat is not None and body.lng is not None else None)
