import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ChevronDown, ChevronRight, MapPin, Plus, ShoppingBasket } from "lucide-react";
import { AddressView, api, brl, getPosition, LocationResult, NearbyAddress, savedAddressId, saveAddressId, ShoppingList, Suggestion } from "../api";
import SuggestionCard from "../components/SuggestionCard";
import AddressPicker from "../components/AddressPicker";
import { BottomNav, ErrorStrip, Fab, ListSkeleton, PrimaryButton, Sheet, placeholderColor, useToast } from "../ui";

type SavedAddress = AddressView | NearbyAddress;

const label = (a: SavedAddress) => a.alias ?? a.formatted;

const daysUntil = (due: string, today: string) =>
  Math.round((new Date(due).getTime() - new Date(today).getTime()) / 86400000);

function whenLabel(days: number): string {
  if (days < 0) return `Atrasada há ${-days} dia${days === -1 ? "" : "s"}`;
  if (days === 0) return "Hoje";
  if (days === 1) return "Amanhã";
  return `Em ${days} dias`;
}

const recurrence = (l: ShoppingList) => (l.recurrence_days ? `a cada ${l.recurrence_days} dias` : "sem recorrência");

export default function Home() {
  const [lists, setLists] = useState<ShoppingList[] | null>(null);
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [failed, setFailed] = useState(false);
  const [addresses, setAddresses] = useState<SavedAddress[]>([]);
  const [current, setCurrent] = useState<SavedAddress | null>(null);
  const [detecting, setDetecting] = useState(true);
  const [sheet, setSheet] = useState<null | "address" | "new-address" | "new-list">(null);
  const [today, setToday] = useState(new Date().toISOString().slice(0, 10));
  const [newName, setNewName] = useState("");
  const [creating, setCreating] = useState(false);
  const navigate = useNavigate();
  const toast = useToast();

  const load = () => {
    setFailed(false);
    Promise.all([
      api.get<ShoppingList[]>("/lists").then(setLists),
      api.get<Suggestion[]>("/suggestions").then(setSuggestions),
      api.get<{ today: string }>("/me").then((m) => setToday(m.today)),
    ]).catch(() => setFailed(true));
  };

  const choose = (a: SavedAddress) => {
    saveAddressId(a.id);
    setCurrent(a);
  };

  // endereço: o que o usuário já escolheu vale; senão o GPS sugere; senão ele escolhe entre os salvos
  useEffect(() => {
    load();
    (async () => {
      const saved = await api.get<AddressView[]>("/addresses").catch(() => [] as AddressView[]);
      setAddresses(saved);
      const stored = saved.find((a) => a.id === savedAddressId());
      if (stored) return (choose(stored), setDetecting(false));
      const pos = await getPosition();
      if (pos) {
        try { sessionStorage.setItem("gnomers.pos", JSON.stringify(pos)); } catch { /* opcional */ }
        const r = await api.post<LocationResult>("/location", pos).catch(() => null);
        if (r?.address) return (choose(r.address), setDetecting(false));
        if (r?.nearby.length) setAddresses(r.nearby);
      }
      setDetecting(false);
    })();
  }, []);

  const created = async (id: number | null) => {
    const saved = await api.get<AddressView[]>("/addresses");
    setAddresses(saved);
    const a = saved.find((x) => x.id === id);
    if (a) {
      choose(a);
      setSheet(null);
      toast(`Entregando em ${label(a)}`);
    } else setSheet("address");
  };

  const createList = async () => {
    setCreating(true);
    try {
      const lst = await api.post<ShoppingList>("/lists", { name: newName.trim() || "Nova lista" });
      navigate(`/listas/${lst.id}`);
    } catch (e) {
      toast((e as Error).message);
      setCreating(false);
    }
  };

  const next = (lists ?? [])
    .filter((l) => l.next_due)
    .sort((a, b) => a.next_due!.localeCompare(b.next_due!))[0];

  return (
    <div className="screen">
      {failed && <ErrorStrip onRetry={load} />}
      <header className="header">
        <Link to="/" className="brand" aria-label="Cesta, início">
          <span className="logo"><ShoppingBasket size={20} strokeWidth={2.4} aria-hidden="true" /></span>
          <span>Cesta</span>
        </Link>
      </header>

      <main className="content has-nav">
        <div className="hero-head">
          <button type="button" className="address-chip" onClick={() => setSheet(addresses.length ? "address" : "new-address")}
                  aria-label={current ? `Endereço de entrega: ${label(current)}. Trocar` : "Escolher endereço de entrega"}>
            <MapPin size={16} strokeWidth={2.2} aria-hidden="true" />
            <span>{detecting ? "Detectando seu endereço…" : current ? label(current) : "Escolher endereço"}</span>
            <ChevronDown size={16} strokeWidth={2.2} aria-hidden="true" />
          </button>
          <h1 className="t-display">O que vamos comprar hoje?</h1>
        </div>

        {!detecting && !current && (
          <div className="notice">
            <MapPin size={18} className="ico" aria-hidden="true" />
            <div className="grow">
              <span>Diga onde entregar para o agente procurar mercados perto de você.</span>
              <button type="button" className="btn-text" onClick={() => setSheet(addresses.length ? "address" : "new-address")}>
                Escolher endereço
              </button>
            </div>
          </div>
        )}

        {suggestions.length > 0 && (
          <section className="stack" aria-label="Sugestões">
            {suggestions.map((s) => <SuggestionCard key={s.id} suggestion={s} onChange={load} />)}
          </section>
        )}

        {lists === null && !failed && <ListSkeleton rows={3} />}

        {next && (
          <Link to={`/listas/${next.id}`} className="next-card">
            <div className="top">
              <div className="stack" style={{ gap: 6 }}>
                <span className="t-overline">Próxima compra</span>
                <span className="t-title-lg">{next.name}</span>
                <span className="sub">{recurrence(next)}</span>
              </div>
              <span className="when">{whenLabel(daysUntil(next.next_due!, today))}</span>
            </div>
            <div className="foot">
              <span>{next.item_count} {next.item_count === 1 ? "item" : "itens"}</span>
              {next.last_total != null && <span>Último total {brl(next.last_total)}</span>}
            </div>
          </Link>
        )}

        {lists && lists.length > 0 && (
          <section className="section">
            <h2 className="t-overline">Suas listas</h2>
            <ul className="card divided">
              {lists.map((l) => (
                <li key={l.id}>
                  <Link to={`/listas/${l.id}`} className="list-card">
                    <span className="thumb" style={{ width: 48, height: 48, background: placeholderColor(l.name) }} aria-hidden="true">
                      <ShoppingBasket size={22} strokeWidth={2} />
                    </span>
                    <span className="grow">
                      <span className="t-body-strong">{l.name}</span>
                      <span className="t-caption">{l.item_count} {l.item_count === 1 ? "item" : "itens"} · {recurrence(l)}</span>
                    </span>
                    <ChevronRight size={18} className="chev" aria-hidden="true" />
                  </Link>
                </li>
              ))}
            </ul>
          </section>
        )}

        {lists && lists.length === 0 && (
          <div className="empty">
            <span className="art"><ShoppingBasket size={44} strokeWidth={1.8} aria-hidden="true" /></span>
            <h2 className="t-title-lg">Crie sua primeira lista</h2>
            <p className="t-caption">Anote o que você costuma comprar. O agente monta o carrinho no iFood com o melhor preço.</p>
            <PrimaryButton onClick={() => setSheet("new-list")}>Criar lista</PrimaryButton>
          </div>
        )}
      </main>

      {lists && lists.length > 0 && (
        <Fab aboveNav icon={<Plus size={20} strokeWidth={2.4} aria-hidden="true" />} onClick={() => setSheet("new-list")}>
          Nova lista
        </Fab>
      )}
      <BottomNav />

      {sheet === "new-list" && (
        <Sheet title="Nova lista" onClose={() => setSheet(null)}
               footer={<PrimaryButton loading={creating} onClick={createList}>Criar lista</PrimaryButton>}>
          <label>
            <span className="label-row"><span className="t-title-md">Nome da lista</span></span>
            <input className="input" autoFocus placeholder="Compra do mês" value={newName} enterKeyHint="done"
                   onChange={(e) => setNewName(e.target.value)} onKeyDown={(e) => e.key === "Enter" && createList()} />
          </label>
        </Sheet>
      )}

      {sheet === "address" && (
        <Sheet title="Onde entregar?" onClose={() => setSheet(null)}>
          <div className="stack" role="radiogroup" aria-label="Endereços salvos">
            {addresses.map((a) => (
              <label key={a.id} className="option">
                <span className="ico-box"><MapPin size={18} aria-hidden="true" /></span>
                <span className="grow">
                  <span className="t-body-strong">{a.alias ?? "Sem apelido"}</span>
                  <span className="t-caption">{a.formatted}</span>
                </span>
                <input type="radio" name="address" checked={current?.id === a.id}
                       onChange={() => { choose(a); setSheet(null); toast(`Entregando em ${label(a)}`); }} />
              </label>
            ))}
          </div>
          <button type="button" className="add-dashed" onClick={() => setSheet("new-address")}>
            <Plus size={18} strokeWidth={2.4} aria-hidden="true" />Novo endereço
          </button>
        </Sheet>
      )}

      {sheet === "new-address" && (
        <Sheet title="Novo endereço" onClose={() => setSheet(null)}>
          <AddressPicker onDone={created} onCancel={addresses.length ? () => setSheet("address") : undefined} />
        </Sheet>
      )}
    </div>
  );
}
