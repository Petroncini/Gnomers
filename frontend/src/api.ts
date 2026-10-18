// Cliente da API do back-end, servido pelo proxy /api do Vite.
//
// Os tipos vêm do contrato (contract/openapi.json), gerados em src/api-types.ts por
// `npm run gen:api`. Não escreva interfaces de resposta à mão aqui: se faltar um campo,
// ele precisa entrar em backend/app/schemas.py primeiro.

import type { components } from "./api-types";

type Schemas = components["schemas"];

export type Unit = NonNullable<Schemas["ListItemView"]["pack_unit"]>;
export type ListItem = Schemas["ListItemView"];
export type ParsedItem = Schemas["ParsedItem"];
export type ShoppingList = Schemas["ShoppingListView"];
export type Rule = Schemas["RuleView"];
export type ProposedRule = Schemas["ProposedRule"];
export type CartLine = Schemas["CartLine"];
export type Cart = Schemas["Cart"];
export type Solution = Schemas["Solution"];
export type Alternative = Schemas["Alternative"];
export type Decision = Schemas["Decision"];
export type PlanResult = Schemas["PlanResult"];
export type Order = Schemas["OrderView"];
export type Plan = Schemas["PlanView"];
export type ProgressEvent = Schemas["ProgressEvent"];
export type Suggestion = Schemas["SuggestionView"];
export type Tracking = Schemas["TrackingView"];
export type AuthStatus = Schemas["AuthStatus"];
export type UserView = Schemas["UserView"];
export type AddressView = Schemas["AddressView"];
export type NearbyAddress = Schemas["NearbyAddress"];
export type LocationResult = Schemas["LocationResult"];
export type CreateAddressResult = Schemas["CreateAddressResult"];
export type AddressCandidate = Schemas["AddressCandidate"];
export type ChatResponse = Schemas["ChatResponse"];
export type DecisionResponse = Schemas["DecisionResponse"];
export type CheckoutResponse = Schemas["CheckoutResponse"];
export type PlanCreated = Schemas["PlanCreated"];

export class ApiError extends Error {
  constructor(public status: number, message: string, public body?: unknown) {
    super(message);
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(`/api${path}`, {
    method,
    headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  const text = await res.text();
  const data = text ? JSON.parse(text) : null;
  if (!res.ok) throw new ApiError(res.status, data?.detail ?? res.statusText, data);
  return data as T;
}

export const api = {
  get: <T>(p: string) => request<T>("GET", p),
  post: <T>(p: string, b: unknown = {}) => request<T>("POST", p, b),
  patch: <T>(p: string, b: unknown) => request<T>("PATCH", p, b),
  del: <T>(p: string) => request<T>("DELETE", p),
};

export const brl = (v: number | null | undefined) =>
  (v ?? 0).toLocaleString("pt-BR", { style: "currency", currency: "BRL" });

export function getPosition(): Promise<{ lat: number; lng: number; accuracy: number } | null> {
  return new Promise((resolve) => {
    if (!navigator.geolocation) return resolve(null);
    navigator.geolocation.getCurrentPosition(
      (p) => resolve({ lat: p.coords.latitude, lng: p.coords.longitude, accuracy: p.coords.accuracy }),
      () => resolve(null),
      { enableHighAccuracy: true, timeout: 8000, maximumAge: 300000 },
    );
  });
}

// Endereço de entrega escolhido na tela inicial; vale até o usuário trocar.
const ADDRESS_KEY = "gnomers.address";

export function savedAddressId(): number | null {
  try {
    const v = localStorage.getItem(ADDRESS_KEY);
    return v ? Number(v) : null;
  } catch {
    return null;
  }
}

export function saveAddressId(id: number | null) {
  try {
    if (id == null) localStorage.removeItem(ADDRESS_KEY);
    else localStorage.setItem(ADDRESS_KEY, String(id));
  } catch {
    /* sem storage: o back-end cai no GPS ou no último endereço usado */
  }
}

// Corpo do POST /lists/{id}/plan: o endereço escolhido vence o GPS.
export function planTarget(): Record<string, number> {
  const id = savedAddressId();
  if (id) return { address_id: id };
  try {
    return JSON.parse(sessionStorage.getItem("gnomers.pos") ?? "null") ?? {};
  } catch {
    return {};
  }
}
