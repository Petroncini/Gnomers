// Peças visuais compartilhadas pelas telas (spec-frontend-cesta.md, seção 4).

import { ButtonHTMLAttributes, ReactNode, createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Link, NavLink } from "react-router-dom";
import {
  AlignLeft, Apple, Beef, Beer, Candy, ChevronLeft, CircleCheck, Coffee, Cookie, Croissant, CupSoda, Drumstick, Egg,
  Fish, GlassWater, House, LucideIcon, Milk, ShoppingBasket, SprayCan, UserRound, Wheat, Wine, X,
} from "lucide-react";
import { Rule } from "./api";

// ---------- cores e ícones de placeholder ----------

const PLACEHOLDERS = ["#F3EBDD", "#E6EEF6", "#F6E6E3", "#E9F1E4", "#EAE7F3", "#EEECE8"];

const fold = (s: string) => s.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();

// mesma categoria, mesma cor: o hash é estável entre renders e entre telas
export function placeholderColor(key: string): string {
  let h = 0;
  for (const ch of fold(key)) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return PLACEHOLDERS[h % PLACEHOLDERS.length];
}

const CATEGORY_ICONS: [RegExp, LucideIcon][] = [
  [/leite|iogurte|queijo|manteiga|requeij/, Milk],
  [/cafe|cha\b|capuccino/, Coffee],
  [/arroz|feij|farinha|macarrao|aveia|trigo|graos?/, Wheat],
  [/ovo/, Egg],
  [/carne|bife|patinho|alcatra|linguica/, Beef],
  [/frango|peito|coxa/, Drumstick],
  [/peixe|atum|sardinha|salmao/, Fish],
  [/pao|bisnaga|torrada/, Croissant],
  [/fruta|banana|maca|laranja|uva|mamao|tomate|alface|legume|verdura|batata|cebola/, Apple],
  [/detergente|sabao|amaciante|desinfetante|limpa|agua sanitaria|esponja|papel/, SprayCan],
  [/agua/, GlassWater],
  [/refrigerante|suco|refri/, CupSoda],
  [/cerveja/, Beer],
  [/vinho/, Wine],
  [/biscoito|bolacha|cookie/, Cookie],
  [/chocolate|doce|bala/, Candy],
];

function categoryIcon(category: string): LucideIcon {
  const c = fold(category);
  return CATEGORY_ICONS.find(([re]) => re.test(c))?.[1] ?? ShoppingBasket;
}

export function Thumb({ category, size = 40, image }: { category: string; size?: number; image?: string | null }) {
  const Icon = categoryIcon(category);
  return (
    <span className="thumb" style={{ width: size, height: size, background: placeholderColor(category) }} aria-hidden="true">
      {image ? <img src={image} alt="" loading="lazy" /> : <Icon size={Math.round(size * 0.45)} strokeWidth={2} />}
    </span>
  );
}

// Iniciais da loja para o logo de 40 px (spec 4.14).
export const initials = (name: string) =>
  name.split(/\s+/).filter((w) => /^[\p{L}\d]/u.test(w)).slice(0, 2).map((w) => w[0].toUpperCase()).join("") || "?";

// ---------- estrutura de tela ----------

export function Header({ back, title, subtitle, eyebrow, action, titleClass = "t-title-xl", bordered }: {
  back?: string | (() => void); title?: ReactNode; subtitle?: ReactNode; eyebrow?: ReactNode; action?: ReactNode;
  titleClass?: string; bordered?: boolean;
}) {
  return (
    <header className={`header${bordered ? " bordered" : ""}`}>
      {typeof back === "string" && (
        <Link to={back} className="icon-btn" aria-label="Voltar"><ChevronLeft size={20} strokeWidth={2.2} aria-hidden="true" /></Link>
      )}
      {typeof back === "function" && (
        <button type="button" className="icon-btn" aria-label="Voltar" onClick={back}>
          <ChevronLeft size={20} strokeWidth={2.2} aria-hidden="true" />
        </button>
      )}
      <div className="titles">
        {eyebrow && <span className="t-caption">{eyebrow}</span>}
        {title && <h1 className={titleClass}>{title}</h1>}
        {subtitle && <span className="t-caption">{subtitle}</span>}
      </div>
      {action}
    </header>
  );
}

