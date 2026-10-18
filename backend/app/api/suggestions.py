"""Sugestões proativas mostradas na tela inicial."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlmodel import Session, select

from app.api import schemas as S
from app.core.db import get_session
from app.core.models import Suggestion

router = APIRouter()


@router.get("/suggestions", response_model=list[S.SuggestionView])
async def suggestions(s: Session = Depends(get_session)):
    rows = s.exec(select(Suggestion).where(Suggestion.dismissed == False).order_by(Suggestion.id.desc())).all()  # noqa: E712
    return [r.model_dump(mode="json") for r in rows]


@router.post("/suggestions/{sid}/dismiss", response_model=S.Ok)
async def dismiss(sid: int, s: Session = Depends(get_session)):
    sg = s.get(Suggestion, sid)
    if sg:
        sg.dismissed = True
        s.add(sg)
        s.commit()
    return {"ok": True}
