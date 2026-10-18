// Cliente da API do back-end (rotas da seção 12 do plano), servido pelo proxy /api do Vite.

export type Unit = "g" | "ml" | "un";

export interface ListItem {
  id: number;
  list_id: number;
  raw_text: string;
  quantity: number;
  category: string;
  brand: string | null;
  variety_terms: string[];
  pack_size: number | null;
  pack_unit: Unit | null;
  query: string;
  intention: "product" | "product_exact";
  recurrence_days: number | null;
  next_due: string | null;
}

export interface ShoppingList {
  id: number;
  name: string;
  recurrence_days: number | null;
  next_due: string | null;
  last_total: number | null;
  item_count: number;
  last_plan_id: number | null;
  quality_profile: number | null;
  max_merchants: number | null;
  split_min_savings: number | null;
  items?: ListItem[];
}

export interface Rule {
  id: number;
  scope: string;
  category: string | null;
  kind: string;
  payload: Record<string, unknown>;
  hardness: "hard" | "soft";
  source: "user" | "chat" | "learned";
  confidence: number;
  active: boolean;
  text: string;
}

export interface ProposedRule {
  kind: string;
  category: string | null;
  hardness: "hard" | "soft";
  payload: Record<string, unknown>;
  text: string;
  scope_options: string[];
}

export interface CartLine {
  item_id: number;
  sku_id: string;
  cart_item_id: string | null;
  name: string;
  brand: string | null;
  quantity: number;
  unit_price: number;
  image: string | null;
}

export interface Cart {
  merchant_id: string;
  merchant_name: string;
  cart_id: string;
  lines: CartLine[];
  summary: { subtotal: number; delivery: number; fees: number; benefits: number; voucher: number; total: number };
  voucher: { code: string; description: string | null; discount: number } | null;
  delivery: { id: string; name: string; fee: number; eta: { min: number; max: number } | null;
              slot: { from: string; to: string } | null } | null;
  delivery_problem: string | null;
  payment: { kind: string } | null;
  warning: { userFriendlyMessage?: string; message?: string } | null;
  blocking: boolean;
}

export interface Solution {
  index: number;
  carts: Cart[];
  total: number;
  estimated_total: number;
  ok: boolean;
  missing: number[];
  errors: { merchant_name: string; error: { userMessage?: string } | string }[];
}

export interface Alternative {
  sku_id: string;
  merchant_id: string;
  name: string;
  brand: string | null;
  price: number;
  packs: number;
  reasons: string[];
}

export interface Decision {
  id: number;
  type: string;
  item_id: number | null;
  category: string | null;
  title: string;
  text: string;
  question: boolean;
  status: "open" | "info" | "resolved";
  resolution?: string;
  chosen?: { sku_id: string; name: string; brand: string | null; price: number; packs: number; merchant_name: string };
  alternatives: Alternative[];
  saving?: number | null;
}

export interface PlanResult {
  deliver_at?: string | null;
  address?: { alias: string | null; formatted: string };
  chosen?: Solution;
  alternatives?: Solution[];
  single_total?: number | null;
  savings_vs_single?: number | null;
  missing?: { item_id: number; raw_text: string }[];
  decisions?: Decision[];
  items?: { id: number; raw_text: string; category: string }[];
  merchants?: Record<string, { name: string; delivery_fee: number; minimum_order: number; coverage: number }>;
  error?: { code: string; message: string };
  needs_address?: boolean;
}

export interface Order {
  id: number;
  merchant_name: string;
  ifood_order_id: string | null;
  total: number;
  status: string;
  pix: { code: string } | null;
}

export interface Plan {
  id: number;
  list_id: number;
  status: "pending" | "running" | "ready" | "failed" | "ordered" | "needs_address";
  error: string | null;
  result: PlanResult;
  orders?: Order[];
}

export interface ProgressEvent {
  stage: string;
  message?: string;
  t: number;
  code?: string;
  needs_address?: boolean;
}

export interface Suggestion {
  id: number;
  kind: "promo" | "list_due";
  text: string;
  list_id: number | null;
  data: Record<string, unknown>;
}

export interface Tracking {
  status: { code: string; description: string } | null;
  history: { code: string; description: string }[];
  stopped: boolean;
  retry_after_s: number | null;
  stop_message: string | null;
}

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
