import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, brl, Cart, Decision, Plan, ProgressEvent } from "../api";
import DecisionCard from "../components/DecisionCard";
import ChatDrawer from "../components/ChatDrawer";

const STAGES: [string, string][] = [
  ["address", "Endereço"], ["discovery", "Busca"], ["merchants", "Mercados"], ["gaps", "Lacunas"],
  ["normalize", "Produtos"], ["optimize", "Otimização"], ["carts", "Carrinhos e cupons"],
];

function fmtSlot(slot: { from: string; to: string }): string {
  const from = new Date(slot.from);
  const to = new Date(slot.to);
  const day = from.toLocaleDateString("pt-BR", { weekday: "short", day: "2-digit", month: "2-digit" });
  const h = (d: Date) => d.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
  return `${day}, ${h(from)}–${h(to)}`;
}

function CartGroup({ cart }: { cart: Cart }) {
  const s = cart.summary;
  return (
    <section className="cart-group">
      <header>
        <h2>{cart.merchant_name}</h2>
        {cart.delivery?.slot ? (
          <span className="muted">{fmtSlot(cart.delivery.slot)}</span>
        ) : cart.delivery?.eta && <span className="muted">{cart.delivery.eta.min}–{cart.delivery.eta.max} min</span>}
      </header>
      <ul>
        {cart.lines.map((ln) => (
          <li key={ln.sku_id}>
            <span className="qty">{ln.quantity}×</span>
            <span className="name">{ln.name}</span>
            <span className="price">{brl(ln.unit_price * ln.quantity)}</span>
          </li>
        ))}
      </ul>
      <dl className="breakdown">
        <dt>Produtos</dt><dd>{brl(s.subtotal)}</dd>
        <dt>Frete{cart.delivery ? ` (${cart.delivery.name})` : ""}</dt><dd>{brl(s.delivery)}</dd>
        {s.fees > 0 && (<><dt>Taxas</dt><dd>{brl(s.fees)}</dd></>)}
        {s.voucher > 0 && (<><dt>Cupom {cart.voucher?.code}</dt><dd className="discount">−{brl(s.voucher)}</dd></>)}
        <dt className="total">Total</dt><dd className="total">{brl(s.total)}</dd>
      </dl>
      {cart.delivery_problem && <p className="warning">Entrega: {cart.delivery_problem}</p>}
      {cart.warning && <p className="warning">{cart.warning.userFriendlyMessage ?? cart.warning.message}</p>}
    </section>
  );
}

