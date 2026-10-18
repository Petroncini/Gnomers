from __future__ import annotations

from collections.abc import Iterator
from datetime import date

from sqlmodel import Session, SQLModel, create_engine, select

from app.config import settings
from app.models import User

engine = create_engine(settings.database_url, connect_args={"check_same_thread": False})

# Relógio da aplicação. O /admin/simulate-days avança este deslocamento para a demo.
_day_offset = 0


def today() -> date:
    from datetime import timedelta

    return date.today() + timedelta(days=_day_offset)


def advance_days(n: int) -> date:
    global _day_offset
    _day_offset += n
    return today()


def _add_missing_columns() -> None:
    """Migração mínima: acrescenta colunas novas (sempre anuláveis) em bancos já criados."""
    from sqlalchemy import inspect, text

    insp = inspect(engine)
    with engine.begin() as conn:
        for table in SQLModel.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            existing = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name not in existing:
                    conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {col.type.compile(engine.dialect)}'))


def init_db() -> None:
    SQLModel.metadata.create_all(engine)
    _add_missing_columns()
    with Session(engine) as s:
        if not s.exec(select(User)).first():
            s.add(User(id=1))
            s.commit()


def get_session() -> Iterator[Session]:
    with Session(engine) as s:
        yield s


def current_user(s: Session) -> User:
    """O app da demo tem um único usuário dono do token do iFood."""
    user = s.get(User, 1)
    assert user is not None
    return user
