"""Modelo de dados (seção 3 do plano)."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Optional

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


def now() -> datetime:
    return datetime.now(timezone.utc)


def _json(default: Any = None) -> Any:
    factory = (lambda: list(default)) if isinstance(default, list) else (lambda: dict(default or {}))
    return Field(default_factory=factory, sa_column=Column(JSON))


class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = "Você"
    quality_profile: float = 0.5  # sem uso; a coluna fica para bancos já criados (é NOT NULL)
    max_merchants: int = 2
    split_min_savings: Optional[float] = 10.0
    ifood_token: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    imported_history: bool = False


class Address(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id")
    ifood_address_id: str
    alias: Optional[str] = None
    formatted: str = ""
    lat: float
    lng: float
    groceries_orders: int = 0
    is_last_used: bool = False
    last_synced: datetime = Field(default_factory=now)


class ShoppingList(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    owner_id: int = Field(foreign_key="user.id")
    name: str
    recurrence_days: Optional[int] = None
    next_due: Optional[date] = None
    shared: bool = False
    # sobrescritas do perfil do usuário só para esta lista
    quality_profile: Optional[float] = None  # sem uso
    max_merchants: Optional[int] = None
    split_min_savings: Optional[float] = None
    last_total: Optional[float] = None


class ListMember(SQLModel, table=True):
    list_id: int = Field(foreign_key="shoppinglist.id", primary_key=True)
    user_id: int = Field(foreign_key="user.id", primary_key=True)


class ListItem(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    list_id: int = Field(foreign_key="shoppinglist.id", index=True)
    raw_text: str
    quantity: float = 1
    category: str = ""
    brand: Optional[str] = None
    variety_terms: list = _json([])
    pack_size: Optional[float] = None
    pack_unit: Optional[str] = None  # "un", "g", "ml"
    query: str = ""
    intention: str = "product"  # "product" | "product_exact"
    taxonomy_id: Optional[str] = None
    recurrence_days: Optional[int] = None
    next_due: Optional[date] = None
    requested_by: Optional[int] = None
    parser_version: Optional[int] = None  # itens de um parser antigo são reinterpretados no próximo carrinho
    # buscas do item no catálogo ({specific, broad, alternatives, must_have}); refeitas quando o texto muda
    search_plan: Optional[dict] = Field(default=None, sa_column=Column(JSON))


class Rule(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    scope: str = "global"  # "global" ou o id da lista como texto
    category: Optional[str] = None
    kind: str
    payload: dict = _json({})
    hardness: str = "soft"
    source: str = "user"  # "user" | "chat" | "learned"
    confidence: float = 1.0
    active: bool = True
    created_at: datetime = Field(default_factory=now)


class Product(SQLModel, table=True):
    """Cache da normalização; a chave é o itemId do iFood (que é por mercado)."""

    ifood_item_id: str = Field(primary_key=True)
    merchant_id: str = Field(index=True)
    ean: Optional[str] = Field(default=None, index=True)
    name: str
    brand: Optional[str] = None
    category: str = ""
    variety_terms: list = _json([])
    pack_size: Optional[float] = None
    pack_unit: Optional[str] = None
    quality_tier: int = 2  # sem uso; a coluna fica para bancos já criados
    matched_category: Optional[str] = None  # categoria contra a qual o casamento foi avaliado
    matched: bool = True
    parsed_at: datetime = Field(default_factory=now)


class Judgment(SQLModel, table=True):
    """Veredito do juiz para (pedido, produto): exato, substituto ou não serve, com o motivo."""

    key: str = Field(primary_key=True)  # "<hash do pedido>|<itemId do iFood>"
    verdict: str
    reason: str = ""
    created_at: datetime = Field(default_factory=now)


class Plan(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id")
    list_id: int = Field(foreign_key="shoppinglist.id")
    status: str = "pending"  # pending | running | ready | failed | ordered
    address_id: Optional[int] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    deliver_at: Optional[str] = None  # "2026-10-19T10:00", horário local; None = o quanto antes
    # escolhas de um carrinho anterior: {"<item_id>": {"size": 250.0} | {"removed": true}}
    pins: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    result: dict = _json({})
    error: Optional[str] = None
    created_at: datetime = Field(default_factory=now)


class Order(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    plan_id: int = Field(foreign_key="plan.id")
    merchant_id: str
    merchant_name: str = ""
    ifood_cart_id: str
    ifood_order_id: Optional[str] = None
    total: float = 0
    status: str = "created"
    pix: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    tracking: dict = _json({})
    created_at: datetime = Field(default_factory=now)


class OrderLine(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    order_id: int = Field(foreign_key="order.id")
    list_item_id: int
    ifood_item_id: str
    name: str = ""
    brand: Optional[str] = None
    unit_price: float
    qty: int
    was_substitute: bool = False
    user_action: Optional[str] = None


class Signal(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    category: str
    brand: Optional[str] = None
    kind: str  # chosen | accepted | swapped_to | swapped_from | rejected
    unit_price: Optional[float] = None
    list_item_id: Optional[int] = None
    created_at: datetime = Field(default_factory=now)


class Suggestion(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id")
    list_id: Optional[int] = None
    list_item_id: Optional[int] = None
    kind: str  # "promo" | "list_promo" | "list_due"
    text: str
    data: dict = _json({})
    dismissed: bool = False
    created_at: datetime = Field(default_factory=now)
