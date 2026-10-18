import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { CalendarClock, ChevronRight, Ellipsis, Plus, Receipt, Repeat, ShoppingCart, SlidersHorizontal, Zap } from "lucide-react";
import { ParsedItem, Rule, ShoppingList, api, brl, planTarget } from "../api";
import ItemRow, { factsLine, hasRestriction, rulesFor, understanding } from "../components/ItemRow";
import { ErrorStrip, Fab, Header, IconButton, ListSkeleton, PrimaryButton, Sheet, Spinner, UnderstoodList, useToast } from "../ui";

type Parsed = ParsedItem & { recurrence_days?: number | null };

const VISIBLE = 8;
const DELIVER_KEY = "gnomers.deliverAt";

function fmtDeliverAt(v: string): string {
  const d = new Date(v);
  const day = d.toLocaleDateString("pt-BR", { weekday: "short", day: "2-digit", month: "2-digit" });
  return `${day}, ${d.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" })}`;
}

const fmtDay = (d: string) => new Date(d + "T12:00").toLocaleDateString("pt-BR", { weekday: "short", day: "2-digit", month: "2-digit" });

export default function ListPage() {
  const { id } = useParams();
  const listId = Number(id);
  const [list, setList] = useState<ShoppingList | null>(null);
  const [rules, setRules] = useState<Rule[]>([]);
  const [failed, setFailed] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [sheet, setSheet] = useState<null | "add" | "options" | "delivery">(null);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // horário de entrega opcional: permite montar carrinho com mercado fechado agora (entrega agendada)
  const [deliverAt, setDeliverAt] = useState<string>(() => {
    try { return localStorage.getItem(DELIVER_KEY) ?? ""; } catch { return ""; }
  });
  const navigate = useNavigate();
  const toast = useToast();

  const changeDeliverAt = (v: string) => {
    setDeliverAt(v);
    try { if (v) localStorage.setItem(DELIVER_KEY, v); else localStorage.removeItem(DELIVER_KEY); } catch { /* opcional */ }
  };

  const load = () => {
    setFailed(false);
    Promise.all([
      api.get<ShoppingList>(`/lists/${id}`).then(setList),
      api.get<Rule[]>("/rules").then(setRules),
    ]).catch(() => setFailed(true));
  };
  useEffect(load, [id]);

  const createCart = async () => {
    setStarting(true);
    setError(null);
    try {
      const plan = await api.post<{ id: number }>(`/lists/${id}/plan`, { ...planTarget(), deliver_at: deliverAt || undefined });
      navigate(`/carrinho/${plan.id}`);
    } catch (e) {
      setError((e as Error).message);
      setStarting(false);
    }
  };

  if (!list) {
    return (
      <div className="screen">
        {failed && <ErrorStrip onRetry={load} />}
        <Header back="/" title={failed ? "Lista" : " "} />
        <main className="content">{!failed && <ListSkeleton rows={5} height={64} />}</main>
      </div>
    );
  }

  const items = list.items ?? [];
  const restricted = new Set(items.filter((it) => hasRestriction(it, rulesFor(rules, it.category, listId))).map((it) => it.id));
  const shown = expanded ? items : items.slice(0, VISIBLE);
  // regras que valem para esta lista: as globais e as dela
  const listRules = rules.filter((r) => r.active && (r.scope === "global" || r.scope === String(listId)));
  const learnedCount = listRules.filter((r) => r.source === "learned").length;
  const ownCount = listRules.length - learnedCount;
  const prefsLabel = listRules.length === 0 ? "Nenhuma ainda: diga do que você gosta"
    : [ownCount && `${ownCount} ${ownCount === 1 ? "regra sua" : "regras suas"}`,
       learnedCount && `${learnedCount} ${learnedCount === 1 ? "aprendida" : "aprendidas"}`].filter(Boolean).join(" · ");

  return (
    <div className="screen">
      {failed && <ErrorStrip onRetry={load} />}
      <Header back="/" title={list.name}
              subtitle={`${items.length} ${items.length === 1 ? "item" : "itens"} · ${restricted.size} com restrição`}
              action={<IconButton label="Mais opções" onClick={() => setSheet("options")}><Ellipsis size={28} strokeWidth={2.8} aria-hidden="true" /></IconButton>} />

      <main className="content has-fab">
        {error && <div className="notice" role="alert"><span className="grow">{error}</span></div>}

        <div className="card divided">
          <button type="button" className="info-row" onClick={() => setSheet("options")}>
            <Repeat size={20} className="ico" aria-hidden="true" />
            <span className="grow">
              <span className="t-small muted">Repetição</span>
              <span className="t-body-strong">{list.recurrence_days ? `A cada ${list.recurrence_days} dias` : "Sem repetição"}</span>
              {list.next_due && <span className="t-caption">Próxima compra em {fmtDay(list.next_due)}</span>}
            </span>
            <span className="btn-text" aria-hidden="true">{list.recurrence_days ? "Trocar" : "Definir"}</span>
          </button>
          <Link to={`/listas/${id}/preferencias`} className="info-row">
            <SlidersHorizontal size={20} className="ico" aria-hidden="true" />
            <span className="grow">
              <span className="t-small muted">Preferências</span>
              <span className="t-body-strong">{prefsLabel}</span>
            </span>
            <ChevronRight size={16} className="chev" style={{ color: "var(--color-dashed)" }} aria-hidden="true" />
          </Link>
        </div>

        <button type="button" className="add-dashed" onClick={() => setSheet("add")}>
          <Plus size={18} strokeWidth={2.4} aria-hidden="true" />Adicionar item
        </button>

        {items.length === 0 ? (
          <p className="t-caption" style={{ textAlign: "center", padding: "8px 16px" }}>
            Sua lista está vazia. Você escolhe o produto; loja, marca e preço o agente define ao montar o carrinho.
          </p>
        ) : (
          <div className="stack">
            <ul className="card divided" aria-label="Itens da lista">
              {shown.map((it) => (
                <ItemRow key={it.id} item={it} to={`/listas/${id}/itens/${it.id}`} restricted={restricted.has(it.id)} />
              ))}
            </ul>
            {items.length > VISIBLE && (
              <button type="button" className="btn-text" onClick={() => setExpanded(!expanded)}>
                {expanded ? "Ver menos" : `Ver mais ${items.length - VISIBLE} itens`}
              </button>
            )}
          </div>
        )}

        <div className="card divided">
          <button type="button" className="info-row" onClick={() => setSheet("delivery")}>
            <CalendarClock size={20} className="ico" aria-hidden="true" />
            <span className="grow">
              <span className="t-small muted">Entrega</span>
              <span className="t-body-strong">{deliverAt ? fmtDeliverAt(deliverAt) : "O quanto antes"}</span>
            </span>
            <span className="btn-text" aria-hidden="true">Trocar</span>
          </button>
          {list.last_plan_id && (
            <Link to={`/carrinho/${list.last_plan_id}`} className="info-row">
              <Receipt size={20} className="ico" aria-hidden="true" />
              <span className="grow">
                <span className="t-small muted">Último carrinho</span>
                <span className="t-body-strong">{list.last_total != null ? brl(list.last_total) : "Ver de novo"}</span>
              </span>
              <ChevronRight size={16} className="chev" style={{ color: "var(--color-dashed)" }} aria-hidden="true" />
            </Link>
          )}
        </div>
      </main>

      <Fab icon={<ShoppingCart size={20} strokeWidth={2.2} aria-hidden="true" />} loading={starting}
           disabled={!items.length} onClick={createCart}>
        Montar carrinho
      </Fab>

      {sheet === "add" && <AddItemSheet listId={listId} listName={list.name} rules={rules} onAdded={load} onClose={() => setSheet(null)} />}
      {sheet === "options" && <OptionsSheet list={list} onSaved={(l) => { setList({ ...list, ...l }); setSheet(null); toast("Lista salva"); }}
                                            onClose={() => setSheet(null)} />}
      {sheet === "delivery" && <DeliverySheet value={deliverAt} onSave={(v) => { changeDeliverAt(v); setSheet(null); }}
                                              onClose={() => setSheet(null)} />}
    </div>
  );
}

