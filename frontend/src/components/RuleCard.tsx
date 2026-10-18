import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Trash2 } from "lucide-react";
import { api, PlanCreated, ProposedRule, Rule } from "../api";
import { IconButton, Selo, ruleBody, ruleSelos } from "../ui";

const SOURCE_LABEL: Record<string, string> = { user: "Você", chat: "Pelo chat", learned: "Aprendido" };

export default function RuleCard({ rule, onChange }: { rule: Rule; onChange: () => void }) {
  const toggle = async () => {
    await api.patch(`/rules/${rule.id}`, { active: !rule.active });
    onChange();
  };
  const remove = async () => {
    await api.del(`/rules/${rule.id}`);
    onChange();
  };
  const switchId = `rule-${rule.id}`;
  return (
    <li className="info-row" style={{ alignItems: "flex-start", opacity: rule.active ? 1 : 0.6 }}>
      <div className="grow" style={{ gap: 6, paddingTop: 4 }}>
        <span className="row wrap" style={{ gap: 6 }}>
          {ruleSelos(rule).slice(0, 1).map((u, i) => <Selo key={i} kind={u.kind} />)}
          {rule.category && <span className="t-small muted">{rule.category}</span>}
        </span>
        <label htmlFor={switchId} className="t-body-strong">{ruleBody(rule)}</label>
        <span className="t-caption">{SOURCE_LABEL[rule.source] ?? rule.source}</span>
      </div>
      <label className="switch">
        <input id={switchId} type="checkbox" role="switch" checked={rule.active} onChange={toggle}
               aria-label={rule.active ? "Regra ligada" : "Regra desligada"} />
        <span />
      </label>
      <IconButton label="Apagar regra" ghost onClick={remove}><Trash2 size={18} aria-hidden="true" /></IconButton>
    </li>
  );
}

// Proposta de regra vinda do chat ou do texto livre, com o seletor de escopo.
// No chat de um carrinho (planId), salvar também refaz o carrinho com a regra nova, se o usuário quiser.
export function RuleProposal({ proposal, listId, planId, onSaved, onDiscard }: {
  proposal: ProposedRule; listId?: number; planId?: number; onSaved: (r: Rule) => void; onDiscard: () => void;
}) {
  const [replan, setReplan] = useState(true);
  const [busy, setBusy] = useState(false);
  const navigate = useNavigate();

  const save = async (scope: string) => {
    setBusy(true);
    try {
      const saved = await api.post<Rule>("/rules", {
        kind: proposal.kind, category: proposal.category, payload: proposal.payload, hardness: proposal.hardness,
        scope, source: "chat",
      });
      onSaved(saved);
      if (planId && replan) {
        const next = await api.post<PlanCreated>(`/plans/${planId}/replan`);
        navigate(`/carrinho/${next.id}`, { state: { chat: true } });
      }
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="rule-proposal">
      <span className="t-small" style={{ color: "var(--color-primary-strong)" }}>Salvar como regra?</span>
      <span className="row" style={{ gap: 8, alignItems: "flex-start" }}>
        {ruleSelos(proposal).slice(0, 1).map((u, i) => <Selo key={i} kind={u.kind} />)}
        <strong className="t-body-strong">{proposal.text}</strong>
      </span>
      {planId && (
        <label className="row t-small" style={{ gap: 8 }}>
          <input type="checkbox" checked={replan} onChange={(e) => setReplan(e.target.checked)} />
          e refazer o carrinho agora
        </label>
      )}
      <div className="row wrap">
        <button type="button" className="btn-secondary" disabled={busy} onClick={() => save("global")}>Todas as listas</button>
        {listId && <button type="button" className="btn-secondary" disabled={busy} onClick={() => save(String(listId))}>Só esta lista</button>}
        <button type="button" className="btn-text" disabled={busy} onClick={onDiscard}>Não salvar</button>
      </div>
    </div>
  );
}
