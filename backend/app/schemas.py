"""Contrato da API entre back-end e front-end.

Todo formato de resposta das rotas está aqui. O FastAPI valida a saída contra estes
modelos, e o `contract/openapi.json` gerado a partir deles é a fonte dos tipos
TypeScript do front (`npm run gen:api`). Mudou um campo aqui? Rode
`uv run python -m app.contract` e avise quem cuida do front.

Campos fora do modelo são descartados na resposta: para o front ver um campo novo,
ele precisa existir aqui.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

Unit = Literal["g", "ml", "un"]
PlanStatus = Literal["pending", "running", "ready", "failed", "ordered", "needs_address"]
RuleKind = Literal["dietary", "variety", "brand_chain", "brand_block", "merchant", "delivery", "quantity", "product",
                  "note"]
DecisionType = Literal["missing", "brand_chain_info", "brand_substitution", "brand_choice", "variety", "quantity", "split",
                       "replan"]


class Ok(BaseModel):
    ok: bool = True


# --------------------------------------------------------------- auth e perfil

class AuthStatus(BaseModel):
    mode: Literal["live", "record", "replay", "fake"]
    connected: bool
    has_token: bool
    llm: bool
    imported_history: bool


class UserView(BaseModel):
    id: int
    name: str
    quality_profile: float
    max_merchants: int
    split_min_savings: Optional[float]
    imported_history: bool
    today: date  # "hoje" do app, que o /admin/simulate-days avança


class ImportResult(BaseModel):
    items: int
    signals: int
    rules: int
    profile: Optional[str]


# ------------------------------------------------------------------ listas

class ParsedItem(BaseModel):
    query: str
    intention: Literal["product", "product_exact"]
    category: str
    brand: Optional[str]
    variety_terms: list[str]
    pack_size: Optional[float]
    pack_unit: Optional[Unit]
    quantity: float


class ListItemView(BaseModel):
    id: int
    list_id: int
    raw_text: str
    quantity: float
    category: str
    brand: Optional[str]
    variety_terms: list[str]
    pack_size: Optional[float]
    pack_unit: Optional[Unit]
    query: str
    intention: Literal["product", "product_exact"]
    taxonomy_id: Optional[str]
    recurrence_days: Optional[int]
    next_due: Optional[date]


class ShoppingListView(BaseModel):
    id: int
    name: str
    recurrence_days: Optional[int]
    next_due: Optional[date]
    last_total: Optional[float]
    quality_profile: Optional[float]
    max_merchants: Optional[int]
    split_min_savings: Optional[float]
    item_count: int
    last_plan_id: Optional[int]
    items: Optional[list[ListItemView]] = None  # só no GET /lists/{id} e nas mutações


# ------------------------------------------------------------------ regras

class RuleView(BaseModel):
    id: int
    scope: str  # "global" ou o id da lista
    category: Optional[str]
    kind: RuleKind
    payload: dict[str, Any]
    hardness: Literal["hard", "soft"]
    source: Literal["user", "chat", "learned"]
    confidence: float
    active: bool
    created_at: datetime
    text: str  # frase pronta para a tela


class ProposedRule(BaseModel):
    kind: RuleKind
    category: Optional[str]
    hardness: Literal["hard", "soft"]
    payload: dict[str, Any]
    text: str
    scope_options: list[str]


# --------------------------------------------------------------- endereços

class AddressView(BaseModel):
    id: int
    alias: Optional[str]
    formatted: str
    lat: float
    lng: float
    is_last_used: bool


class NearbyAddress(BaseModel):
    id: int
    alias: Optional[str]
    formatted: str
    distance_m: int


class LocationResult(BaseModel):
    address: Optional[AddressView]
    nearby: list[NearbyAddress]
    needs_choice: bool  # há endereços salvos, mas nenhum perto: a tela deixa escolher
    needs_address: bool  # nenhum endereço salvo: a tela pede cadastro


class AddressCandidate(BaseModel):
    candidateId: str
    formattedAddress: Optional[str]
    needsNeighborhood: bool
    distance_km: Optional[float]


class CreateAddressResult(BaseModel):
    status: Literal["created", "confirm"]
    address_id: Optional[int] = None
    matchConfidence: Optional[str] = None
    candidates: list[AddressCandidate] = []
    guidance: Optional[str] = None


# ------------------------------------------------------------------- plano

class PlanCreated(BaseModel):
    id: int
    status: PlanStatus


class McpErrorView(BaseModel):
    tool: str
    code: str
    message: str
    retryable: bool
    userMessage: str


class CartLine(BaseModel):
    item_id: int  # ListItem.id
    sku_id: str  # itemId do iFood
    cart_item_id: Optional[str]
    name: str
    brand: Optional[str]
    packs: int
    quantity: int
    unit_price: float
    image: Optional[str]
    user_action: Optional[str] = None


class CartSummary(BaseModel):
    subtotal: float
    delivery: float
    fees: float
    benefits: float
    voucher: float
    total: float


class VoucherView(BaseModel):
    code: str
    description: Optional[str]
    discount: float


class EtaView(BaseModel):
    min: int
    max: int


class SlotView(BaseModel):
    # "from" é palavra reservada em Python; no JSON o campo é "from"
    model_config = ConfigDict(populate_by_name=True)
    from_: str = Field(alias="from")
    to: str


class DeliveryView(BaseModel):
    id: str
    name: Optional[str]
    fee: Optional[float]
    eta: Optional[EtaView]
    slot: Optional[SlotView] = None


class PaymentView(BaseModel):
    kind: str
    paymentMethodId: str


class CartWarning(BaseModel):
    code: Optional[str] = None
    message: Optional[str] = None
    userFriendlyMessage: Optional[str] = None


class Cart(BaseModel):
    merchant_id: str
    merchant_name: str
    cart_id: str
    lines: list[CartLine]
    summary: CartSummary
    minimum_order: Optional[float]
    voucher: Optional[VoucherView]
    delivery: Optional[DeliveryView]
    delivery_problem: Optional[str] = None
    payment: Optional[PaymentView]
    warning: Optional[CartWarning]
    blocking: bool  # true: este carrinho não pode ir para o checkout
    deeplink: Optional[str]


class CartError(BaseModel):
    merchant_id: str
    merchant_name: str
    error: Union[McpErrorView, str]


class Solution(BaseModel):
    index: int
    merchants: list[str]
    carts: list[Cart]
    errors: list[CartError]
    ok: bool
    total: float  # soma dos totalAmount reais
    estimated_total: float  # estimativa do solver, antes de montar os carrinhos
    missing: list[int]  # ListItem.id que ficaram de fora


class Alternative(BaseModel):
    sku_id: str
    merchant_id: str
    name: str
    brand: Optional[str]
    price: float
    packs: int
    reasons: list[str]


class DecisionChoice(BaseModel):
    sku_id: str
    name: str
    brand: Optional[str]
    price: float
    packs: int
    merchant_id: str
    merchant_name: str


class Decision(BaseModel):
    id: int
    type: DecisionType
    item_id: Optional[int]
    category: Optional[str]
    title: str
    text: str
    question: bool  # true: precisa de resposta; false: só informa
    status: Literal["open", "info", "resolved"]
    resolution: Optional[str] = None
    chosen: Optional[DecisionChoice] = None
    alternatives: list[Alternative] = []
    saving: Optional[float] = None


class PlanAddress(BaseModel):
    id: int
    alias: Optional[str]
    formatted: str


class PlanItem(BaseModel):
    id: int
    raw_text: str
    category: str
    query: str
    required: float
    unit: Literal["g", "ml", "un", "pack"]


class MerchantSummary(BaseModel):
    name: str
    delivery_fee: float
    minimum_order: float
    distance_km: Optional[float]
    eta_max: Optional[int]
    logo: Optional[str]
    coverage: int


class MissingItem(BaseModel):
    item_id: int
    raw_text: str


class PlanError(BaseModel):
    code: str
    message: str


class PlanResult(BaseModel):
    """Preenchido quando o plano fica pronto; em falha, só `error`."""

    address: Optional[PlanAddress] = None
    items: list[PlanItem] = []
    merchants: dict[str, MerchantSummary] = {}
    chosen: Optional[Solution] = None
    alternatives: list[Solution] = []
    single_total: Optional[float] = None
    savings_vs_single: Optional[float] = None
    missing: list[MissingItem] = []
    decisions: list[Decision] = []
    only_open: Optional[bool] = None
    deliver_at: Optional[str] = None
    needs_address: Optional[bool] = None
    error: Optional[PlanError] = None


class OrderView(BaseModel):
    id: int
    plan_id: int
    merchant_id: str
    merchant_name: str
    ifood_order_id: Optional[str]
    total: float
    status: str
    pix: Optional[dict[str, str]]  # {"code": "<pix copia e cola>"}
    created_at: datetime


class PlanView(BaseModel):
    id: int
    list_id: int
    status: PlanStatus
    error: Optional[str]
    address_id: Optional[int]
    deliver_at: Optional[str]
    created_at: datetime
    result: PlanResult
    orders: list[OrderView] = []  # só no GET /plans/{id}


class ProgressEvent(BaseModel):
    """Cada evento `progress` do SSE em /plans/{id}/events. O stream termina com o evento `end`."""

    stage: str  # address, discovery, merchants, gaps, brands, normalize, optimize, carts, cart, done, error
    t: float
    message: Optional[str] = None
    code: Optional[str] = None
    needs_address: Optional[bool] = None


class DecisionResponse(BaseModel):
    plan: PlanView
    rule: Optional[RuleView]


class ChatResponse(BaseModel):
    reply: str
    proposed_rule: Optional[ProposedRule]
    plan: PlanView
    steps: list[str] = []  # o que o agente fez para responder ("Busquei “café extraforte” no Extra")


class CheckoutOrder(BaseModel):
    id: int
    ifood_order_id: Optional[str]
    merchant_name: str
    total: float
    pix: Optional[str]


class TotalChange(BaseModel):
    merchant_name: str
    before: float
    after: float


class CheckoutResponse(BaseModel):
    status: Literal["ordered", "changed"]  # "changed": o total mudou; reenviar com accept_changes
    orders: list[CheckoutOrder] = []
    changes: list[TotalChange] = []
    plan: PlanView


class StatusView(BaseModel):
    code: str
    description: str


class TrackingView(BaseModel):
    order_id: int
    status: Optional[StatusView]
    history: list[StatusView]
    retry_message: Optional[str]
    stopped: bool
    stop_message: Optional[str]
    next_poll_at: Optional[float]
    retry_after_s: Optional[int]  # quando chamar de novo; vem do retryMessage do iFood
    cached: bool


# ------------------------------------------------------------ proatividade

class SuggestionView(BaseModel):
    # promo: um item em promoção (data.action: "advance" adiantar | "stock" estocar)
    # list_promo: dois ou mais itens da lista em promoção (data.savings, data.items)
    # list_due: a lista vence nos próximos dias
    id: int
    kind: Literal["promo", "list_due", "list_promo"]
    text: str
    list_id: Optional[int]
    list_item_id: Optional[int]
    data: dict[str, Any]
    created_at: datetime


class SimulateResult(BaseModel):
    today: date
    created: list[SuggestionView]


class PromoOffer(BaseModel):
    sku_id: str
    name: str
    merchant_id: Optional[str]
    merchant: Optional[str]
    unit_price: float  # preço cheio
    price: float  # preço promocional
    simulated: bool  # true fora do simulador: o carrinho real não terá esse preço


class PurchasePattern(BaseModel):
    interval_days: Optional[int]
    progress: Optional[float]  # fração do ciclo passada; 0 = acabou de comprar, 1 = vence hoje
    days_to_due: Optional[int]
    regularity: float
    source: Literal["history", "item_recurrence", "list_recurrence", "list_due", "none"]
    purchases: int


class OfferEvaluation(BaseModel):
    item_id: int
    list_id: int
    raw_text: str
    offer: Optional[str]
    discount_pct: Optional[int]
    action: Optional[Literal["advance", "stock"]]  # None: não sugeriu (veja reason)
    reason: str
    suggestion_id: Optional[int]
    pattern: PurchasePattern


class PromotionResult(BaseModel):
    offers: list[PromoOffer]
    evaluations: list[OfferEvaluation]
    suggestions: list[SuggestionView]  # criadas ou atualizadas pelo evento


class ItemPatternView(BaseModel):
    item_id: int
    list_id: int
    list_name: str
    raw_text: str
    last_purchase: Optional[date]
    next_due: Optional[date]
    pattern: PurchasePattern


class Health(BaseModel):
    ok: bool
    mode: str
