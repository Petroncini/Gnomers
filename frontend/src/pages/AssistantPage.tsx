import { Fragment, useEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import { SendHorizontal } from "lucide-react";
import { api, brl, ChatResponse, Plan, ProgressEvent, ProposedRule, ShoppingList } from "../api";
import { DecisionBubble, resolutionLabel } from "../components/DecisionCard";
import { RuleProposal } from "../components/RuleCard";
import { ErrorStrip, Header } from "../ui";

const STAGES: [string, string][] = [
  ["address", "Endereço"], ["discovery", "Busca"], ["merchants", "Mercados"], ["gaps", "Lacunas"],
  ["normalize", "Produtos"], ["optimize", "Otimização"], ["carts", "Carrinhos e cupons"],
];

interface Message {
  from: "user" | "agent";
  text: string;
  steps?: string[];
  proposal?: ProposedRule;
  cardId: number | null;  // a conversa fica junto do conflito sobre o qual ela foi
}

const isSettled = (p: Plan) => p.status === "ready" || p.status === "ordered";
const isRunning = (p: Plan) => p.status === "pending" || p.status === "running";

// Chat do agente: mostra a montagem do carrinho, um conflito por vez e leva ao Carrinho (spec 5.6).
export default function AssistantPage() {
  const { planId } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const [plan, setPlan] = useState<Plan | null>(null);
  const [listName, setListName] = useState("");
  const [events, setEvents] = useState<ProgressEvent[]>([]);
  const [messages, setMessages] = useState<Message[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const end = useRef<HTMLDivElement>(null);
  const wantsChat = !!(location.state as { chat?: boolean } | null)?.chat;

  const fetchPlan = () => api.get<Plan>(`/plans/${planId}`).then((p) => (setPlan(p), p));

  useEffect(() => {
    let alive = true;
    let es: EventSource | null = null;
    setPlan(null);
    setEvents([]);
    setMessages([]);
    setFailed(false);
    fetchPlan().then((p) => {
      if (!alive) return;
      api.get<ShoppingList>(`/lists/${p.list_id}`).then((l) => alive && setListName(l.name)).catch(() => {});
      // carrinho já pronto e sem nada para decidir: vai direto para a confirmação
      const open = (p.result.decisions ?? []).some((d) => d.status === "open");
      if (isSettled(p) && !open && !wantsChat) return navigate(`/carrinho/${planId}/confirmar`, { replace: true });
      if (!isRunning(p)) return;
      es = new EventSource(`/api/plans/${planId}/events`);
      es.addEventListener("progress", (e) => setEvents((ev) => [...ev, JSON.parse((e as MessageEvent).data)]));
      es.addEventListener("end", () => { es?.close(); fetchPlan(); });
      es.onerror = () => { es?.close(); fetchPlan().catch(() => setFailed(true)); };
    }).catch(() => alive && setFailed(true));
    return () => { alive = false; es?.close(); };
  }, [planId]);

  useEffect(() => {
    const smooth = !window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    // até o fim do documento, e não até `end`: assim o rodapé fixo não cobre a última mensagem
    if (end.current) window.scrollTo({ top: document.documentElement.scrollHeight, behavior: smooth ? "smooth" : "auto" });
  }, [events.length, messages.length, plan]);

  const decisions = plan?.result.decisions ?? [];
  const questions = decisions.filter((d) => d.question);
  const current = questions.find((d) => d.status === "open") ?? null;
  const resolvedCount = questions.filter((d) => d.status !== "open").length;
  const infoCount = decisions.filter((d) => !d.question).length;
  const settled = !!plan && isSettled(plan);
  const running = !!plan && isRunning(plan);
  const chosen = plan?.result.chosen;

  const send = async () => {
    const msg = text.trim();
    if (!msg || !plan || busy) return;
    const cardId = current?.id ?? null;
    setText("");
    setMessages((m) => [...m, { from: "user", text: msg, cardId }]);
    setBusy(true);
    try {
      const r = await api.post<ChatResponse>(`/plans/${plan.id}/chat`, { message: msg, card_id: cardId ?? undefined });
      setPlan(r.plan);
      setMessages((m) => [...m, { from: "agent", text: r.reply, steps: r.steps, proposal: r.proposed_rule ?? undefined, cardId }]);
    } catch (e) {
      setMessages((m) => [...m, { from: "agent", text: `Não consegui: ${(e as Error).message}`, cardId }]);
    } finally {
      setBusy(false);
    }
  };

  const dropProposal = (i: number, extra?: Message) =>
    setMessages((ms) => [...ms.map((x, j) => (j === i ? { ...x, proposal: undefined } : x)), ...(extra ? [extra] : [])]);

  const thread = (cardId: number | null) => messages.map((m, i) => m.cardId !== cardId ? null : (
    <div key={`m${i}`} className={`bubble ${m.from}`}>
      {!!m.steps?.length && (
        <ul className="agent-steps" aria-label="O que o assistente fez">
          {m.steps.map((st, j) => <li key={j}>{st}</li>)}
        </ul>
      )}
      <p>{m.text}</p>
      {m.proposal && plan && (
        <RuleProposal proposal={m.proposal} listId={plan.list_id} planId={plan.id}
                      onSaved={(r) => dropProposal(i, { from: "agent", text: `Regra salva: ${r.text}`, cardId })}
                      onDiscard={() => dropProposal(i)} />
      )}
    </div>
  ));

  // barra de progresso: etapas enquanto monta, decisões depois
  const reached = new Set(events.map((e) => e.stage));
  const stageCount = STAGES.filter(([k]) => reached.has(k)).length;
  const progress = running
    ? { pct: Math.max(4, (stageCount / STAGES.length) * 100), label: `Etapa ${Math.min(stageCount + 1, STAGES.length)} de ${STAGES.length}` }
    : settled && questions.length
      ? { pct: (resolvedCount / questions.length) * 100, label: `${resolvedCount} de ${questions.length} resolvidas` }
      : settled ? { pct: 100, label: "Carrinho pronto" } : null;

  return (
    <div className="screen">
      {failed && <ErrorStrip onRetry={() => { setFailed(false); fetchPlan().catch(() => setFailed(true)); }} />}
      <div className="sticky-top">
        <Header back={plan ? `/listas/${plan.list_id}` : "/"} title="Assistente de compras" subtitle={listName || " "} titleClass="t-title-lg" />
        {progress && (
          <div className="progress-strip">
            <div className="track" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(progress.pct)}
                 aria-label="Progresso do carrinho">
              <div className="fill" style={{ width: `${progress.pct}%` }} />
            </div>
            <span className="t-small">{progress.label}</span>
          </div>
        )}
      </div>

      <main className="messages" aria-live="polite">
        {plan && (running || events.length > 0) && (
          <div className="bubble agent">
            <p>Vou montar o carrinho{listName ? ` de ${listName}` : ""}: procuro os mercados perto de você, comparo os preços e aplico o melhor cupom.</p>
          </div>
        )}
        {STAGES.filter(([k]) => reached.has(k)).map(([key, label]) => (
          <div key={key} className="bubble agent compact">
            <p><strong>{label}.</strong> {[...events].reverse().find((e) => e.stage === key)?.message}</p>
          </div>
        ))}
        {running && <div className="bubble agent compact" aria-label="Agente trabalhando"><span className="typing"><i /><i /><i /></span></div>}

        {plan?.status === "failed" && (
          <>
            <div className="bubble agent"><p>{plan.result.error?.message ?? plan.error ?? "Não consegui montar o carrinho."}</p></div>
            <div className="quick-replies">
              {plan.result.error?.code === "auth" && <a className="suggested" href="/api/auth/login">Entrar no iFood</a>}
              <Link to={`/listas/${plan.list_id}`}>Voltar para a lista</Link>
            </div>
          </>
        )}
        {plan?.status === "needs_address" && (
          <>
            <div className="bubble agent"><p>Não achei um endereço salvo perto de você. Cadastre um para eu procurar os mercados.</p></div>
            <div className="quick-replies"><Link className="suggested" to="/">Cadastrar endereço</Link></div>
          </>
        )}

        {settled && chosen && (
          <div className="bubble agent">
            <p>Carrinho pronto em {chosen.carts.length} {chosen.carts.length === 1 ? "mercado" : "mercados"}, já com frete e cupom:</p>
            <span className="total-line">{brl(chosen.total)}</span>
            {chosen.carts.length > 1 && (plan.result.savings_vs_single ?? 0) > 0 && (
              <p>Dividindo a compra, sai {brl(plan.result.savings_vs_single)} mais barato que num mercado só.</p>
            )}
            {questions.length > 0 && <p>Antes de confirmar, preciso de você em {questions.length === 1 ? "uma decisão" : `${questions.length} decisões`}.</p>}
          </div>
        )}

        {settled && plan && questions.map((d, i) => {
          if (d.status === "open" && d !== current) return null;
          if (d === current) {
            return (
              <Fragment key={d.id}>
                <DecisionBubble plan={plan} card={d} index={i + 1} total={questions.length} onPlan={setPlan} />
                {thread(d.id)}
              </Fragment>
            );
          }
          const talked = messages.some((m) => m.cardId === d.id);
          return (
            <Fragment key={d.id}>
              <div className="bubble agent compact"><p><strong>{d.title}.</strong> {d.text}</p></div>
              {thread(d.id)}
              {!talked && <div className="bubble user"><p>{resolutionLabel(d)}</p></div>}
            </Fragment>
          );
        })}

        {settled && !current && (
          <div className="bubble agent">
            <p>
              {questions.length ? "Tudo resolvido. " : ""}
              {infoCount > 0 ? `Também fiz ${infoCount} ${infoCount === 1 ? "escolha" : "escolhas"} de marca por você; dá para trocar no carrinho. ` : ""}
              Confira o carrinho e confirme quando quiser.
            </p>
          </div>
        )}
        {thread(null)}
        {busy && <div className="bubble agent compact" aria-label="Agente digitando"><span className="typing"><i /><i /><i /></span></div>}
        <div ref={end} />
      </main>

      <footer className="footer compact">
        <button type="button" className="btn-dark" disabled={!settled || !!current}
                onClick={() => navigate(`/carrinho/${planId}/confirmar`)}>
          {running ? "Montando o carrinho…" : chosen ? `Ver carrinho · ${brl(chosen.total)}` : "Ver carrinho"}
        </button>
        <form className="composer" onSubmit={(e) => { e.preventDefault(); send(); }}>
          <label className="pill">
            <span className="sr-only">Mensagem para o assistente</span>
            <input value={text} onChange={(e) => setText(e.target.value)} enterKeyHint="send" disabled={!settled}
                   placeholder={current ? "“prefiro Limpol”, “pode tirar”…" : "Pergunte sobre o carrinho…"} />
          </label>
          <button type="submit" className="send" aria-label="Enviar" disabled={!settled || busy || !text.trim()}>
            <SendHorizontal size={18} strokeWidth={2.2} aria-hidden="true" />
          </button>
        </form>
      </footer>
    </div>
  );
}
