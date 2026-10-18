import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { CalendarDays, Store } from "lucide-react";
import { ProposedRule, Rule, ShoppingList, UserView, api } from "../api";
import RuleCard, { RuleProposal } from "../components/RuleCard";
import { BottomNav, ErrorStrip, Header, ListSkeleton, PrimaryButton, useToast } from "../ui";

type Profile = Pick<UserView, "max_merchants" | "split_min_savings" | "today">;

// Perfil (global, com a barra de navegação) ou preferências de uma lista.
export default function Preferences() {
  const { id } = useParams();
  const listId = id ? Number(id) : undefined;
  const [tab, setTab] = useState<"global" | "list">(listId ? "list" : "global");
  const [rules, setRules] = useState<Rule[] | null>(null);
  const [profile, setProfile] = useState<Profile | null>(null);
  const [list, setList] = useState<ShoppingList | null>(null);
  const [failed, setFailed] = useState(false);
  const [text, setText] = useState("");
  const [category, setCategory] = useState("");
  const [proposal, setProposal] = useState<ProposedRule | null>(null);
  const [compiling, setCompiling] = useState(false);
  const [simulating, setSimulating] = useState(false);
  const toast = useToast();

  const load = () => {
    setFailed(false);
    Promise.all([
      api.get<Rule[]>("/rules").then(setRules),
      api.get<Profile>("/me").then(setProfile),
      listId ? api.get<ShoppingList>(`/lists/${listId}`).then(setList) : null,
    ]).catch(() => setFailed(true));
  };
  useEffect(load, [listId]);

  const shown = (rules ?? []).filter((r) => (tab === "global" ? r.scope === "global" : r.scope === String(listId)));
  const learned = shown.filter((r) => r.source === "learned");
  const mine = shown.filter((r) => r.source !== "learned");

  // na aba da lista, os controles sobrescrevem o perfil só para ela
  const settings = tab === "list" && list
    ? { max_merchants: list.max_merchants ?? profile?.max_merchants ?? 2,
        split_min_savings: list.split_min_savings ?? profile?.split_min_savings ?? null }
    : profile;

  const saveSetting = async (p: Partial<Profile>) => {
    if (tab === "list" && listId) setList(await api.patch<ShoppingList>(`/lists/${listId}`, p));
    else setProfile(await api.patch<Profile>("/me", p));
  };

  const compile = async () => {
    if (!text.trim()) return;
    setCompiling(true);
    try {
      setProposal(await api.post<ProposedRule>("/rules/compile", { text, category: category || undefined, list_id: listId }));
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setCompiling(false);
    }
  };

  const simulate = async () => {
    setSimulating(true);
    try {
      await api.post("/admin/simulate-days", { days: 7 });
      load();
      toast("Uma semana se passou no app");
    } finally {
      setSimulating(false);
    }
  };

  return (
    <div className="screen">
      {failed && <ErrorStrip onRetry={load} />}
      {listId
        ? <Header back={`/listas/${listId}`} title="Preferências" subtitle={list?.name} titleClass="t-title-lg" />
        : <Header title="Preferências" subtitle="Como o agente escolhe por você" />}

      <main className={`content${listId ? "" : " has-nav no-fab"}`} style={{ gap: 24 }}>
        {listId && (
          <div className="segmented" role="group" aria-label="Valem para">
            <button type="button" aria-pressed={tab === "global"} onClick={() => setTab("global")}>Todas as listas</button>
            <button type="button" aria-pressed={tab === "list"} onClick={() => setTab("list")}>Só esta lista</button>
          </div>
        )}

        {!settings && !failed && <ListSkeleton rows={2} height={120} />}
        {settings && (
          <section className="section">
            <h2 className="t-overline">Ao montar o carrinho</h2>
            <div className="card pad stack" style={{ gap: 20 }}>
              <fieldset style={{ border: 0, padding: 0, margin: 0 }}>
                <legend className="t-title-md" style={{ marginBottom: 10 }}>Comprar em até</legend>
                <div className="stack">
                  {[1, 2, 3].map((n) => (
                    <label key={n} className="option">
                      <span className="ico-box"><Store size={18} aria-hidden="true" /></span>
                      <span className="grow"><span className="t-body-strong">{n} mercado{n > 1 ? "s" : ""}</span></span>
                      <input type="radio" name="max-merchants" checked={settings.max_merchants === n}
                             onChange={() => saveSetting({ max_merchants: n })} />
                    </label>
                  ))}
                </div>
              </fieldset>

              <label className="row" style={{ justifyContent: "space-between" }}>
                <span className="t-title-md" style={{ flex: 1 }}>Dividir só se economizar mais de</span>
                <span className="row">
                  <span className="t-body">R$</span>
                  <input className="input inline" type="number" inputMode="decimal" min={0} step={1}
                         value={settings.split_min_savings ?? ""} placeholder="—"
                         onChange={(e) => saveSetting({ split_min_savings: e.target.value ? Number(e.target.value) : null })} />
                </span>
              </label>
            </div>
          </section>
        )}

        <section className="section">
          <h2 className="t-overline">Regras</h2>
          {rules === null && !failed && <ListSkeleton rows={2} height={72} />}
          {rules && mine.length === 0 && <p className="t-caption">Nenhuma regra aqui ainda.</p>}
          {mine.length > 0 && <ul className="card divided">{mine.map((r) => <RuleCard key={r.id} rule={r} onChange={load} />)}</ul>}
        </section>

        {learned.length > 0 && (
          <section className="section">
            <h2 className="t-overline">Aprendido com suas compras</h2>
            <ul className="card divided">{learned.map((r) => <RuleCard key={r.id} rule={r} onChange={load} />)}</ul>
          </section>
        )}

        <section className="section">
          <h2 className="t-overline">Nova preferência</h2>
          <div className="card pad field-group">
            <label>
              <span className="label-row">
                <span className="t-title-md">Em poucas palavras</span>
                {tab === "list" && <span className="t-small muted">vale só nesta lista</span>}
              </span>
              <textarea className="field" rows={3} value={text} onChange={(e) => setText(e.target.value)}
                        placeholder="“detergente sempre Ypê, senão Limpol”, “leite sem lactose”, “frete até R$ 10”" />
            </label>
            <label>
              <span className="t-small">Categoria (opcional)</span>
              <input className="input" placeholder="detergente, café…" value={category} onChange={(e) => setCategory(e.target.value)} />
            </label>
            <PrimaryButton loading={compiling} disabled={!text.trim()} onClick={compile}>Interpretar</PrimaryButton>
            {proposal && (
              <RuleProposal proposal={proposal} listId={listId}
                            onSaved={() => { setProposal(null); setText(""); load(); toast("Regra salva"); }}
                            onDiscard={() => setProposal(null)} />
            )}
          </div>
        </section>

        {!listId && profile && (
          <section className="section">
            <h2 className="t-overline">Demonstração</h2>
            <div className="card divided">
              <div className="info-row">
                <CalendarDays size={20} className="ico" aria-hidden="true" />
                <span className="grow">
                  <span className="t-small muted">Hoje no app</span>
                  <span className="t-body-strong">{new Date(profile.today + "T12:00").toLocaleDateString("pt-BR")}</span>
                </span>
                <button type="button" className="btn-secondary" onClick={simulate} disabled={simulating}>
                  {simulating ? "Simulando…" : "Simular uma semana"}
                </button>
              </div>
            </div>
          </section>
        )}
      </main>

      {!listId && <BottomNav />}
    </div>
  );
}
