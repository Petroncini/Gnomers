import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Copy } from "lucide-react";
import { api, brl, Order, Plan, Tracking } from "../api";
import { ErrorStrip, Header, ListSkeleton, PrimaryButton, initials, useToast } from "../ui";

const STATUS_PT: Record<string, string> = {
  ORDER_CREATED: "Pedido criado", PAYMENT_DECLINED: "Pagamento recusado", CONFIRMED: "Confirmado pelo mercado",
  PREPARING: "Separando os itens", DISPATCHED: "Saiu para entrega", ON_THE_WAY: "A caminho", ORDER_LATE: "Atrasado",
  DELIVERY_ARRIVED_AT_DESTINATION: "Entregador chegou", DELIVERED: "Entregue",
};

function OrderCard({ order }: { order: Order }) {
  const [tracking, setTracking] = useState<Tracking | null>(null);
  const toast = useToast();

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
    try {
      await navigator.clipboard.writeText(order.pix.code);
      toast("Código Pix copiado");
    } catch {
      toast("Não deu para copiar; segure o código para selecionar");
    }
  };

  const history = tracking?.history ?? [];
  return (
    <section className="card store-card" aria-label={`Pedido em ${order.merchant_name}`}>
      <div className="top">
        <span className="store-logo" aria-hidden="true">{initials(order.merchant_name)}</span>
        <div className="grow">
          <span className="t-title-md">{order.merchant_name}</span>
          <span className="t-caption">{STATUS_PT[history[history.length - 1]?.code] ?? "Pedido enviado"}</span>
        </div>
        <span className="price" style={{ fontSize: "1rem" }}>{brl(order.total)}</span>
      </div>

      {order.pix ? (
        <div className="stack">
          <span className="t-small" style={{ color: "var(--color-ink-2)" }}>Pix copia e cola</span>
          <code className="pix-code">{order.pix.code}</code>
          <PrimaryButton onClick={copy}><Copy size={18} aria-hidden="true" />Copiar código Pix</PrimaryButton>
        </div>
      ) : (
        <div className="notice plain"><span className="grow">O iFood não devolveu o código Pix deste pedido; pague pelo app do iFood.</span></div>
      )}

      {history.length > 0 && (
        <div className="stack">
          <span className="t-small" style={{ color: "var(--color-ink-2)" }}>Acompanhamento</span>
          <ol className="timeline">
            {history.map((h, i) => (
              <li key={i} className={i === history.length - 1 ? "current" : "done"}>
                <span className="dot" aria-hidden="true" />
                <span>{STATUS_PT[h.code] ?? h.code}</span>
              </li>
            ))}
          </ol>
        </div>
      )}
      {tracking?.stopped && <p className="t-caption">Acompanhamento encerrado.</p>}
    </section>
  );
}

export default function OrderPage() {
  const { planId } = useParams();
  const [plan, setPlan] = useState<Plan | null>(null);
  const [failed, setFailed] = useState(false);
  const load = () => {
    setFailed(false);
    api.get<Plan>(`/plans/${planId}`).then(setPlan).catch(() => setFailed(true));
  };
  useEffect(load, [planId]);

  const orders = plan?.orders ?? [];
  return (
    <div className="screen">
      {failed && <ErrorStrip onRetry={load} />}
      <Header back="/" title={orders.length > 1 ? `${orders.length} pedidos enviados` : "Pedido enviado"} titleClass="t-title-lg"
              subtitle={orders.length > 1 ? "Cada mercado é um pedido e um pagamento separado no iFood." : undefined} />
      <main className="content">
        {!plan && !failed && <ListSkeleton rows={2} height={200} />}
        {orders.map((o) => <OrderCard key={o.id} order={o} />)}
        {plan && <Link to="/" className="btn-text" style={{ alignSelf: "center" }}>Voltar ao início</Link>}
      </main>
    </div>
  );
}
