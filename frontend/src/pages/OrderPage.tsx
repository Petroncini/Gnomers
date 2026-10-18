import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, brl, Order, Plan, Tracking } from "../api";

const STATUS_PT: Record<string, string> = {
  ORDER_CREATED: "Pedido criado", PAYMENT_DECLINED: "Pagamento recusado", CONFIRMED: "Confirmado pelo mercado",
  PREPARING: "Separando os itens", DISPATCHED: "Saiu para entrega", ON_THE_WAY: "A caminho", ORDER_LATE: "Atrasado",
  DELIVERY_ARRIVED_AT_DESTINATION: "Entregador chegou", DELIVERED: "Entregue",
};

function OrderCard({ order }: { order: Order }) {
  const [tracking, setTracking] = useState<Tracking | null>(null);
  const [copied, setCopied] = useState(false);

  // o intervalo de polling vem do retryMessage do trackOrder; para no pollingStop
  useEffect(() => {
    let timer: number | undefined;
    let alive = true;
    const poll = async () => {
      try {
        const t = await api.get<Tracking>(`/orders/${order.id}/track`);
        if (!alive) return;
        setTracking(t);
        if (!t.stopped) timer = window.setTimeout(poll, Math.max(2, t.retry_after_s ?? 30) * 1000);
      } catch {
        if (alive) timer = window.setTimeout(poll, 30000);
      }
    };
    poll();
    return () => { alive = false; window.clearTimeout(timer); };
  }, [order.id]);

  const copy = async () => {
    if (!order.pix) return;
    await navigator.clipboard.writeText(order.pix.code);
    setCopied(true);
  };

  return (
    <section className="order-card">
      <header>
        <h2>{order.merchant_name}</h2>
        <strong>{brl(order.total)}</strong>
      </header>
      {order.pix ? (
        <div className="pix">
          <span className="muted small">Pix copia e cola</span>
          <code>{order.pix.code}</code>
          <button onClick={copy}>{copied ? "Copiado" : "Copiar código"}</button>
        </div>
      ) : (
        <p className="muted small">O iFood não devolveu o código Pix deste pedido; pague pelo app do iFood.</p>
      )}
      <ol className="tracking">
        {(tracking?.history ?? []).slice().reverse().map((h, i) => (
          <li key={i} className={i === (tracking?.history.length ?? 0) - 1 ? "current" : ""}>
            {STATUS_PT[h.code] ?? h.code}
          </li>
        ))}
      </ol>
      {tracking?.stopped && <p className="muted small">Acompanhamento encerrado.</p>}
    </section>
  );
}

export default function OrderPage() {
  const { planId } = useParams();
  const [plan, setPlan] = useState<Plan | null>(null);
  useEffect(() => { api.get<Plan>(`/plans/${planId}`).then(setPlan); }, [planId]);
  if (!plan) return <div className="page muted">Carregando…</div>;
  const orders = plan.orders ?? [];
  return (
    <div className="page">
      <Link to="/" className="muted small">← início</Link>
      <h1>{orders.length > 1 ? `${orders.length} pedidos enviados` : "Pedido enviado"}</h1>
      {orders.length > 1 && <p className="muted">Cada mercado é um pedido e um pagamento separado no iFood.</p>}
      {orders.map((o) => <OrderCard key={o.id} order={o} />)}
    </div>
  );
}