// Adicionar item em texto livre: o agente interpreta enquanto a pessoa digita.
function AddItemSheet({ listId, listName, rules, onAdded, onClose }: {
  listId: number; listName: string; rules: Rule[]; onAdded: () => void; onClose: () => void;
}) {
  const [text, setText] = useState("");
  const [preview, setPreview] = useState<Parsed | null>(null);
  const [parsing, setParsing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [added, setAdded] = useState(0);
  const timer = useRef<number>();
  const input = useRef<HTMLInputElement>(null);
  const toast = useToast();

  useEffect(() => {
    window.clearTimeout(timer.current);
    if (text.trim().length < 3) return (setPreview(null), setParsing(false));
    setParsing(true);
    timer.current = window.setTimeout(() => {
      api.post<Parsed>("/items/parse", { text })
        .then(setPreview).catch(() => setPreview(null)).finally(() => setParsing(false));
    }, 350);
  }, [text]);

  const add = async () => {
    if (!text.trim() || saving) return;
    setSaving(true);
    try {
      const overrides = preview ? { quantity: preview.quantity, brand: preview.brand, variety_terms: preview.variety_terms,
                                    recurrence_days: preview.recurrence_days ?? undefined } : {};
      await api.post(`/lists/${listId}/items`, { raw_text: text, ...overrides });
      setText("");
      setPreview(null);
      setAdded((n) => n + 1);
      toast("Item adicionado");
      onAdded();
      input.current?.focus();
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const understood = preview ? understanding({ ...preview, recurrence_days: preview.recurrence_days ?? null },
                                             rulesFor(rules, preview.category, listId)) : [];
  const facts = preview && factsLine({ ...preview, recurrence_days: preview.recurrence_days ?? null });

  return (
    <Sheet title="Adicionar item" onClose={onClose}
           footer={<>
             <PrimaryButton loading={saving} disabled={!text.trim()} onClick={add}>Adicionar à lista</PrimaryButton>
             {added > 0 && <button type="button" className="btn-text" onClick={onClose}>Concluir ({added} {added === 1 ? "item" : "itens"})</button>}
           </>}>
      <p className="t-caption">Adicionando à lista <strong style={{ color: "var(--color-ink)" }}>{listName}</strong></p>
      <label>
        <span className="label-row"><span className="t-title-md">O que você quer?</span></span>
        <input ref={input} className="input" autoFocus enterKeyHint="done" value={text}
               placeholder="2 leite integral Italac" onChange={(e) => setText(e.target.value)}
               onKeyDown={(e) => e.key === "Enter" && add()} />
        <span className="hint" style={{ display: "block" }}>Quantidade, marca e tamanho entram no texto: “café 500 g”, “6 ovos”.</span>
      </label>
      {(preview || parsing) && (
        <section className="card pad" aria-live="polite">
          <div className="block-title">
            <h3 className="t-title-md">Como o agente entendeu</h3>
            {parsing && <Spinner small />}
          </div>
          {preview && (
            <div className="stack">
              {understood.length > 0
                ? <UnderstoodList items={understood} />
                : <p className="t-caption">Qualquer marca: o agente escolhe o melhor custo-benefício.</p>}
              <p className="t-caption">{preview.quantity.toLocaleString("pt-BR")}× {preview.query}{facts ? ` · ${facts.toLowerCase()}` : ""}</p>
            </div>
          )}
        </section>
      )}
    </Sheet>
  );
}

function OptionsSheet({ list, onSaved, onClose }: {
  list: ShoppingList; onSaved: (l: ShoppingList) => void; onClose: () => void;
}) {
  const [name, setName] = useState(list.name);
  const [days, setDays] = useState(list.recurrence_days ? String(list.recurrence_days) : "");
  const [saving, setSaving] = useState(false);

  const save = async () => {
    setSaving(true);
    try {
      onSaved(await api.patch<ShoppingList>(`/lists/${list.id}`, {
        name: name.trim() || list.name, recurrence_days: days ? Number(days) : null,
      }));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Sheet title="Opções da lista" onClose={onClose}
           footer={<PrimaryButton loading={saving} onClick={save}>Salvar</PrimaryButton>}>
      <label>
        <span className="label-row"><span className="t-title-md">Nome</span></span>
        <input className="input" value={name} onChange={(e) => setName(e.target.value)} enterKeyHint="done" />
      </label>
      <label className="row" style={{ justifyContent: "space-between" }}>
        <span className="t-title-md">Repetir a cada</span>
        <span className="row">
          <input className="input inline" type="number" inputMode="numeric" min={0} placeholder="—" value={days}
                 onChange={(e) => setDays(e.target.value)} />
          <span className="t-body">dias</span>
        </span>
      </label>
      <Link to={`/listas/${list.id}/preferencias`} className="option" style={{ textDecoration: "none" }}>
        <span className="ico-box"><SlidersHorizontal size={18} aria-hidden="true" /></span>
        <span className="grow">
          <span className="t-body-strong">Preferências desta lista</span>
          <span className="t-caption">Regras de marca e número de mercados</span>
        </span>
        <ChevronRight size={16} style={{ color: "var(--color-dashed)" }} aria-hidden="true" />
      </Link>
    </Sheet>
  );
}

function DeliverySheet({ value, onSave, onClose }: { value: string; onSave: (v: string) => void; onClose: () => void }) {
  const [scheduled, setScheduled] = useState(!!value);
  const [at, setAt] = useState(value);

  return (
    <Sheet title="Quando entregar?" onClose={onClose}
           footer={<PrimaryButton disabled={scheduled && !at} onClick={() => onSave(scheduled ? at : "")}>Salvar</PrimaryButton>}>
      <div className="stack" role="radiogroup" aria-label="Horário de entrega">
        <label className="option">
          <span className="ico-box"><Zap size={18} aria-hidden="true" /></span>
          <span className="grow">
            <span className="t-body-strong">O quanto antes</span>
            <span className="t-caption">Só mercados abertos agora</span>
          </span>
          <input type="radio" name="when" checked={!scheduled} onChange={() => setScheduled(false)} />
        </label>
        <label className="option">
          <span className="ico-box"><CalendarClock size={18} aria-hidden="true" /></span>
          <span className="grow">
            <span className="t-body-strong">Agendar</span>
            <span className="t-caption">Inclui mercados fechados agora, com entrega agendada</span>
          </span>
          <input type="radio" name="when" checked={scheduled} onChange={() => setScheduled(true)} />
        </label>
      </div>
      {scheduled && (
        <label>
          <span className="label-row"><span className="t-title-md">Dia e hora</span></span>
          <input className="input" type="datetime-local" step={3600} value={at} onChange={(e) => setAt(e.target.value)} />
        </label>
      )}
    </Sheet>
  );
}
