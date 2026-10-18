"""Listas de compras e seus itens."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from app.ai import llm
from app.ai.heuristics import PARSER_VERSION
from app.api import schemas as S
from app.core.db import current_user, get_session, today
from app.core.models import ListItem, Plan, Rule, ShoppingList

router = APIRouter()


class ListIn(BaseModel):
    name: Optional[str] = None
    recurrence_days: Optional[int] = None
    next_due: Optional[date] = None
    max_merchants: Optional[int] = None
    split_min_savings: Optional[float] = None


def _list_view(s: Session, lst: ShoppingList, with_items: bool = False) -> dict:
    data = lst.model_dump(mode="json")
    items = s.exec(select(ListItem).where(ListItem.list_id == lst.id).order_by(ListItem.id)).all()
    data["item_count"] = len(items)
    last = s.exec(select(Plan).where(Plan.list_id == lst.id, Plan.status.in_(["ready", "ordered"]))
                  .order_by(Plan.id.desc())).first()
    data["last_plan_id"] = last.id if last and not (last.result or {}).get("seed") else None
    if with_items:
        data["items"] = [i.model_dump(mode="json") for i in items]
    return data


@router.get("/lists", response_model=list[S.ShoppingListView])
async def get_lists(s: Session = Depends(get_session)):
    user = current_user(s)
    lists = s.exec(select(ShoppingList).where(ShoppingList.owner_id == user.id).order_by(ShoppingList.id)).all()
    return [_list_view(s, lst) for lst in lists]


@router.post("/lists", response_model=S.ShoppingListView)
async def create_list(body: ListIn, s: Session = Depends(get_session)):
    user = current_user(s)
    lst = ShoppingList(owner_id=user.id, name=body.name or "Nova lista", recurrence_days=body.recurrence_days,
                       next_due=body.next_due or (today() if body.recurrence_days else None))
    s.add(lst)
    s.commit()
    s.refresh(lst)
    return _list_view(s, lst, True)


def get_list_or_404(s: Session, list_id: int) -> ShoppingList:
    lst = s.get(ShoppingList, list_id)
    if not lst:
        raise HTTPException(404, "lista não encontrada")
    return lst


@router.get("/lists/{list_id}", response_model=S.ShoppingListView)
async def get_list(list_id: int, s: Session = Depends(get_session)):
    return _list_view(s, get_list_or_404(s, list_id), True)


@router.patch("/lists/{list_id}", response_model=S.ShoppingListView)
async def patch_list(list_id: int, body: ListIn, s: Session = Depends(get_session)):
    lst = get_list_or_404(s, list_id)
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(lst, k, v)
    if body.recurrence_days and not lst.next_due:
        lst.next_due = today()
    s.add(lst)
    s.commit()
    return _list_view(s, lst, True)


@router.delete("/lists/{list_id}", response_model=S.Ok)
async def delete_list(list_id: int, s: Session = Depends(get_session)):
    lst = get_list_or_404(s, list_id)
    for it in s.exec(select(ListItem).where(ListItem.list_id == list_id)).all():
        s.delete(it)
    for r in s.exec(select(Rule).where(Rule.scope == str(list_id))).all():
        s.delete(r)
    s.delete(lst)
    s.commit()
    return {"ok": True}


class ItemIn(BaseModel):
    raw_text: Optional[str] = None
    quantity: Optional[float] = None
    category: Optional[str] = None
    brand: Optional[str] = None
    variety_terms: Optional[list[str]] = None
    pack_size: Optional[float] = None
    pack_unit: Optional[Literal["g", "ml", "un"]] = None
    query: Optional[str] = None
    intention: Optional[Literal["product", "product_exact"]] = None
    recurrence_days: Optional[int] = None


class ParseIn(BaseModel):
    text: str


@router.post("/items/parse", response_model=S.ParsedItem)
async def parse_preview(body: ParseIn):
    """Interpretação do texto enquanto o usuário digita (chips da tela da lista)."""
    return await llm.parse_item(body.text)


@router.post("/lists/{list_id}/items", response_model=S.ListItemView)
async def add_item(list_id: int, body: ItemIn, s: Session = Depends(get_session)):
    get_list_or_404(s, list_id)
    if not body.raw_text:
        raise HTTPException(422, "raw_text é obrigatório")
    parsed = await llm.parse_item(body.raw_text)
    # o que o usuário corrigiu nos chips vence a interpretação
    overrides = body.model_dump(exclude_unset=True, exclude={"raw_text"})
    item = ListItem(list_id=list_id, raw_text=body.raw_text, parser_version=PARSER_VERSION, **{**parsed, **overrides})
    if item.recurrence_days:
        item.next_due = today() + timedelta(days=item.recurrence_days)
    s.add(item)
    s.commit()
    s.refresh(item)
    return item


@router.patch("/lists/{list_id}/items/{item_id}", response_model=S.ListItemView)
async def patch_item(list_id: int, item_id: int, body: ItemIn, s: Session = Depends(get_session)):
    item = s.get(ListItem, item_id)
    if not item or item.list_id != list_id:
        raise HTTPException(404, "item não encontrado")
    changes = body.model_dump(exclude_unset=True)
    if "raw_text" in changes and changes["raw_text"] != item.raw_text:
        parsed = await llm.parse_item(changes["raw_text"])
        for k, v in parsed.items():
            setattr(item, k, v)
        item.taxonomy_id = None
        item.parser_version = PARSER_VERSION
        item.search_plan = None
    for k, v in changes.items():
        setattr(item, k, v)
    if "brand" in changes:
        item.intention = "product_exact" if item.brand else "product"
        item.search_plan = None
    s.add(item)
    s.commit()
    s.refresh(item)
    return item


@router.delete("/lists/{list_id}/items/{item_id}", response_model=S.Ok)
async def delete_item(list_id: int, item_id: int, s: Session = Depends(get_session)):
    item = s.get(ListItem, item_id)
    if not item or item.list_id != list_id:
        raise HTTPException(404, "item não encontrado")
    s.delete(item)
    s.commit()
    return {"ok": True}
