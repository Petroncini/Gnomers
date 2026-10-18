import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Percent, RefreshCw } from "lucide-react";
import { api, planTarget, Suggestion } from "../api";
import { PrimaryButton, useToast } from "../ui";

export default function SuggestionCard({ suggestion, onChange }: { suggestion: Suggestion; onChange: () => void }) {
  const [busy, setBusy] = useState(false);
  const navigate = useNavigate();
  const toast = useToast();
  const promo = suggestion.kind === "promo";

  const dismiss = async () => {
    await api.post(`/suggestions/${suggestion.id}/dismiss`);
    onChange();
  };

  const act = async () => {
    if (!suggestion.list_id) return;
    setBusy(true);
    try {
      const plan = await api.post<{ id: number }>(`/lists/${suggestion.list_id}/plan`, planTarget());
      await api.post(`/suggestions/${suggestion.id}/dismiss`);
      navigate(`/carrinho/${plan.id}`);
    } catch (e) {
      toast((e as Error).message);
      setBusy(false);
    }
  };

  const Icon = promo ? Percent : RefreshCw;
  return (
    <article className="card pad suggestion">
      <div className="top">
        <span className="ico"><Icon size={18} strokeWidth={2.2} aria-hidden="true" /></span>
        <p className="t-body">{suggestion.text}</p>
      </div>
      <div className="actions">
        <button type="button" className="btn-secondary" onClick={dismiss} disabled={busy}>Agora não</button>
        {suggestion.list_id && (
          <PrimaryButton loading={busy} onClick={act}>{promo ? "Adiantar" : "Montar carrinho"}</PrimaryButton>
        )}
      </div>
    </article>
  );
}
