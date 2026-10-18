import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ProposedRule, Rule, ShoppingList, UserView, api } from "../api";
import RuleCard, { RuleProposal } from "../components/RuleCard";

type Profile = Pick<UserView, "quality_profile" | "max_merchants" | "split_min_savings">;

export default function Preferences() {
  const { id } = useParams();
  const listId = id ? Number(id) : undefined;
  const [tab, setTab] = useState<"global" | "list">(listId ? "list" : "global");
  const [rules, setRules] = useState<Rule[]>([]);
  const [profile, setProfile] = useState<Profile | null>(null);
  const [list, setList] = useState<ShoppingList | null>(null);
  const [text, setText] = useState("");
  const [category, setCategory] = useState("");
  const [proposal, setProposal] = useState<ProposedRule | null>(null);

  const load = () => {
    api.get<Rule[]>("/rules").then(setRules);
    api.get<Profile>("/me").then(setProfile);
    if (listId) api.get<ShoppingList>(`/lists/${listId}`).then(setList);
  };
  useEffect(load, [listId]);

  const shown = rules.filter((r) => (tab === "global" ? r.scope === "global" : r.scope === String(listId)));
  const learned = shown.filter((r) => r.source === "learned");
  const mine = shown.filter((r) => r.source !== "learned");

  // na aba da lista, os controles sobrescrevem o perfil só para ela
  const settings = tab === "list" && list
    ? { quality_profile: list.quality_profile ?? profile?.quality_profile ?? 0.5,
        max_merchants: list.max_merchants ?? profile?.max_merchants ?? 2,
        split_min_savings: list.split_min_savings ?? profile?.split_min_savings ?? null }
    : profile;

  const saveSetting = async (p: Partial<Profile>) => {
    if (tab === "list" && listId) setList(await api.patch<ShoppingList>(`/lists/${listId}`, p));
    else setProfile(await api.patch<Profile>("/me", p));
  };

  const compile = async () => {
    if (!text.trim()) return;
    setProposal(await api.post<ProposedRule>("/rules/compile", { text, category: category || undefined, list_id: listId }));
  };

  return (
    <div className="page">
      {listId && <Link to={`/listas/${listId}`} className="muted small">← voltar para {list?.name ?? "a lista"}</Link>}
      <h1>Preferências</h1>
      <div className="tabs">
        <button className={tab === "global" ? "active" : ""} onClick={() => setTab("global")}>Global</button>
        {listId && <button className={tab === "list" ? "active" : ""} onClick={() => setTab("list")}>Esta lista</button>}
      </div>

      {settings && (
        <section className="settings">
          <label className="slider">
            <span>Economia</span>
            <input type="range" min={0} max={1} step={0.1} value={settings.quality_profile}
                   onChange={(e) => saveSetting({ quality_profile: Number(e.target.value) })} />
            <span>Qualidade</span>
          </label>
          <label>
            Comprar em até
            <select value={settings.max_merchants} onChange={(e) => saveSetting({ max_merchants: Number(e.target.value) })}>
              {[1, 2, 3].map((n) => <option key={n} value={n}>{n} mercado{n > 1 ? "s" : ""}</option>)}
            </select>
          </label>
          <label>
            Dividir só se economizar mais de R$
            <input type="number" min={0} step={1} value={settings.split_min_savings ?? ""}
                   onChange={(e) => saveSetting({ split_min_savings: e.target.value ? Number(e.target.value) : null })} />
          </label>
        </section>
      )}

      <section>
        <h2>Regras</h2>
        {mine.length === 0 && <p className="muted">Nenhuma regra aqui ainda.</p>}
        {mine.map((r) => <RuleCard key={r.id} rule={r} onChange={load} />)}
      </section>

      {learned.length > 0 && (
        <section>
          <h2>Aprendido com suas compras</h2>
          {learned.map((r) => <RuleCard key={r.id} rule={r} onChange={load} />)}
        </section>
      )}

      <section className="new-rule">
        <h2>Nova preferência</h2>
        <div className="row">
          <input placeholder="Categoria (opcional): detergente, café…" value={category} onChange={(e) => setCategory(e.target.value)} />
        </div>
        <textarea placeholder="Ex.: “detergente sempre Ypê, senão Limpol”, “leite sem lactose”, “frete até R$ 10”"
                  value={text} onChange={(e) => setText(e.target.value)} />
        <button onClick={compile}>Interpretar</button>
        {proposal && (
          <RuleProposal proposal={proposal} listId={listId}
                        onSaved={() => { setProposal(null); setText(""); load(); }}
                        onDiscard={() => setProposal(null)} />
        )}
      </section>
    </div>
  );
}
