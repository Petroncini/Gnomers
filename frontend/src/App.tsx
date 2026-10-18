import { useEffect, useState } from "react";
import { Link, Route, Routes, useLocation } from "react-router-dom";
import { api, AuthStatus } from "./api";
import Home from "./pages/Home";
import ListPage from "./pages/ListPage";
import Preferences from "./pages/Preferences";
import CartPage from "./pages/CartPage";
import OrderPage from "./pages/OrderPage";

export default function App() {
  const [auth, setAuth] = useState<AuthStatus | null>(null);
  const location = useLocation();

  useEffect(() => {
    api.get<AuthStatus>("/auth/status").then(setAuth).catch(() => setAuth(null));
  }, [location.pathname]);

  return (
    <div className="app">
      <header className="topbar">
        <Link to="/" className="brand">
          <img src="/icon.svg" alt="" width={28} height={28} />
          <span>Gnomers</span>
        </Link>
        <nav>
          <Link to="/preferencias">Preferências</Link>
        </nav>
      </header>
      {auth && auth.mode === "live" && !auth.has_token && (
        <div className="banner">
          Conecte sua conta de teste do iFood para montar carrinhos de verdade.{" "}
          <a href="/api/auth/login">Entrar no iFood</a>
        </div>
      )}
      {auth && auth.mode !== "live" && (
        <div className="banner subtle">
          Modo {auth.mode === "fake" ? "simulador" : auth.mode}: {auth.mode === "replay"
            ? "respostas gravadas do sandbox do iFood"
            : auth.mode === "fake" ? "catálogo local, sem chamar o iFood" : "gravando chamadas"}
        </div>
      )}
      <main>
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/listas/:id" element={<ListPage />} />
          <Route path="/preferencias" element={<Preferences />} />
          <Route path="/listas/:id/preferencias" element={<Preferences />} />
          <Route path="/carrinho/:planId" element={<CartPage />} />
          <Route path="/pedido/:planId" element={<OrderPage />} />
        </Routes>
      </main>
    </div>
  );
}
