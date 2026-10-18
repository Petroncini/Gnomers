"""Wrappers tipados das tools do iFood usadas pelo back-end.

Toda chamada passa por aqui: os wrappers montam os argumentos no formato da tool,
validam o que o pipeline manda (nada de ID inventado, qualificador na query etc.) e
repassam ao recorder, que decide entre MCP real, gravação e reprodução.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from app.config import settings
from app.db import engine
from app.mcp.client import DbTokenStorage, IfoodConnection, McpToolError, TRANSIENT_CODES
from app.mcp.recorder import Recorder

log = logging.getLogger(__name__)

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
QUALIFIERS = re.compile(r"\b(barat[oa]s?|perto|rápid[oa]|rapid[oa]|em conta|promo[cç][aã]o|mais em conta)\b", re.I)


def _uuid(value: str, field: str) -> str:
    if not isinstance(value, str) or not UUID_RE.match(value):
        raise ValueError(f"{field} precisa ser um UUID vindo de uma resposta do MCP, recebi {value!r}")
    return value


def _loc(lat: float, lng: float) -> dict:
    return {"latitude": float(lat), "longitude": float(lng)}


class Ifood:
    def __init__(self, mode: str | None = None):
        self.mode = mode or settings.mcp_mode
        self.connection: IfoodConnection | None = None
        self.fake = None
        if self.mode == "fake":
            from app.mcp.fake import FakeIfood

            self.fake = FakeIfood()
            caller = self.fake
            recorder_mode = "live"
        else:
            self.connection = IfoodConnection(DbTokenStorage(engine))
            caller = self.connection.call
            recorder_mode = self.mode
        self.recorder = Recorder(recorder_mode, settings.fixtures_dir, caller if self.mode != "replay" else None)
        self._sem = asyncio.Semaphore(settings.mcp_concurrency)

    async def call(self, tool: str, args: dict) -> Any:
        # remove campos vazios: o MCP trata ausência e null de jeitos diferentes
        args = {k: v for k, v in args.items() if v is not None}
        async with self._sem:
            try:
                return await self.recorder.call(tool, args)
            except McpToolError as exc:
                if exc.code in TRANSIENT_CODES and self.mode != "replay":
                    log.info("repetindo %s depois de %s", tool, exc.code)
                    await asyncio.sleep(1.0)
                    return await self.recorder.call(tool, args)
                raise

    # ------------------------------------------------------------------ user

    async def get_user(self, include_profile: bool = False) -> dict:
        return await self.call("getUser", {"includeProfileData": include_profile})

    async def list_addresses(self) -> list[dict]:
        body = await self.call("listAddresses", {})
        return body.get("results", [])

    async def create_address(self, address: str, *, candidate_id: str | None = None, alias: str | None = None,
                             complement: str | None = None, neighborhood: str | None = None,
                             reference: str | None = None) -> dict:
        return await self.call("createAddress", {"address": address, "candidateId": candidate_id, "alias": alias,
                                                 "complement": complement, "neighborhood": neighborhood,
                                                 "reference": reference})

    # ------------------------------------------------------------- discovery

    async def search_items(self, query: str, lat: float, lng: float, *, intention: str = "product",
                           refinements: dict | None = None, page_size: int = 25, include_facets: bool = False,
                           merchant_type: str = "GROCERIES") -> dict:
        if not query.strip():
            raise ValueError("query vazia")
        if QUALIFIERS.search(query):
            raise ValueError(f"qualificador na query ({query!r}); use refinements")
        ref = dict(refinements or {})
        if "merchantId" in ref:
            _uuid(ref["merchantId"], "merchantId")
        return await self.call("searchOffers", {
            "query": query, "userLocation": _loc(lat, lng), "resultKind": "ITEM", "merchantType": merchant_type,
            "intention": intention, "refinements": ref or None, "pageSize": page_size,
            "includeFacets": include_facets or None,
        })

    async def browse(self, lat: float, lng: float, *, merchant_type: str = "GROCERIES", result_kind: str = "MERCHANT",
                     refinements: dict | None = None, page_size: int = 25, include_facets: bool = True) -> dict:
        return await self.call("browseOffers", {"merchantType": merchant_type, "userLocation": _loc(lat, lng),
                                                "resultKind": result_kind, "refinements": refinements or None,
                                                "pageSize": page_size, "includeFacets": include_facets})

    async def recommendations(self, content_mode: str, lat: float, lng: float, *, result_kind: str = "ITEM",
                              merchant_type: str = "GROCERIES", refinements: dict | None = None,
                              page_size: int = 25) -> dict:
        return await self.call("getRecommendations", {
            "contentMode": content_mode, "userLocation": _loc(lat, lng), "resultKind": result_kind,
            "merchantType": merchant_type, "refinements": refinements or None, "pageSize": page_size})

    async def get_merchant(self, merchant_id: str, lat: float, lng: float) -> dict:
        return await self.call("getMerchant", {"merchantId": _uuid(merchant_id, "merchantId"),
                                               "userLocation": _loc(lat, lng)})

    async def get_item(self, merchant_id: str, item_id: str, lat: float, lng: float) -> dict:
        return await self.call("getItem", {"merchantId": _uuid(merchant_id, "merchantId"),
                                           "itemId": _uuid(item_id, "itemId"), "userLocation": _loc(lat, lng)})

    # ------------------------------------------------------------------ cart

    async def create_cart(self, merchant_id: str, address_id: str, items: list[dict]) -> dict:
        if not items:
            raise ValueError("carrinho precisa de pelo menos um item")
        payload = [{"itemId": _uuid(i["itemId"], "itemId"), "quantity": int(i["quantity"]),
                    "options": i.get("options", [])} for i in items]
        return await self.call("createCart", {"merchantId": _uuid(merchant_id, "merchantId"),
                                              "addressId": _uuid(address_id, "addressId"), "items": payload})

    async def get_cart(self, cart_id: str) -> dict:
        return await self.call("getCart", {"cartId": _uuid(cart_id, "cartId")})

    async def add_item(self, cart_id: str, item_id: str, quantity: int) -> dict:
        return await self.call("addItemToCart", {"cartId": _uuid(cart_id, "cartId"), "itemId": _uuid(item_id, "itemId"),
                                                 "quantity": int(quantity), "options": []})

    async def update_item(self, cart_id: str, cart_item_id: str, item_id: str, quantity: int) -> dict:
        return await self.call("updateCartItem", {"cartId": _uuid(cart_id, "cartId"),
                                                  "cartItemId": _uuid(cart_item_id, "cartItemId"),
                                                  "itemId": _uuid(item_id, "itemId"), "quantity": int(quantity),
                                                  "options": []})

    async def remove_item(self, cart_id: str, cart_item_id: str) -> dict:
        return await self.call("removeItemFromCart", {"cartId": _uuid(cart_id, "cartId"),
                                                      "cartItemId": _uuid(cart_item_id, "cartItemId")})

    async def list_vouchers(self, cart_id: str) -> list[dict]:
        body = await self.call("listVouchers", {"cartId": _uuid(cart_id, "cartId")})
        return body.get("vouchers", [])

    async def set_voucher(self, cart_id: str, code: str, campaign_id: str) -> dict:
        return await self.call("setVoucher", {"cartId": _uuid(cart_id, "cartId"), "code": code,
                                              "campaignId": campaign_id})

    async def remove_voucher(self, cart_id: str) -> dict:
        return await self.call("removeVoucher", {"cartId": _uuid(cart_id, "cartId")})

    async def delivery_methods(self, cart_id: str) -> list[dict]:
        body = await self.call("getCartDeliveryMethods", {"cartId": _uuid(cart_id, "cartId")})
        return body.get("deliveryMethods", [])

    async def set_delivery(self, cart_id: str, method_id: str, timeslot_id: str | None = None) -> dict:
        return await self.call("setCartDelivery", {
            "cartId": _uuid(cart_id, "cartId"), "deliveryMethodId": method_id,
            "selectedTimeSlotId": _uuid(timeslot_id, "selectedTimeSlotId") if timeslot_id else None})

    async def payment_methods(self, merchant_id: str) -> list[dict]:
        body = await self.call("listPaymentMethods", {"merchantId": _uuid(merchant_id, "merchantId")})
        return body.get("paymentMethods", [])

    async def set_payment(self, cart_id: str, payment_method_id: str) -> dict:
        return await self.call("setCartPayment", {"cartId": _uuid(cart_id, "cartId"),
                                                  "paymentMethodId": payment_method_id})

    async def submit_order(self, cart_id: str) -> dict:
        # só é chamado depois do "Confirmar e pedir" na tela
        return await self.call("submitOrder", {"cartId": _uuid(cart_id, "cartId"), "userConsent": True})

    # ---------------------------------------------------------------- orders

    async def track_order(self, order_id: str) -> dict:
        return await self.call("trackOrder", {"orderId": _uuid(order_id, "orderId")})

    async def get_order(self, order_id: str) -> dict:
        return await self.call("getOrder", {"orderId": _uuid(order_id, "orderId")})


_instance: Ifood | None = None


def ifood() -> Ifood:
    global _instance
    if _instance is None:
        _instance = Ifood()
    return _instance


def set_ifood(instance: Ifood) -> None:
    global _instance
    _instance = instance
