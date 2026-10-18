import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { Minus, Plus, Trash2 } from "lucide-react";
import { ListItem, ParsedItem, Rule, ShoppingList, api } from "../api";
import { displayName, factsLine, rulesFor, understanding } from "../components/ItemRow";
import { ErrorStrip, Header, IconButton, ListSkeleton, PrimaryButton, Spinner, Thumb, UnderstoodList, useToast } from "../ui";

type Parsed = ParsedItem & { recurrence_days?: number | null };

interface Draft {
  raw_text: string;
  quantity: number;
  brand: string;
  variety: string;
  recurrence: string;
  category: string;
  pack_size: number | null;
  pack_unit: ListItem["pack_unit"];
}

const toDraft = (it: ListItem): Draft => ({
  raw_text: it.raw_text, quantity: it.quantity, brand: it.brand ?? "", variety: it.variety_terms.join(", "),
  recurrence: it.recurrence_days ? String(it.recurrence_days) : "", category: it.category,
  pack_size: it.pack_size, pack_unit: it.pack_unit,
});

const terms = (s: string) => s.split(",").map((x) => x.trim()).filter(Boolean);

// Item e restrições: tela cheia, porque a descrição pode ser longa (spec 5.3).
export default function ItemPage() {
  const { id, itemId } = useParams();
  const listId = Number(id);
  const back = `/listas/${id}`;
  const [list, setList] = useState<ShoppingList | null>(null);
  const [rules, setRules] = useState<Rule[]>([]);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [failed, setFailed] = useState(false);
  const [parsing, setParsing] = useState(false);
  const [saving, setSaving] = useState(false);
  const original = useRef<string>("");
  const timer = useRef<number>();
  const navigate = useNavigate();
  const toast = useToast();

  const load = () => {
    setFailed(false);
    Promise.all([api.get<ShoppingList>(`/lists/${id}`), api.get<Rule[]>("/rules")])
      .then(([l, r]) => {
        setList(l);
        setRules(r);
        const it = l.items?.find((x) => x.id === Number(itemId));
        if (it) {
          original.current = it.raw_text;
          setDraft(toDraft(it));
        }
      })
      .catch(() => setFailed(true));
  };
  useEffect(load, [id, itemId]);

  const set = (p: Partial<Draft>) => setDraft((d) => (d ? { ...d, ...p } : d));

  // reinterpreta a descrição alguns instantes depois que a pessoa para de digitar
  const text = draft?.raw_text;
  useEffect(() => {
    window.clearTimeout(timer.current);
    if (text == null || text === original.current || text.trim().length < 3) return setParsing(false);
    setParsing(true);
    timer.current = window.setTimeout(() => {
      api.post<Parsed>("/items/parse", { text })
        .then((p) => set({
          quantity: p.quantity, brand: p.brand ?? "", variety: p.variety_terms.join(", "), category: p.category,
          pack_size: p.pack_size, pack_unit: p.pack_unit,
          ...(p.recurrence_days ? { recurrence: String(p.recurrence_days) } : {}),
        }))
        .catch(() => {})
        .finally(() => setParsing(false));
    }, 800);
  }, [text]);

  const save = async () => {
    if (!draft) return;
    setSaving(true);
    try {
      await api.patch(`/lists/${id}/items/${itemId}`, {
        ...(draft.raw_text !== original.current ? { raw_text: draft.raw_text } : {}),
        quantity: draft.quantity, brand: draft.brand.trim() || null, variety_terms: terms(draft.variety),
        recurrence_days: draft.recurrence ? Number(draft.recurrence) || null : null,
      });
      toast("Item salvo");
      navigate(back);
    } catch (e) {
      toast((e as Error).message);
      setSaving(false);
    }
  };

  const remove = async () => {
    await api.del(`/lists/${id}/items/${itemId}`);
    toast("Item removido");
    navigate(back, { replace: true });
  };

  if (!list || !draft) {
    return (
      <div className="screen surface">
        {failed && <ErrorStrip onRetry={load} />}
        <Header back={back} eyebrow={list?.name} />
        <main className="content">
          {list && !draft ? <p className="t-body">Este item não está mais na lista. <Link to={back}>Voltar</Link></p>
                          : !failed && <ListSkeleton rows={3} height={96} />}
        </main>
      </div>
    );
  }

  const facts = { brand: draft.brand.trim() || null, variety_terms: terms(draft.variety), category: draft.category,
                  pack_size: draft.pack_size, pack_unit: draft.pack_unit,
                  recurrence_days: draft.recurrence ? Number(draft.recurrence) || null : null };
  const understood = understanding(facts, rulesFor(rules, draft.category, listId));
  const factLine = factsLine(facts);
  const step = (delta: number) => set({ quantity: Math.max(1, Math.round(draft.quantity) + delta) });

  return (
    <div className="screen surface">
      {failed && <ErrorStrip onRetry={load} />}
      <Header back={back} eyebrow={list.name}
              action={<IconButton label="Remover item" onClick={remove}><Trash2 size={18} aria-hidden="true" /></IconButton>} />

      <main className="content" style={{ gap: 24 }}>
        <div className="item-hero">
          <Thumb category={draft.category} size={64} />
          <h1 className="t-title-xl">{displayName(draft.raw_text) || "Item"}</h1>
        </div>

        <div className="card qty-row">
          <span className="t-title-md" id="qty-label">Quantidade</span>
          <div className="stepper" role="group" aria-labelledby="qty-label">
            <IconButton label="Diminuir" onClick={() => step(-1)} disabled={draft.quantity <= 1}><Minus size={18} aria-hidden="true" /></IconButton>
            <output aria-live="polite">{draft.quantity.toLocaleString("pt-BR")}</output>
            <IconButton label="Aumentar" onClick={() => step(1)}><Plus size={18} aria-hidden="true" /></IconButton>
          </div>
        </div>

        <label>
          <span className="label-row">
            <span className="t-title-md">Descrição e restrições</span>
            <span className="t-small muted">vale só nesta lista</span>
          </span>
          <textarea className="field" rows={3} value={draft.raw_text} onChange={(e) => set({ raw_text: e.target.value })}
                    placeholder="leite integral Italac 1 L" />
          <span className="hint" style={{ display: "block" }}>Marca, variedade e tamanho escritos aqui viram regras para o agente.</span>
        </label>

        <section className="card pad" aria-live="polite" aria-busy={parsing}>
          <div className="block-title">
            <h2 className="t-title-md">Como o agente entendeu</h2>
            {parsing && <Spinner small />}
          </div>
          <div className="stack" style={{ opacity: parsing ? 0.55 : 1, transition: "opacity 150ms ease-out" }}>
            {understood.length > 0
              ? <UnderstoodList items={understood} />
              : <p className="t-caption">Qualquer marca e variedade: o agente escolhe o melhor custo-benefício.</p>}
            {factLine && <p className="t-caption">{factLine}</p>}
          </div>
        </section>

        <section className="section">
          <h2 className="t-overline">Ajustar</h2>
          <div className="field-group">
            <label>
              <span className="t-small">Marca</span>
              <input className="input" value={draft.brand} placeholder="Qualquer marca" onChange={(e) => set({ brand: e.target.value })} />
            </label>
            <label>
              <span className="t-small">Variedade (separe por vírgula)</span>
              <input className="input" value={draft.variety} placeholder="integral, sem lactose" onChange={(e) => set({ variety: e.target.value })} />
            </label>
            <label className="row" style={{ justifyContent: "space-between" }}>
              <span className="t-body-strong">Repetir a cada</span>
              <span className="row">
                <input className="input inline" type="number" inputMode="numeric" min={0} placeholder="—" value={draft.recurrence}
                       onChange={(e) => set({ recurrence: e.target.value })} />
                <span className="t-body">dias</span>
              </span>
            </label>
          </div>
          <Link to={`/listas/${id}/preferencias`} className="btn-text" style={{ alignSelf: "flex-start" }}>
            Ver regras e preferências
          </Link>
        </section>
      </main>

      <footer className="footer">
        <PrimaryButton loading={saving} onClick={save}>Salvar item</PrimaryButton>
      </footer>
    </div>
  );
}
