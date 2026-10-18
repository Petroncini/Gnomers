import { useState } from "react";
import { ListItem, Unit } from "../api";

export type ItemPatch = Partial<Pick<ListItem, "quantity" | "brand" | "variety_terms" | "pack_size" | "pack_unit" | "recurrence_days" | "raw_text">>;

export function packLabel(size: number | null, unit: Unit | null): string | null {
  if (!size || !unit) return null;
  if (unit === "g") return size >= 1000 ? `${size / 1000} kg` : `${size} g`;
  if (unit === "ml") return size >= 1000 ? `${size / 1000} L` : `${size} ml`;
  return `${size} un`;
}

// Interpretação do item em chips editáveis: quantidade, marca, variedade, embalagem e recorrência.
export function Chips({ item, onPatch }: { item: Pick<ListItem, "quantity" | "brand" | "variety_terms" | "pack_size" | "pack_unit" | "recurrence_days">; onPatch?: (p: ItemPatch) => void }) {
  const [editing, setEditing] = useState<string | null>(null);
  const [value, setValue] = useState("");

  const start = (field: string, current: string) => {
    if (!onPatch) return;
    setEditing(field);
    setValue(current);
  };

  const commit = () => {
    if (!onPatch || !editing) return;
    const v = value.trim();
    if (editing === "quantity") onPatch({ quantity: Math.max(1, Number(v.replace(",", ".")) || 1) });
    if (editing === "brand") onPatch({ brand: v || null });
    if (editing === "variety") onPatch({ variety_terms: v ? v.split(",").map((x) => x.trim()).filter(Boolean) : [] });
    if (editing === "recurrence") onPatch({ recurrence_days: v ? Number(v) || null : null });
    setEditing(null);
  };

  const chip = (field: string, label: string, current: string, kind = "") =>
    editing === field ? (
      <input key={field} className="chip-input" autoFocus value={value} onChange={(e) => setValue(e.target.value)}
             onBlur={commit} onKeyDown={(e) => e.key === "Enter" && commit()} />
    ) : (
      <button key={field} type="button" className={`chip ${kind}`} onClick={() => start(field, current)}>{label}</button>
    );

  const pack = packLabel(item.pack_size, item.pack_unit);
  return (
    <span className="chips">
      {chip("quantity", `${item.quantity}×`, String(item.quantity))}
      {chip("brand", item.brand ?? "qualquer marca", item.brand ?? "", item.brand ? "brand" : "empty")}
      {item.variety_terms.length > 0
        ? chip("variety", item.variety_terms.join(", "), item.variety_terms.join(", "), "variety")
        : onPatch && chip("variety", "+ variedade", "", "empty")}
      {pack && <span className="chip pack">{pack}</span>}
      {item.recurrence_days
        ? chip("recurrence", `a cada ${item.recurrence_days} d`, String(item.recurrence_days), "rec")
        : onPatch && chip("recurrence", "+ recorrência", "", "empty")}
    </span>
  );
}

export default function ItemRow({ item, onPatch, onDelete }: { item: ListItem; onPatch: (p: ItemPatch) => void; onDelete: () => void }) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(item.raw_text);

  return (
    <li className="item-row">
      {editing ? (
        <input className="item-text" autoFocus value={text} onChange={(e) => setText(e.target.value)}
               onBlur={() => { setEditing(false); if (text !== item.raw_text) onPatch({ raw_text: text }); }}
               onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()} />
      ) : (
        <button type="button" className="item-text" onClick={() => setEditing(true)}>{item.raw_text}</button>
      )}
      <Chips item={item} onPatch={onPatch} />
      <button type="button" className="icon-btn" aria-label="Remover item" onClick={onDelete}>×</button>
    </li>
  );
}
