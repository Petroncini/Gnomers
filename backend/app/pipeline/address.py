"""Endereço de entrega a partir da geolocalização do navegador (seção 7.1)."""

from __future__ import annotations

import math
from datetime import timedelta

from sqlmodel import Session, select

from app.mcp.ifood import Ifood
from app.models import Address, now

NEAR_METERS = 200
CACHE_MINUTES = 5


def distance_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _count(value) -> int:
    # o listAddresses devolve contagens como string
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


async def sync_addresses(s: Session, api: Ifood, user_id: int, force: bool = False) -> list[Address]:
    existing = s.exec(select(Address).where(Address.user_id == user_id)).all()
    fresh = existing and all(now() - _aware(a.last_synced) < timedelta(minutes=CACHE_MINUTES) for a in existing)
    if fresh and not force:
        return list(existing)
    remote = await api.list_addresses()  # sempre a primeira chamada do fluxo de compra
    by_id = {a.ifood_address_id: a for a in existing}
    seen = set()
    for r in remote:
        coords = r.get("coordinates") or {}
        if "latitude" not in coords:
            continue
        a = by_id.get(r["addressId"]) or Address(user_id=user_id, ifood_address_id=r["addressId"], lat=0, lng=0)
        a.alias = r.get("alias")
        a.formatted = r.get("formattedAddress", "")
        a.lat, a.lng = float(coords["latitude"]), float(coords["longitude"])
        a.groceries_orders = _count((r.get("topMerchantTypes") or {}).get("GROCERIES"))
        a.is_last_used = bool(r.get("isLastUsedAddress"))
        a.last_synced = now()
        s.add(a)
        seen.add(r["addressId"])
    for a in existing:
        if a.ifood_address_id not in seen:
            s.delete(a)
    s.commit()
    return list(s.exec(select(Address).where(Address.user_id == user_id)).all())


def _aware(dt):
    from datetime import timezone

    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def pick_address(addresses: list[Address], lat: float, lng: float,
                 accuracy_m: float | None = None) -> tuple[Address | None, list[dict]]:
    """Escolhe o endereço salvo mais próximo, se estiver dentro do raio.

    O raio é 200 m ou a margem de erro informada pelo navegador, o que for maior
    (até 1,5 km): localização por Wi-Fi costuma errar mais que 200 m.
    """
    radius = min(max(NEAR_METERS, accuracy_m or 0), 1500)
    scored = sorted(((distance_m(lat, lng, a.lat, a.lng), a) for a in addresses), key=lambda x: x[0])
    ranked = [{"id": a.id, "alias": a.alias, "formatted": a.formatted, "distance_m": round(d)} for d, a in scored]
    near = [(d, a) for d, a in scored if d <= radius]
    if not near:
        return None, ranked
    if len(near) == 1:
        return near[0][1], ranked
    # empate entre endereços próximos: quem mais recebeu mercado, depois o último usado
    near.sort(key=lambda x: (-x[1].groceries_orders, not x[1].is_last_used, x[0]))
    return near[0][1], ranked


async def create_address(s: Session, api: Ifood, user_id: int, text: str, *, candidate_id: str | None = None,
                         alias: str | None = None, complement: str | None = None,
                         neighborhood: str | None = None, near: tuple[float, float] | None = None) -> dict:
    """createAddress em duas etapas: geocodifica, a tela confirma e então salva."""
    # o iFood só aceita CASA, TRABALHO ou nenhum apelido
    alias = (alias or "").strip().upper() or None
    if alias not in (None, "CASA", "TRABALHO"):
        raise ValueError("o apelido do endereço precisa ser Casa, Trabalho ou nenhum")
    body = await api.create_address(text, candidate_id=candidate_id, alias=alias, complement=complement,
                                    neighborhood=neighborhood)
    if body.get("addressId"):
        await sync_addresses(s, api, user_id, force=True)
        addr = s.exec(select(Address).where(Address.ifood_address_id == body["addressId"])).first()
        return {"status": "created", "address_id": addr.id if addr else None}
    candidates = body.get("candidates", [])
    if near:
        # o geocodificador devolve a mesma rua em várias cidades; a mais próxima vem primeiro
        def dist(c: dict) -> float:
            co = c.get("coordinates") or {}
            if "latitude" not in co:
                return float("inf")
            return distance_m(near[0], near[1], co["latitude"], co["longitude"])
        candidates = sorted(candidates, key=dist)
    return {
        "status": "confirm",
        "matchConfidence": body.get("matchConfidence"),
        "candidates": [{"candidateId": c.get("candidateId"), "formattedAddress": c.get("formattedAddress"),
                        "distance_km": round(dist(c) / 1000, 1) if near and dist(c) != float("inf") else None,
                        "needsNeighborhood": not (c.get("location") or {}).get("neighborhood")} for c in candidates],
        "guidance": (body.get("agentHints") or {}).get("confirmAddressGuidance"),
    }
