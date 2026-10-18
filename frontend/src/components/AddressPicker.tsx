import { useState } from "react";
import { MapPin } from "lucide-react";
import { AddressCandidate as Candidate, api, CreateAddressResult as CreateResult } from "../api";
import { PrimaryButton } from "../ui";

const position = () => {
  try {
    return JSON.parse(sessionStorage.getItem("gnomers.pos") ?? "null") ?? {};
  } catch {
    return {};
  }
};

const ALIASES = ["Casa", "Trabalho", ""];

// Pede o endereço uma única vez e salva no iFood pelo createAddress em duas etapas.
export default function AddressPicker({ onDone, onCancel }: { onDone: (addressId: number | null) => void; onCancel?: () => void }) {
  const [text, setText] = useState("");
  const [alias, setAlias] = useState("Casa");
  const [complement, setComplement] = useState("");
  const [neighborhood, setNeighborhood] = useState("");
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [searching, setSearching] = useState(false);
  const [saving, setSaving] = useState(false);

  const geocode = async () => {
    if (!text.trim()) return;
    setError(null);
    setSearching(true);
    try {
      const r = await api.post<CreateResult>("/addresses", { address: text, ...position() });
      if (r.status === "created") return onDone(r.address_id ?? null);
      if (!r.candidates?.length) setError("Não encontrei esse endereço. Inclua a cidade.");
      setCandidates(r.candidates ?? []);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSearching(false);
    }
  };

  const confirm = async (c: Candidate) => {
    if (saving) return;  // um toque só: cada confirmação cria um endereço novo no iFood
    setSaving(true);
    try {
      const r = await api.post<CreateResult>("/addresses", {
        address: text, candidate_id: c.candidateId, alias: alias || undefined, complement: complement || undefined,
        neighborhood: neighborhood || undefined,
      });
      if (r.status === "created") onDone(r.address_id ?? null);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="field-group">
      <label>
        <span className="label-row"><span className="t-title-md">Endereço</span></span>
        <input className="input" autoFocus placeholder="Rua, número, cidade" value={text} autoComplete="street-address"
               enterKeyHint="search" onChange={(e) => { setText(e.target.value); setCandidates([]); }}
               onKeyDown={(e) => e.key === "Enter" && geocode()} />
      </label>

      {candidates.length === 0 && (
        <PrimaryButton loading={searching} disabled={!text.trim()} onClick={geocode}>Procurar</PrimaryButton>
      )}

      {candidates.length > 0 && (
        <>
          <div className="stack">
            <span className="t-small" style={{ color: "var(--color-ink-2)" }}>Salvar como</span>
            <div className="segmented" role="group" aria-label="Apelido do endereço">
              {ALIASES.map((a) => (
                <button key={a || "none"} type="button" aria-pressed={alias === a} onClick={() => setAlias(a)}>
                  {a || "Sem apelido"}
                </button>
              ))}
            </div>
          </div>
          <label>
            <span className="t-small" style={{ display: "block", color: "var(--color-ink-2)", marginBottom: 6 }}>Complemento</span>
            <input className="input" placeholder="Apto, bloco…" value={complement} onChange={(e) => setComplement(e.target.value)} />
          </label>
          {candidates.some((c) => c.needsNeighborhood) && (
            <label>
              <span className="t-small" style={{ display: "block", color: "var(--color-ink-2)", marginBottom: 6 }}>Bairro</span>
              <input className="input" value={neighborhood} onChange={(e) => setNeighborhood(e.target.value)} />
            </label>
          )}
          <div className="stack">
            <span className="t-small" style={{ color: "var(--color-ink-2)" }}>Toque no endereço certo para salvar</span>
            {candidates.map((c) => (
              <button key={c.candidateId} type="button" className="option" disabled={saving} onClick={() => confirm(c)}>
                <span className="ico-box"><MapPin size={18} aria-hidden="true" /></span>
                <span className="grow">
                  <span className="t-body-strong">{c.formattedAddress}</span>
                  {c.distance_km != null && <span className="t-caption">{c.distance_km} km de você</span>}
                </span>
              </button>
            ))}
          </div>
        </>
      )}

      {error && <div className="notice" role="alert"><span className="grow">{error}</span></div>}
      {onCancel && <button type="button" className="btn-text" onClick={onCancel}>Voltar aos endereços salvos</button>}
    </div>
  );
}
