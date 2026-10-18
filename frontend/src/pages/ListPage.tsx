import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, brl, ListItem, planTarget, ShoppingList } from "../api";
import ItemRow, { Chips, ItemPatch } from "../components/ItemRow";

type Parsed = Omit<ListItem, "id" | "list_id" | "raw_text" | "next_due">;

export default function ListPage() {
  const { id } = useParams();
  const [list, setList] = useState<ShoppingList | null>(null);
  const [text, setText] = useState("");
  const [preview, setPreview] = useState<Parsed | null>(null);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // horário de entrega opcional: permite montar carrinho com mercado fechado agora (entrega agendada)
  const [deliverAt, setDeliverAt] = useState<string>(() => {
    try { return localStorage.getItem("gnomers.deliverAt") ?? ""; } catch { return ""; }
  });
  const changeDeliverAt = (v: string) => {
    setDeliverAt(v);
    try { if (v) localStorage.setItem("gnomers.deliverAt", v); else localStorage.removeItem("gnomers.deliverAt"); } catch { /* opcional */ }
  };
  const timer = useRef<number>();
  const navigate = useNavigate();

  const load = () => api.get<ShoppingList>(`/lists/${id}`).then(setList);
  useEffect(() => { load(); }, [id]);

  // interpreta enquanto o usuário digita, para mostrar os chips antes de salvar
  useEffect(() => {
    window.clearTimeout(timer.current);
    if (text.trim().length < 3) return setPreview(null);
    timer.current = window.setTimeout(() => {
      api.post<Parsed>("/items/parse", { text }).then(setPreview).catch(() => setPreview(null));
    }, 350);
  }, [text]);

  const add = async () => {
    if (!text.trim()) return;
    const overrides = preview ? { quantity: preview.quantity, brand: preview.brand, variety_terms: preview.variety_terms,
                                  recurrence_days: preview.recurrence_days ?? undefined } : {};
    await api.post(`/lists/${id}/items`, { raw_text: text, ...overrides });
    setText("");
    setPreview(null);
    load();
  };

  const patch = async (itemId: number, p: ItemPatch) => {
    await api.patch(`/lists/${id}/items/${itemId}`, p);
    load();
  };

  const remove = async (itemId: number) => {
    await api.del(`/lists/${id}/items/${itemId}`);
    load();
  };

  const patchList = async (p: Partial<ShoppingList>) => {
    const updated = await api.patch<ShoppingList>(`/lists/${id}`, p);
    setList(updated);
  };

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

  if (!list) return <div className="page muted">Carregando…</div>;

  return (
    <div className="page list-page">
      <div className="list-head">
        <input className="title-input" value={list.name} onChange={(e) => setList({ ...list, name: e.target.value })}
               onBlur={() => patchList({ name: list.name })} />
        <label className="recurrence">
          repetir a cada
          <input type="number" min={0} value={list.recurrence_days ?? ""} placeholder="—"
                 onChange={(e) => setList({ ...list, recurrence_days: e.target.value ? Number(e.target.value) : null })}
                 onBlur={() => patchList({ recurrence_days: list.recurrence_days })} />
          dias
        </label>
        <Link to={`/listas/${id}/preferencias`} className="muted small">preferências desta lista</Link>
      </div>

      <ul className="items">
        {list.items?.map((it) => (
          <ItemRow key={it.id} item={it} onPatch={(p) => patch(it.id, p)} onDelete={() => remove(it.id)} />
        ))}
      </ul>

      <div className="add-item">
        <input placeholder="Adicionar item: “2 leite integral Italac”, “café 500g”…" value={text}
               onChange={(e) => setText(e.target.value)} onKeyDown={(e) => e.key === "Enter" && add()} />
        {preview && (
          <div className="preview">
            <Chips item={{ ...preview, recurrence_days: preview.recurrence_days ?? null }}
                   onPatch={(p) => setPreview({ ...preview, ...p } as Parsed)} />
            <button onClick={add}>Adicionar</button>
          </div>
        )}
      </div>

      <div className="cta">
        {list.last_plan_id && (
          <Link to={`/carrinho/${list.last_plan_id}`} className="muted small">
            último carrinho{list.last_total != null ? ` (${brl(list.last_total)})` : ""}
          </Link>
        )}
        <label className="deliver-at">
          Entrega
          <input type="datetime-local" step={3600} value={deliverAt} onChange={(e) => changeDeliverAt(e.target.value)} />
          {deliverAt ? <button className="link" onClick={() => changeDeliverAt("")}>o quanto antes</button>
                     : <span className="muted small">o quanto antes</span>}
        </label>
        <button className="primary big" onClick={createCart} disabled={starting || !list.items?.length}>
          {starting ? "Começando…" : "Criar carrinho"}
        </button>
        {error && <p className="error">{error}</p>}
      </div>
    </div>
  );
}
