import { useNavigate } from "react-router-dom";
import { api, planTarget, Suggestion } from "../api";

export default function SuggestionCard({ suggestion, onChange }: { suggestion: Suggestion; onChange: () => void }) {
  const navigate = useNavigate();

  const dismiss = async () => {
    await api.post(`/suggestions/${suggestion.id}/dismiss`);
    onChange();
  };

  const act = async () => {
    if (!suggestion.list_id) return;
    const plan = await api.post<{ id: number }>(`/lists/${suggestion.list_id}/plan`, planTarget());
    await api.post(`/suggestions/${suggestion.id}/dismiss`);
    navigate(`/carrinho/${plan.id}`);
  };

  return (
    <div className={`suggestion ${suggestion.kind}`}>
      <span className="icon" aria-hidden>{suggestion.kind === "promo" ? "%" : "↻"}</span>
      <p>{suggestion.text}</p>
      <div className="actions">
        <button onClick={act}>{suggestion.kind === "promo" ? "Adiantar" : "Montar carrinho"}</button>
        <button className="ghost" onClick={dismiss}>Agora não</button>
      </div>
    </div>
  );
}
