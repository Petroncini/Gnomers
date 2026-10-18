import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowLeftRight } from "lucide-react";
import { api, brl, Decision, Plan, PlanCreated } from "../api";
import { RichText } from "../ui";

// Ações de um cartão de decisão: aceitar, trocar, remover, dispensar ou refazer o carrinho.
function useDecision(plan: Plan, card: Decision, onPlan: (p: Plan) => void) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();
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

  // novo carrinho do zero, mantendo as trocas feitas neste
  const replan = async () => {
    setBusy(true);
    try {
      const next = await api.post<PlanCreated>(`/plans/${plan.id}/replan`);
      navigate(`/carrinho/${next.id}`);
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  };

  const swap = (skuId: string, brand: string | null, remember: boolean) => {
    const rule = remember && brand && card.category
      ? { kind: "brand_chain", category: card.category, payload: { order: [brand], allow_others: true }, scope: "global" }
      : undefined;
    send("swap", { sku_id: skuId, rule });
  };

  return { busy, error, swappable, send, replan, swap };
}

function SwapOptions({ card, busy, swappable, onSwap }: {
  card: Decision; busy: boolean; swappable: Decision["alternatives"]; onSwap: (sku: string, brand: string | null, remember: boolean) => void;
}) {
  const [remember, setRemember] = useState(true);
  return (
    <div className="swap-options">
      {swappable.map((a) => (
        <button key={a.sku_id} type="button" className="option" disabled={busy} onClick={() => onSwap(a.sku_id, a.brand, remember)}>
          <span className="grow">
            <span className="t-body-strong">{a.name}</span>
            {a.reasons.length > 0 && <span className="t-caption">{a.reasons.join(" · ")}</span>}
          </span>
          <span className="price">{a.packs}× {brl(a.price)}</span>
        </button>
      ))}
      {card.category && (
        <label className="check-row">
          <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} />
          Lembrar esta marca nas próximas compras
        </label>
      )}
    </div>
  );
}

const RESOLUTION: Record<string, string> = {
  accept: "Pode ser assim", swap: "Troquei", remove: "Pode tirar", dismiss: "Ok", set_quantity: "Ajustei a quantidade",
};

export const resolutionLabel = (card: Decision) => RESOLUTION[card.resolution ?? ""] ?? "Resolvido";

// Conflito no chat: balão do agente com respostas rápidas (spec 4.12). Um por vez.
export function DecisionBubble({ plan, card, index, total, onPlan }: {
  plan: Plan; card: Decision; index: number; total: number; onPlan: (p: Plan) => void;
}) {
  const { busy, error, swappable, send, replan, swap } = useDecision(plan, card, onPlan);
  const [swapOpen, setSwapOpen] = useState(false);

  return (
    <>
      <div className="bubble agent">
        <span className="conflict">{index} de {total} · {card.title}</span>
        <p><RichText text={card.text} /></p>
        {error && <p className="conflict" role="alert">{error}</p>}
      </div>
      <div className="quick-replies" role="group" aria-label="Respostas rápidas">
        {card.item_id != null && <button type="button" className="suggested" disabled={busy} onClick={() => send("accept")}>Aceitar</button>}
        {card.type === "replan" && <button type="button" className="suggested" disabled={busy} onClick={replan}>Refazer carrinho</button>}
        {card.item_id == null && (
          <button type="button" className={card.type === "replan" ? "" : "suggested"} disabled={busy} onClick={() => send("dismiss")}>
            {card.type === "replan" ? "Manter assim" : "Ok"}
          </button>
        )}
        {swappable.length > 0 && (
          <button type="button" disabled={busy} aria-expanded={swapOpen} onClick={() => setSwapOpen(!swapOpen)}>Trocar</button>
        )}
        {card.item_id != null && card.type !== "missing" && (
          <button type="button" disabled={busy} onClick={() => send("remove")}>Remover</button>
        )}
      </div>
      {swapOpen && <SwapOptions card={card} busy={busy} swappable={swappable} onSwap={swap} />}
    </>
  );
}

// Escolha que o agente fez sozinho, mostrada no carrinho; dá para trocar ali mesmo (spec 4.14).
export function ChoiceNote({ plan, card, onPlan, tone = "warn" }: {
  plan: Plan; card: Decision; onPlan: (p: Plan) => void; tone?: "warn" | "plain";
}) {
  const { busy, error, swappable, swap } = useDecision(plan, card, onPlan);
  const [swapOpen, setSwapOpen] = useState(false);
  const resolved = card.status === "resolved";

  return (
    <div className={`notice${tone === "plain" ? " plain" : ""}`}>
      <ArrowLeftRight size={16} className="ico" aria-hidden="true" />
      <div className="grow">
        <span><RichText text={card.text} /></span>
        {resolved && <span className="t-small">{resolutionLabel(card)}</span>}
        {!resolved && swappable.length > 0 && (
          <button type="button" className="btn-text" aria-expanded={swapOpen} onClick={() => setSwapOpen(!swapOpen)}>
            {swapOpen ? "Manter esta escolha" : "Trocar"}
          </button>
        )}
        {swapOpen && <SwapOptions card={card} busy={busy} swappable={swappable} onSwap={swap} />}
        {error && <span role="alert">{error}</span>}
      </div>
    </div>
  );
}