export function IconButton({ label, children, ghost, ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { label: string; ghost?: boolean }) {
  return (
    <button type="button" className={`icon-btn${ghost ? " ghost" : ""}`} aria-label={label} {...props}>{children}</button>
  );
}

export const Spinner = ({ small }: { small?: boolean }) => (
  <span className={`spinner${small ? " sm" : ""}`} role="status" aria-label="Carregando" />
);

export function PrimaryButton({ loading, children, className = "", disabled, ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { loading?: boolean }) {
  return (
    <button type="button" className={`btn-primary${loading ? " loading" : ""} ${className}`} disabled={disabled || loading}
            aria-busy={loading || undefined} {...props}>
      {loading ? <Spinner /> : children}
    </button>
  );
}

export function Fab({ icon, children, loading, aboveNav, disabled, ...props }: ButtonHTMLAttributes<HTMLButtonElement> & {
  icon: ReactNode; loading?: boolean; aboveNav?: boolean;
}) {
  return (
    <button type="button" className={`fab${aboveNav ? " above-nav" : ""}${loading ? " loading" : ""}`} disabled={disabled || loading}
            aria-busy={loading || undefined} {...props}>
      {loading ? <Spinner /> : icon}
      <span>{children}</span>
    </button>
  );
}

export function Skeleton({ height = 16, width = "100%", radius }: { height?: number; width?: number | string; radius?: number }) {
  return <span className="skeleton" style={{ display: "block", height, width, borderRadius: radius }} aria-hidden="true" />;
}

export function ListSkeleton({ rows = 3, height = 76 }: { rows?: number; height?: number }) {
  return (
    <div className="stack" role="status" aria-label="Carregando">
      {Array.from({ length: rows }, (_, i) => <Skeleton key={i} height={height} radius={18} />)}
    </div>
  );
}

// Faixa de erro de rede no topo, com "Tentar de novo".
export function ErrorStrip({ message, onRetry }: { message?: string; onRetry: () => void }) {
  return (
    <div className="strip" role="alert">
      <span className="grow">{message ?? "Sem conexão com o servidor."}</span>
      <button type="button" className="btn-secondary" onClick={onRetry}>Tentar de novo</button>
    </div>
  );
}

export function BottomNav() {
  return (
    <nav className="bottom-nav" aria-label="Principal">
      <NavLink to="/" end><House size={22} strokeWidth={2} aria-hidden="true" />Início</NavLink>
      <NavLink to="/preferencias" end><UserRound size={22} strokeWidth={2} aria-hidden="true" />Perfil</NavLink>
    </nav>
  );
}

// ---------- folha (sheet) ----------

export function Sheet({ title, onClose, children, footer, label }: {
  title: ReactNode; onClose: () => void; children: ReactNode; footer?: ReactNode; label?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    // um campo com autoFocus já recebeu o foco; senão a folha recebe, para o leitor de tela anunciar
    if (!ref.current?.contains(document.activeElement)) ref.current?.focus();
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
      prev?.focus?.();
    };
  }, []);

  return createPortal(
    <div className="sheet-backdrop" onClick={onClose}>
      <div className="sheet" role="dialog" aria-modal="true" aria-label={label ?? (typeof title === "string" ? title : undefined)}
           tabIndex={-1} ref={ref} onClick={(e) => e.stopPropagation()}>
        <span className="grabber" aria-hidden="true" />
        <div className="sheet-head">
          <h2 className="t-title-lg">{title}</h2>
          <IconButton label="Fechar" onClick={onClose}><X size={20} strokeWidth={2.2} aria-hidden="true" /></IconButton>
        </div>
        <div className="sheet-body">{children}</div>
        {footer && <div className="sheet-foot">{footer}</div>}
      </div>
    </div>,
    document.body,
  );
}

// ---------- toast ----------

const ToastContext = createContext<(msg: string) => void>(() => {});

export function ToastProvider({ children }: { children: ReactNode }) {
  const [msg, setMsg] = useState<{ text: string; key: number } | null>(null);
  const timer = useRef<number>();
  const show = useCallback((text: string) => {
    window.clearTimeout(timer.current);
    setMsg({ text, key: Date.now() });
    timer.current = window.setTimeout(() => setMsg(null), 3000);
  }, []);
  return (
    <ToastContext.Provider value={show}>
      {children}
      {createPortal(
        <div aria-live="polite" aria-atomic="true">
          {msg && (
            <div key={msg.key} className="toast">
              <CircleCheck size={18} strokeWidth={2.2} aria-hidden="true" />{msg.text}
            </div>
          )}
        </div>,
        document.body,
      )}
    </ToastContext.Provider>
  );
}

export const useToast = () => useContext(ToastContext);

// ---------- restrições e selos ----------

export const RestrictionTag = () => (
  <span className="tag-restriction"><AlignLeft size={12} strokeWidth={2.4} aria-hidden="true" />Com restrição</span>
);

export type SeloKind = "prefere" | "exige" | "se-faltar" | "nunca";
const SELO_LABEL: Record<SeloKind, string> = { prefere: "Prefere", exige: "Exige", "se-faltar": "Se faltar", nunca: "Nunca" };

export const Selo = ({ kind }: { kind: SeloKind }) => <span className={`selo ${kind}`}>{SELO_LABEL[kind]}</span>;

export interface Understanding { kind: SeloKind; text: string }

const capitalize = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

// Frase da regra sem o prefixo "Categoria: " que o back-end põe em `text`.
export function ruleBody(rule: Pick<Rule, "text" | "category">): string {
  const prefix = rule.category ? `${capitalize(rule.category)}: ` : "";
  return prefix && rule.text.startsWith(prefix) ? rule.text.slice(prefix.length) : rule.text;
}

// Uma regra vira um ou mais selos: a cadeia de marcas vira "Prefere A" + "Se faltar B, C".
export function ruleSelos(rule: Pick<Rule, "kind" | "payload" | "hardness" | "text" | "category">): Understanding[] {
  const p = rule.payload as Record<string, unknown>;
  if (rule.kind === "brand_chain" && Array.isArray(p.order) && p.order.length) {
    const order = p.order as string[];
    const out: Understanding[] = [{ kind: p.allow_others === false ? "exige" : "prefere", text: `marca ${order[0]}` }];
    if (order.length > 1) out.push({ kind: "se-faltar", text: order.slice(1).join(", ") });
    return out;
  }
  if (rule.kind === "brand_block" && Array.isArray(p.brands)) {
    return [{ kind: "nunca", text: (p.brands as string[]).join(", ") }];
  }
  return [{ kind: rule.hardness === "hard" ? "exige" : "prefere", text: ruleBody(rule) }];
}

export function UnderstoodList({ items }: { items: Understanding[] }) {
  return (
    <ul className="understood">
      {items.map((u, i) => (
        <li key={i}><Selo kind={u.kind} /><span className="t-body">{u.text}</span></li>
      ))}
    </ul>
  );
}
