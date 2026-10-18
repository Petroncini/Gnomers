import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowLeftRight } from "lucide-react";
import { api, brl, Decision, DecisionResponse, Plan, PlanCreated } from "../api";
import { RichText, useToast } from "../ui";

// Ações de um cartão de decisão: aceitar, trocar, remover, dispensar ou refazer o carrinho.
function useDecision(plan: Plan, card: Decision, onPlan: (p: Plan) => void) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();
  const toast = useToast();
  const merchants = new Set(plan.result.chosen?.carts.map((c) => c.merchant_id));
  const swappable = card.alternatives.filter((a) => merchants.has(a.merchant_id));

  const send = async (action: string, extra: Record<string, unknown> = {}) => {
    setBusy(true);
    setError(null);
    try {
      const r = await api.post<DecisionResponse>(`/plans/${plan.id}/decisions/${card.id}`, { action, ...extra });
      onPlan(r.plan);
      if (r.rule) toast(`Preferência salva: ${r.rule.text}`);
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

  // "Sempre esta marca": mantém a escolha atual e guarda a marca para as próximas compras
  const keepBrand = card.item_id != null && card.category && card.chosen?.brand
    && (card.type === "brand_choice" || card.type === "brand_substitution") ? card.chosen.brand : null;
  const rememberCurrent = () => keepBrand && send("accept", {
    rule: { kind: "brand_chain", category: card.category, payload: { order: [keepBrand], allow_others: true }, scope: "global" },
  });

  return { busy, error, swappable, send, replan, swap, keepBrand, rememberCurrent };
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
          Lembrar a marca que eu escolher nas próximas compras
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
  const { busy, error, swappable, send, replan, swap, keepBrand, rememberCurrent } = useDecision(plan, card, onPlan);
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
        {keepBrand && <button type="button" disabled={busy} onClick={rememberCurrent}>Sempre {keepBrand}</button>}
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
  const { busy, error, swappable, swap, keepBrand, rememberCurrent } = useDecision(plan, card, onPlan);
  const [swapOpen, setSwapOpen] = useState(false);
  const resolved = card.status === "resolved";

  return (
    <div className={`notice${tone === "plain" ? " plain" : ""}`}>
      <ArrowLeftRight size={16} className="ico" aria-hidden="true" />
      <div className="grow">
        <span><RichText text={card.text} /></span>
        {resolved && <span className="t-small">{resolutionLabel(card)}</span>}
        {!resolved && (keepBrand || swappable.length > 0) && (
          <span className="row" style={{ gap: 16, flexWrap: "wrap" }}>
            {keepBrand && (
              <button type="button" className="btn-text" disabled={busy} onClick={rememberCurrent}>Sempre {keepBrand}</button>
            )}
            {swappable.length > 0 && (
              <button type="button" className="btn-text" aria-expanded={swapOpen} onClick={() => setSwapOpen(!swapOpen)}>
                {swapOpen ? "Manter esta escolha" : "Trocar"}
              </button>
            )}
          </span>
        )}
        {swapOpen && <SwapOptions card={card} busy={busy} swappable={swappable} onSwap={swap} />}
        {error && <span role="alert">{error}</span>}
      </div>
    </div>
  );
}
