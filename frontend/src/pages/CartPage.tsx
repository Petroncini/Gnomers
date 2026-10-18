import { Fragment, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ChevronDown, CircleAlert, MapPin, MessageCircle, Wallet } from "lucide-react";
import { api, brl, Cart, CheckoutResponse, Decision, Plan, ShoppingList } from "../api";
import { ChoiceNote } from "../components/DecisionCard";
import { ErrorStrip, Header, ListSkeleton, PrimaryButton, Thumb, initials } from "../ui";

function fmtSlot(slot: { from: string; to: string }): string {
  const from = new Date(slot.from);
  const to = new Date(slot.to);
  const day = from.toLocaleDateString("pt-BR", { weekday: "short", day: "2-digit", month: "2-digit" });
  const h = (d: Date) => d.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
  return `${day}, ${h(from)}–${h(to)}`;
}

function deliveryLabel(cart: Cart): string | null {
  if (cart.delivery?.slot) return fmtSlot(cart.delivery.slot);
  if (cart.delivery?.eta) return `Entrega em ${cart.delivery.eta.min}–${cart.delivery.eta.max} min`;
  return null;
}

function Breakdown({ cart }: { cart: Cart }) {
  const s = cart.summary;
  return (
    <dl className="breakdown">
      <dt>Produtos</dt><dd>{brl(s.subtotal)}</dd>
      <dt>Frete{cart.delivery?.name ? ` (${cart.delivery.name})` : ""}</dt><dd>{s.delivery ? brl(s.delivery) : "Grátis"}</dd>
      {s.fees > 0 && (<><dt>Taxas</dt><dd>{brl(s.fees)}</dd></>)}
      {s.voucher > 0 && (<><dt>Cupom {cart.voucher?.code}</dt><dd className="discount">−{brl(s.voucher)}</dd></>)}
    </dl>
  );
}

// Cartão de loja (spec 4.14): resumo numa linha; "Ver itens" abre as linhas com preço.
function StoreCard({ cart, plan, notes, onPlan, multi }: {
  cart: Cart; plan: Plan; notes: Decision[]; onPlan: (p: Plan) => void; multi: boolean;
}) {
  const [open, setOpen] = useState(false);
  const count = cart.lines.reduce((n, ln) => n + ln.quantity, 0);
  const names = cart.lines.map((ln) => `${ln.quantity > 1 ? `${ln.quantity}× ` : ""}${ln.name}`);
  const summary = names.length > 2 ? `${names.slice(0, 2).join(", ")} e mais ${names.length - 2}` : names.join(" e ");
  const when = deliveryLabel(cart);

  return (
    <section className="card store-card" aria-label={cart.merchant_name}>
      <div className="top">
        <span className="store-logo" aria-hidden="true">{initials(cart.merchant_name)}</span>
        <div className="grow">
          <span className="t-title-md">{cart.merchant_name} · {count} {count === 1 ? "item" : "itens"}</span>
          {when && <span className="t-caption">{when}</span>}
        </div>
        <span className="price" style={{ fontSize: "1rem" }}>{brl(cart.summary.subtotal)}</span>
      </div>
      <p className="summary-line">{summary}</p>

      {notes.map((d) => <ChoiceNote key={d.id} plan={plan} card={d} onPlan={onPlan} />)}
      {cart.delivery_problem && (
        <div className="notice"><CircleAlert size={16} className="ico" aria-hidden="true" /><span className="grow">Entrega: {cart.delivery_problem}</span></div>
      )}
      {cart.warning && (
        <div className="notice"><CircleAlert size={16} className="ico" aria-hidden="true" />
          <span className="grow">{cart.warning.userFriendlyMessage ?? cart.warning.message}</span></div>
      )}

      <button type="button" className="btn-text toggle" aria-expanded={open} onClick={() => setOpen(!open)}>
        {open ? "Esconder itens" : "Ver itens e valores"}
        <ChevronDown size={16} aria-hidden="true" style={{ transform: open ? "rotate(180deg)" : undefined, transition: "transform 150ms ease-out" }} />
      </button>
      {open && (
        <>
          <ul className="lines">
            {cart.lines.map((ln) => (
              <li key={ln.sku_id}>
                <Thumb category={ln.name} size={36} image={ln.image} />
                <span className="line-name"><span className="q">{ln.quantity}×</span> {ln.name}</span>
                <span className="price" style={{ fontWeight: 700 }}>{brl(ln.unit_price * ln.quantity)}</span>
              </li>
            ))}
          </ul>
          {multi && <Breakdown cart={cart} />}
        </>
      )}
    </section>
  );
}

