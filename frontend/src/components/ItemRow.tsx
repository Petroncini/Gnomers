import { Link } from "react-router-dom";
import { ChevronRight } from "lucide-react";
import { ListItem, Rule, Unit } from "../api";
import { RestrictionTag, Thumb, Understanding, ruleSelos } from "../ui";

export type ItemPatch = Partial<Pick<ListItem, "quantity" | "brand" | "variety_terms" | "pack_size" | "pack_unit" | "recurrence_days" | "raw_text">>;

type ItemFacts = Pick<ListItem, "brand" | "variety_terms" | "pack_size" | "pack_unit" | "recurrence_days" | "category">;

export function packLabel(size: number | null, unit: Unit | null): string | null {
  if (!size || !unit) return null;
  if (unit === "g") return size >= 1000 ? `${size / 1000} kg` : `${size} g`;
  if (unit === "ml") return size >= 1000 ? `${size / 1000} L` : `${size} ml`;
  return `${size} un`;
}

export const qtyLabel = (q: number) => `${q.toLocaleString("pt-BR")}×`;

// A quantidade já aparece à direita: tira o "2 " ou "2x " do começo do texto digitado.
export function displayName(rawText: string): string {
  const unit = /^(?:kg|g|mg|ml|l|lt|litros?|gramas?|quilos?)$/i;
  const m = rawText.match(/^\s*\d+(?:[.,]\d+)?\s*(?:x|×|un|unid(?:ades?)?)?\s+(?=\S)/i);
  const rest = m ? rawText.slice(m[0].length) : rawText;
  const name = (m && !unit.test(rest.split(/\s/)[0]) ? rest : rawText).trim();
  return name.charAt(0).toUpperCase() + name.slice(1);
}

// Regras ligadas que valem para a categoria do item nesta lista (globais ou só dela).
export function rulesFor(rules: Rule[], category: string, listId: number): Rule[] {
  return rules.filter((r) => r.active && r.category === category && (r.scope === "global" || r.scope === String(listId)));
}

export function hasRestriction(item: ItemFacts, rules: Rule[]): boolean {
  return !!item.brand || item.variety_terms.length > 0 || rules.length > 0;
}

// "Como o agente entendeu": a marca e a variedade do próprio item, depois as regras da categoria.
export function understanding(item: ItemFacts, rules: Rule[]): Understanding[] {
  const out: Understanding[] = [];
  if (item.brand) out.push({ kind: "prefere", text: `marca ${item.brand}` });
  if (item.variety_terms.length) out.push({ kind: "prefere", text: item.variety_terms.join(", ") });
  for (const r of rules) out.push(...ruleSelos(r));
  // a marca do item e uma regra aprendida costumam dizer a mesma coisa: mostra uma vez só
  const seen = new Set<string>();
  return out.filter((u) => {
    const key = `${u.kind}|${u.text.toLocaleLowerCase("pt-BR")}`;
    return !seen.has(key) && !!seen.add(key);
  });
}

export function factsLine(item: ItemFacts): string | null {
  const parts = [];
  const pack = packLabel(item.pack_size, item.pack_unit);
  if (pack) parts.push(`embalagem de ${pack}`);
  if (item.recurrence_days) parts.push(`repete a cada ${item.recurrence_days} dias`);
  return parts.length ? parts.join(" · ").replace(/^./, (c) => c.toUpperCase()) : null;
}

export default function ItemRow({ item, to, restricted }: { item: ListItem; to: string; restricted: boolean }) {
  return (
    <li>
      <Link to={to} className="item-row">
        <Thumb category={item.category} />
        <span className="grow">
          <span className="t-body-strong">{displayName(item.raw_text)}</span>
          {restricted && <RestrictionTag />}
        </span>
        <span className="qty" aria-label={`quantidade ${item.quantity}`}>{qtyLabel(item.quantity)}</span>
        <ChevronRight size={16} className="chev" aria-hidden="true" />
      </Link>
    </li>
  );
}
