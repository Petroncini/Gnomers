import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, brl, getPosition, savedAddressId, saveAddressId, ShoppingList, Suggestion } from "../api";
import SuggestionCard from "../components/SuggestionCard";
import AddressPicker from "../components/AddressPicker";

interface SavedAddress {
  id: number;
  alias: string | null;
  formatted: string;
  distance_m?: number;
}

interface LocationResult {
  address: SavedAddress | null;
  nearby: SavedAddress[];
  needs_choice: boolean;
  needs_address: boolean;
}

const label = (a: SavedAddress) => a.alias ?? a.formatted;

function dueLabel(due: string | null, today: string): string | null {
  if (!due) return null;
  const days = Math.round((new Date(due).getTime() - new Date(today).getTime()) / 86400000);
  if (days < 0) return `venceu há ${-days} dia${days === -1 ? "" : "s"}`;
  if (days === 0) return "vence hoje";
  if (days === 1) return "vence amanhã";
  return `vence em ${days} dias`;
}

export default function Home() {
  const [lists, setLists] = useState<ShoppingList[]>([]);
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [addresses, setAddresses] = useState<SavedAddress[]>([]);
  const [current, setCurrent] = useState<SavedAddress | null>(null);
  const [mode, setMode] = useState<"loading" | "chosen" | "choose" | "new">("loading");
  const [today, setToday] = useState(new Date().toISOString().slice(0, 10));
  const [newName, setNewName] = useState("");
  const [busy, setBusy] = useState(false);
  const navigate = useNavigate();

  const load = () => {
    api.get<ShoppingList[]>("/lists").then(setLists);
    api.get<Suggestion[]>("/suggestions").then(setSuggestions);
    api.get<{ today: string }>("/me").then((m) => setToday(m.today));
  };

  const choose = (a: SavedAddress) => {
    saveAddressId(a.id);
    setCurrent(a);
    setMode("chosen");
  };

  // endereço: o que o usuário já escolheu vale; senão o GPS sugere; senão ele escolhe entre os salvos
  useEffect(() => {
    load();
    (async () => {
      const saved = await api.get<SavedAddress[]>("/addresses").catch(() => [] as SavedAddress[]);
      setAddresses(saved);
      const stored = saved.find((a) => a.id === savedAddressId());
      if (stored) return choose(stored);
      const pos = await getPosition();
      if (pos) {
        sessionStorage.setItem("gnomers.pos", JSON.stringify(pos));
        const r = await api.post<LocationResult>("/location", pos).catch(() => null);
        if (r?.address) return choose(r.address);
        if (r?.nearby.length) setAddresses(r.nearby);
      }
      setMode(saved.length ? "choose" : "new");
    })();
  }, []);

  const created = async (id: number | null) => {
    const saved = await api.get<SavedAddress[]>("/addresses");
    setAddresses(saved);
    const a = saved.find((x) => x.id === id);
    if (a) choose(a);
    else setMode(saved.length ? "choose" : "new");
  };

  const createList = async () => {
    const lst = await api.post<ShoppingList>("/lists", { name: newName || "Nova lista" });
    navigate(`/listas/${lst.id}`);
  };

  const simulate = async () => {
    setBusy(true);
    try {
      await api.post("/admin/simulate-days", { days: 7 });
      load();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="page">
      <section className="location">
        {mode === "loading" && <p className="muted">Detectando seu endereço…</p>}
        {mode === "chosen" && current && (
          <p>
            Entregando em <strong>{label(current)}</strong>{" "}
            <button className="link" onClick={() => setMode("choose")}>trocar</button>
          </p>
        )}
        {mode === "choose" && (
          <div className="address-choice">
            <p>Onde entregar?</p>
            {addresses.map((a) => (
              <button key={a.id} className="ghost block" onClick={() => choose(a)}>
                <strong>{a.alias ?? "Sem apelido"}</strong> <span className="muted small">{a.formatted}</span>
              </button>
            ))}
            <button className="link" onClick={() => setMode("new")}>+ novo endereço</button>
          </div>
        )}
        {mode === "new" && (
          <AddressPicker onDone={created} onCancel={addresses.length ? () => setMode("choose") : undefined} />
        )}
      </section>

      {suggestions.length > 0 && (
        <section className="suggestions">
          {suggestions.map((s) => (
            <SuggestionCard key={s.id} suggestion={s} onChange={load} />
          ))}
        </section>
      )}

      <h1>Suas listas</h1>
      <ul className="lists">
        {lists.map((l) => (
          <li key={l.id}>
            <Link to={`/listas/${l.id}`} className="list-card">
              <div>
                <strong>{l.name}</strong>
                <span className="muted">
                  {l.item_count} itens
                  {l.recurrence_days ? ` · a cada ${l.recurrence_days} dias` : ""}
                </span>
              </div>
              <div className="right">
                {dueLabel(l.next_due, today) && <span className="chip due">{dueLabel(l.next_due, today)}</span>}
                {l.last_total != null && <span className="muted">último {brl(l.last_total)}</span>}
              </div>
            </Link>
          </li>
        ))}
      </ul>

      <div className="new-list">
        <input placeholder="Nome da nova lista" value={newName} onChange={(e) => setNewName(e.target.value)}
               onKeyDown={(e) => e.key === "Enter" && createList()} />
        <button onClick={createList}>Criar lista</button>
      </div>

      <footer className="demo-tools">
        <button className="ghost" onClick={simulate} disabled={busy}>
          {busy ? "Simulando…" : "Simular uma semana"}
        </button>
        <span className="muted">hoje no app: {new Date(today + "T12:00").toLocaleDateString("pt-BR")}</span>
      </footer>
    </div>
  );
}
