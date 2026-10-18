import { useState } from "react";
import { api, brl, Decision, Plan } from "../api";

interface Props {
  plan: Plan;
  card: Decision;
  onPlan: (p: Plan) => void;
  onChat: (card: Decision) => void;
}

// O cartão já mostra a escolha aplicada; o usuário aceita, troca ou remove.
export default function DecisionCard({ plan, card, onPlan, onChat }: Props) {
  const [swapOpen, setSwapOpen] = useState(false);
  const [remember, setRemember] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const merchants = new Set(plan.result.chosen?.carts.map((c) => c.merchant_id));
  const swappable = card.alternatives.filter((a) => merchants.has(a.merchant_id));

  const send = async (action: string, extra: Record<string, unknown> = {}) => {
    setBusy(true);
    setError(null);
    try {
      const r = await api.post<{ plan: Plan }>(`/plans/${plan.id}/decisions/${card.id}`, { action, ...extra });
      onPlan(r.plan);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const swap = (skuId: string, brand: string | null) => {
    const rule = remember && brand && card.category
      ? { kind: "brand_chain", category: card.category, payload: { order: [brand], allow_others: true }, scope: "global" }
      : undefined;
    send("swap", { sku_id: skuId, rule });
  };

  const resolved = card.status === "resolved";
  return (
    <div className={`decision ${card.status} ${card.type}`}>
      <div className="decision-head">
        <strong>{card.title}</strong>
        {resolved && <span className="badge plain">{card.resolution === "swap" ? "trocado" : card.resolution === "remove" ? "removido" : "ok"}</span>}
      </div>
      <p>{card.text}</p>
      {!resolved && card.question && (
        <div className="actions">
          {card.item_id != null && <button disabled={busy} onClick={() => send("accept")}>Aceitar</button>}
          {swappable.length > 0 && <button className="ghost" disabled={busy} onClick={() => setSwapOpen(!swapOpen)}>Trocar</button>}
          {card.item_id != null && card.type !== "missing" && (
            <button className="ghost" disabled={busy} onClick={() => send("remove")}>Remover</button>
          )}
          {card.item_id == null && <button disabled={busy} onClick={() => send("dismiss")}>Ok</button>}
          <button className="link" onClick={() => onChat(card)}>Conversar</button>
        </div>
      )}
      {!resolved && !card.question && (
        <div className="actions">
          {swappable.length > 0 && <button className="ghost small" onClick={() => setSwapOpen(!swapOpen)}>Trocar</button>}
          <button className="link" onClick={() => onChat(card)}>Conversar</button>
        </div>
      )}
      {swapOpen && (
        <div className="swap-list">
          {swappable.map((a) => (
            <button key={a.sku_id} className="swap-option" disabled={busy} onClick={() => swap(a.sku_id, a.brand)}>
              <span>{a.name}</span>
              <span>{a.packs}× {brl(a.price)}</span>
            </button>
          ))}
          <label className="small">
            <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} /> lembrar esta marca nas próximas compras
          </label>
        </div>
      )}
      {error && <p className="error">{error}</p>}
    </div>
  );
}
