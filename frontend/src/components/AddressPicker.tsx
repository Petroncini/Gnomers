import { useState } from "react";
import { api } from "../api";

interface Candidate {
  candidateId: string;
  formattedAddress: string;
  needsNeighborhood: boolean;
  distance_km: number | null;
}

const position = () => JSON.parse(sessionStorage.getItem("gnomers.pos") ?? "null") ?? {};

interface CreateResult {
  status: "created" | "confirm";
  address_id?: number | null;
  candidates?: Candidate[];
}

// Pede o endereço uma única vez e salva no iFood pelo createAddress em duas etapas.
export default function AddressPicker({ onDone, onCancel }: { onDone: (addressId: number | null) => void; onCancel?: () => void }) {
  const [text, setText] = useState("");
  const [alias, setAlias] = useState("Casa");
  const [complement, setComplement] = useState("");
  const [neighborhood, setNeighborhood] = useState("");
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [error, setError] = useState<string | null>(null);

  const geocode = async () => {
    setError(null);
    try {
      const r = await api.post<CreateResult>("/addresses", { address: text, ...position() });
      if (r.status === "created") return onDone(r.address_id ?? null);
      if (!r.candidates?.length) setError("Não encontrei esse endereço. Inclua a cidade.");
      setCandidates(r.candidates ?? []);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const [saving, setSaving] = useState(false);

  const confirm = async (c: Candidate) => {
    if (saving) return;  // um clique só: cada confirmação cria um endereço novo no iFood
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
    <div className="address-picker">
      <p>Novo endereço de entrega</p>
      <input placeholder="Rua, número, cidade" value={text} onChange={(e) => setText(e.target.value)} />
      {candidates.length === 0 ? (
        <button onClick={geocode} disabled={!text}>Procurar</button>
      ) : (
        <>
          <div className="row">
            <select value={alias} onChange={(e) => setAlias(e.target.value)} aria-label="Apelido">
              <option value="Casa">Casa</option>
              <option value="Trabalho">Trabalho</option>
              <option value="">Sem apelido</option>
            </select>
            <input placeholder="Complemento" value={complement} onChange={(e) => setComplement(e.target.value)} />
          </div>
          {candidates.some((c) => c.needsNeighborhood) && (
            <input placeholder="Bairro" value={neighborhood} onChange={(e) => setNeighborhood(e.target.value)} />
          )}
          {candidates.map((c) => (
            <button key={c.candidateId} className="ghost block" disabled={saving} onClick={() => confirm(c)}>
              {c.formattedAddress}
              {c.distance_km != null && <span className="muted small"> · {c.distance_km} km</span>}
            </button>
          ))}
        </>
      )}
      {candidates.length > 0 && <p className="muted small">Toque no endereço certo para salvar.</p>}
      {error && <p className="error">{error}</p>}
      {onCancel && <button className="link" onClick={onCancel}>Cancelar</button>}
    </div>
  );
}
