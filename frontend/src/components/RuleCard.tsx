import { api, ProposedRule, Rule } from "../api";

const SOURCE_LABEL: Record<Rule["source"], string> = { user: "você", chat: "chat", learned: "aprendido" };

export default function RuleCard({ rule, onChange }: { rule: Rule; onChange: () => void }) {
  const toggle = async () => {
    await api.patch(`/rules/${rule.id}`, { active: !rule.active });
    onChange();
  };
  const remove = async () => {
    await api.del(`/rules/${rule.id}`);
    onChange();
  };
  return (
    <div className={`rule-card ${rule.active ? "" : "off"}`}>
      <div>
        <p>{rule.text}</p>
        <span className={`badge ${rule.source}`}>{SOURCE_LABEL[rule.source]}</span>
        <span className="badge plain">{rule.hardness === "hard" ? "obrigatória" : "preferência"}</span>
      </div>
      <div className="rule-actions">
        <label className="switch" aria-label={rule.active ? "Desligar regra" : "Ligar regra"}>
          <input type="checkbox" checked={rule.active} onChange={toggle} />
          <span />
        </label>
        <button className="icon-btn" aria-label="Apagar regra" onClick={remove}>×</button>
      </div>
    </div>
  );
}

// Proposta de regra vinda do chat ou do texto livre, com o seletor de escopo.
export function RuleProposal({ proposal, listId, onSaved, onDiscard }: {
  proposal: ProposedRule; listId?: number; onSaved: (r: Rule) => void; onDiscard: () => void;
}) {
  const save = async (scope: string) => {
    const saved = await api.post<Rule>("/rules", {
      kind: proposal.kind, category: proposal.category, payload: proposal.payload, hardness: proposal.hardness,
      scope, source: "chat",
    });
    onSaved(saved);
  };
  return (
    <div className="rule-proposal">
      <p>Salvar como regra? <strong>{proposal.text}</strong></p>
      <div className="actions">
        <button onClick={() => save("global")}>Todas as listas</button>
        {listId && <button onClick={() => save(String(listId))}>Só esta lista</button>}
        <button className="ghost" onClick={onDiscard}>Não salvar</button>
      </div>
    </div>
  );
}
