import { useEffect, useRef, useState } from "react";
import { ChatResponse, Decision, Plan, ProposedRule, api } from "../api";
import { RuleProposal } from "./RuleCard";

interface Message {
  from: "user" | "agent";
  text: string;
  proposal?: ProposedRule;
}

// Chat ancorado num cartão: a resposta vira ação no carrinho e, se fizer sentido, regra.
export default function ChatDrawer({ plan, card, onClose, onPlan }: {
  plan: Plan; card: Decision | null; onClose: () => void; onPlan: (p: Plan) => void;
}) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const end = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setMessages(card ? [{ from: "agent", text: card.text }] : [{ from: "agent", text: "Pergunte o que quiser sobre este carrinho." }]);
  }, [card?.id]);
  useEffect(() => end.current?.scrollIntoView({ behavior: "smooth" }), [messages]);

  const send = async () => {
    if (!text.trim()) return;
    const msg = text;
    setText("");
    setMessages((m) => [...m, { from: "user", text: msg }]);
    setBusy(true);
    try {
      const r = await api.post<ChatResponse>(`/plans/${plan.id}/chat`, { message: msg, card_id: card?.id });
      onPlan(r.plan);
      setMessages((m) => [...m, { from: "agent", text: r.reply, proposal: r.proposed_rule ?? undefined }]);
    } catch (e) {
      setMessages((m) => [...m, { from: "agent", text: `Não consegui: ${(e as Error).message}` }]);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="drawer-backdrop" onClick={onClose}>
      <aside className="drawer" onClick={(e) => e.stopPropagation()}>
        <header>
          <strong>{card?.title ?? "Sobre o carrinho"}</strong>
          <button className="icon-btn" aria-label="Fechar" onClick={onClose}>×</button>
        </header>
        <div className="messages">
          {messages.map((m, i) => (
            <div key={i} className={`msg ${m.from}`}>
              <p>{m.text}</p>
              {m.proposal && (
                <RuleProposal proposal={m.proposal} listId={plan.list_id}
                              onSaved={(r) => setMessages((ms) => [...ms.map((x, j) => (j === i ? { ...x, proposal: undefined } : x)),
                                                                   { from: "agent", text: `Regra salva: ${r.text}` }])}
                              onDiscard={() => setMessages((ms) => ms.map((x, j) => (j === i ? { ...x, proposal: undefined } : x)))} />
              )}
            </div>
          ))}
          {busy && <div className="msg agent"><p className="muted">…</p></div>}
          <div ref={end} />
        </div>
        <footer>
          <input autoFocus placeholder={card ? "“prefiro Limpol”, “pode tirar”, “por que esse mercado?”" : "Pergunte…"}
                 value={text} onChange={(e) => setText(e.target.value)} onKeyDown={(e) => e.key === "Enter" && send()} />
          <button onClick={send} disabled={busy}>Enviar</button>
        </footer>
      </aside>
    </div>
  );
}