// Confirmação: único lugar onde o pedido é enviado (spec 5.7).
export default function CartPage() {
  const { planId } = useParams();
  const [plan, setPlan] = useState<Plan | null>(null);
  const [listName, setListName] = useState("");
  const [failed, setFailed] = useState(false);
  const [checking, setChecking] = useState(false);
  const [changes, setChanges] = useState<CheckoutResponse["changes"] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();

  const load = () => {
    setFailed(false);
    api.get<Plan>(`/plans/${planId}`).then((p) => {
      setPlan(p);
      api.get<ShoppingList>(`/lists/${p.list_id}`).then((l) => setListName(l.name)).catch(() => {});
    }).catch(() => setFailed(true));
  };
  useEffect(load, [planId]);

  const checkout = async (acceptChanges = false) => {
    if (checking) return;  // um toque só: evita pedido duplicado
    setChecking(true);
    setError(null);
    try {
      const r = await api.post<CheckoutResponse>(`/plans/${planId}/checkout`, { accept_changes: acceptChanges });
      if (r.status === "changed") {
        setChanges(r.changes ?? []);
        setPlan(r.plan);
        setChecking(false);
      } else {
        navigate(`/pedido/${planId}`);
      }
    } catch (e) {
      setError((e as Error).message);
      setChecking(false);
    }
  };

  const chat = `/carrinho/${planId}`;
  // o assistente redireciona para cá quando não há nada a decidir; `chat` pede para ele ficar
  const toChat = () => navigate(chat, { state: { chat: true } });

  if (!plan) {
    return (
      <div className="screen">
        {failed && <ErrorStrip onRetry={load} />}
        <Header back={toChat} title="Confirme sua compra" titleClass="t-title-lg" />
        <main className="content">{!failed && <ListSkeleton rows={3} height={120} />}</main>
      </div>
    );
  }

  const r = plan.result;
  const chosen = r.chosen;
  if (!chosen || (plan.status !== "ready" && plan.status !== "ordered")) {
    return (
      <div className="screen">
        <Header back={toChat} title="Confirme sua compra" titleClass="t-title-lg" />
        <main className="content">
          <div className="notice plain"><span className="grow">O carrinho ainda não está pronto. <Link to={chat}>Ver o assistente</Link></span></div>
        </main>
      </div>
    );
  }

  const decisions = r.decisions ?? [];
  const open = decisions.filter((d) => d.status === "open");
  const info = decisions.filter((d) => !d.question && d.item_id != null);
  const noteFor = (c: Cart) => info.filter((d) => d.type === "brand_chain_info" && d.chosen?.merchant_id === c.merchant_id);
  const choices = info.filter((d) => d.type !== "brand_chain_info");
  const itemCount = chosen.carts.reduce((n, c) => n + c.lines.length, 0);
  const multi = chosen.carts.length > 1;
  const ordered = plan.status === "ordered";
  const payment = chosen.carts.find((c) => c.payment)?.payment;

  return (
    <div className="screen">
      <Header back={toChat} title="Confirme sua compra" titleClass="t-title-lg"
              subtitle={`${listName ? `${listName} · ` : ""}${itemCount} ${itemCount === 1 ? "item" : "itens"}`} />

      <main className="content">
        {open.length > 0 && (
          <div className="notice">
            <CircleAlert size={16} className="ico" aria-hidden="true" />
            <span className="grow">
              Ainda {open.length === 1 ? "há 1 decisão" : `há ${open.length} decisões`} esperando por você.
              <Link to={chat} state={{ chat: true }}>Resolver no assistente</Link>
            </span>
          </div>
        )}

        {chosen.carts.map((c) => (
          <StoreCard key={c.cart_id} cart={c} plan={plan} notes={noteFor(c)} onPlan={setPlan} multi={multi} />
        ))}

        {(r.missing?.length ?? 0) > 0 && (
          <div className="notice">
            <CircleAlert size={16} className="ico" aria-hidden="true" />
            <span className="grow">Ficou de fora: {r.missing!.map((m) => m.raw_text).join(", ")}</span>
          </div>
        )}

        {choices.length > 0 && (
          <details className="card">
            <summary>
              {choices.length} {choices.length === 1 ? "escolha que fiz" : "escolhas que fiz"} por você
              <ChevronDown size={18} className="chev" aria-hidden="true" />
            </summary>
            <div className="details-body">
              {choices.map((d) => <ChoiceNote key={d.id} plan={plan} card={d} onPlan={setPlan} tone="plain" />)}
            </div>
          </details>
        )}

        <div className="card divided">
          <div className="info-row">
            <MapPin size={20} className="ico" aria-hidden="true" />
            <span className="grow">
              <span className="t-small muted">Entrega em</span>
              <span className="t-body-strong">{r.address ? r.address.alias ?? r.address.formatted : "Endereço da sua conta iFood"}</span>
              {r.address?.alias && <span className="t-caption">{r.address.formatted}</span>}
            </span>
          </div>
          <div className="info-row">
            <Wallet size={20} className="ico" aria-hidden="true" />
            <span className="grow">
              <span className="t-small muted">Pagamento</span>
              <span className="t-body-strong">{payment?.kind === "PIX" || !payment ? "Pix" : payment.kind}</span>
              <span className="t-caption">O código aparece depois de confirmar{multi ? ", um por mercado" : ""}.</span>
            </span>
          </div>
        </div>

        <section className="card pad stack" style={{ gap: 12 }} aria-label="Resumo de valores">
          {multi ? (
            <dl className="breakdown">
              {chosen.carts.map((c) => (
                <Fragment key={c.cart_id}><dt>{c.merchant_name}</dt><dd>{brl(c.summary.total)}</dd></Fragment>
              ))}
            </dl>
          ) : <Breakdown cart={chosen.carts[0]} />}
          <div className="total-row">
            <span>Total</span><span className="price">{brl(chosen.total)}</span>
          </div>
          {multi && (r.savings_vs_single ?? 0) > 0 && (
            <p className="saving">{brl(r.savings_vs_single)} a menos que tudo num mercado só</p>
          )}
        </section>

        {(r.alternatives?.length ?? 0) > 0 && (
          <details className="card">
            <summary>Outras opções montadas<ChevronDown size={18} className="chev" aria-hidden="true" /></summary>
            <div className="details-body">
              <dl className="breakdown">
                {r.alternatives!.map((a) => (
                  <Fragment key={a.index}>
                    <dt>{a.carts.map((c) => c.merchant_name).join(" + ") || a.errors.map((e) => e.merchant_name).join(" + ")}</dt>
                    <dd>{a.ok ? brl(a.total) : "indisponível"}</dd>
                  </Fragment>
                ))}
              </dl>
            </div>
          </details>
        )}

        {changes && (
          <div className="notice" role="alert">
            <CircleAlert size={16} className="ico" aria-hidden="true" />
            <div className="grow">
              <span>O total mudou desde que o carrinho foi montado:</span>
              <ul>{changes.map((c) => <li key={c.merchant_name}>{c.merchant_name}: {brl(c.before)} → {brl(c.after)}</li>)}</ul>
            </div>
          </div>
        )}
        {error && <div className="notice" role="alert"><span className="grow">{error}</span></div>}
      </main>

      <footer className="footer">
        {ordered ? (
          <Link to={`/pedido/${plan.id}`} className="btn-primary">Ver pedido</Link>
        ) : changes ? (
          <PrimaryButton loading={checking} onClick={() => checkout(true)}>Confirmar com o novo total · {brl(chosen.total)}</PrimaryButton>
        ) : (
          <PrimaryButton loading={checking} disabled={!chosen.ok} onClick={() => checkout(false)}>
            Confirmar e pagar {brl(chosen.total)}
          </PrimaryButton>
        )}
        {!ordered && (
          <Link to={chat} state={{ chat: true }} className="btn-text center">
            <MessageCircle size={16} aria-hidden="true" />Pedir mudança ao assistente
          </Link>
        )}
      </footer>
    </div>
  );
}