export default function CartPage() {
  const { planId } = useParams();
  const [plan, setPlan] = useState<Plan | null>(null);
  const [events, setEvents] = useState<ProgressEvent[]>([]);
  const [chatCard, setChatCard] = useState<Decision | null | undefined>(undefined);
  const [checking, setChecking] = useState(false);
  const [changes, setChanges] = useState<{ merchant_name: string; before: number; after: number }[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();

  const load = () => api.get<Plan>(`/plans/${planId}`).then(setPlan);

  useEffect(() => {
    load();
    const es = new EventSource(`/api/plans/${planId}/events`);
    es.addEventListener("progress", (e) => setEvents((ev) => [...ev, JSON.parse((e as MessageEvent).data)]));
    es.addEventListener("end", () => { es.close(); load(); });
    es.onerror = () => { es.close(); load(); };
    return () => es.close();
  }, [planId]);

  const checkout = async (acceptChanges = false) => {
    setChecking(true);
    setError(null);
    try {
      const r = await api.post<{ status: string; changes?: typeof changes; plan: Plan }>(
        `/plans/${planId}/checkout`, { accept_changes: acceptChanges });
      if (r.status === "changed") {
        setChanges(r.changes ?? []);
        setPlan(r.plan);
      } else {
        navigate(`/pedido/${planId}`);
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setChecking(false);
    }
  };

  if (!plan) return <div className="page muted">Carregando…</div>;

  const running = plan.status === "pending" || plan.status === "running";
  const reached = new Set(events.map((e) => e.stage));
  const last = events[events.length - 1];

  if (running || (plan.status !== "ready" && plan.status !== "ordered")) {
    return (
      <div className="page">
        <h1>Montando seu carrinho</h1>
        <ol className="progress">
          {STAGES.map(([key, label]) => (
            <li key={key} className={reached.has(key) ? "done" : ""}>
              <span className="dot" />
              {label}
              <span className="muted small">{[...events].reverse().find((e) => e.stage === key)?.message}</span>
            </li>
          ))}
        </ol>
        {running && last && <p className="muted">{last.message}</p>}
        {plan.status === "failed" && (
          <div className="error-box">
            <p>{plan.result.error?.message ?? plan.error}</p>
            {plan.result.error?.code === "auth" && <a href="/api/auth/login">Entrar no iFood</a>}
            <Link to={`/listas/${plan.list_id}`}>Voltar para a lista</Link>
          </div>
        )}
        {plan.status === "needs_address" && (
          <div className="error-box">
            <p>Nenhum endereço salvo perto de você.</p>
            <Link to="/">Cadastrar endereço</Link>
          </div>
        )}
      </div>
    );
  }

  const r = plan.result;
  const chosen = r.chosen!;
  const decisions = r.decisions ?? [];
  const open = decisions.filter((d) => d.status === "open");
  const info = decisions.filter((d) => d.status !== "open");
  const ordered = plan.status === "ordered";

  return (
    <div className="page cart-page">
      <Link to={`/listas/${plan.list_id}`} className="muted small">← lista</Link>
      <div className="total-hero">
        <span className="muted">Total real, com frete e cupom</span>
        <strong>{brl(chosen.total)}</strong>
        {chosen.carts.length > 1 && r.savings_vs_single != null && r.savings_vs_single > 0 && (
          <span className="saving">{brl(r.savings_vs_single)} a menos que num mercado só</span>
        )}
        {r.address && <span className="muted small">entrega em {r.address.alias ?? r.address.formatted}</span>}
      </div>

      {open.length > 0 && (
        <section className="decisions">
          <h2>Precisa de você ({open.length})</h2>
          {open.map((d) => <DecisionCard key={d.id} plan={plan} card={d} onPlan={setPlan} onChat={setChatCard} />)}
        </section>
      )}

      {chosen.carts.map((c) => <CartGroup key={c.cart_id} cart={c} />)}

      {(r.missing?.length ?? 0) > 0 && (
        <p className="warning">Ficou de fora: {r.missing!.map((m) => m.raw_text).join(", ")}</p>
      )}

      {info.length > 0 && (
        <details className="decisions info">
          <summary>{info.length} escolhas que fiz por você</summary>
          {info.map((d) => <DecisionCard key={d.id} plan={plan} card={d} onPlan={setPlan} onChat={setChatCard} />)}
        </details>
      )}

      {(r.alternatives?.length ?? 0) > 0 && (
        <details className="alternatives">
          <summary>Outras opções montadas</summary>
          <ul>
            {r.alternatives!.map((a) => (
              <li key={a.index}>
                {a.carts.map((c) => c.merchant_name).join(" + ") || a.errors.map((e) => e.merchant_name).join(" + ")}
                <span>{a.ok ? brl(a.total) : "indisponível"}</span>
              </li>
            ))}
          </ul>
        </details>
      )}

      {changes && (
        <div className="error-box">
          <p>O total mudou desde que o carrinho foi montado:</p>
          <ul>{changes.map((c) => <li key={c.merchant_name}>{c.merchant_name}: {brl(c.before)} → {brl(c.after)}</li>)}</ul>
          <button onClick={() => checkout(true)}>Confirmar com o novo total</button>
        </div>
      )}
      {error && <p className="error">{error}</p>}

      <div className="checkout-bar">
        <button className="ghost" onClick={() => setChatCard(null)}>Perguntar</button>
        {ordered ? (
          <Link to={`/pedido/${plan.id}`} className="button primary big">Ver pedido</Link>
        ) : (
          <button className="primary big" disabled={checking || !chosen.ok} onClick={() => checkout(false)}>
            {checking ? "Enviando…" : `Confirmar e pedir · ${brl(chosen.total)}`}
          </button>
        )}
      </div>

      {chatCard !== undefined && (
        <ChatDrawer plan={plan} card={chatCard} onClose={() => setChatCard(undefined)} onPlan={setPlan} />
      )}
    </div>
  );
}
