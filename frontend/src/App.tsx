import { useEffect, useState } from "react";
import { Route, Routes, useLocation } from "react-router-dom";
import { api, AuthStatus } from "./api";
import Home from "./pages/Home";
import ListPage from "./pages/ListPage";
import ItemPage from "./pages/ItemPage";
import Preferences from "./pages/Preferences";
import AssistantPage from "./pages/AssistantPage";
import CartPage from "./pages/CartPage";
import OrderPage from "./pages/OrderPage";

export default function App() {
  const [auth, setAuth] = useState<AuthStatus | null>(null);
  const location = useLocation();

  useEffect(() => {
    api.get<AuthStatus>("/auth/status").then(setAuth).catch(() => setAuth(null));
  }, [location.pathname]);

  useEffect(() => window.scrollTo(0, 0), [location.pathname]);

  return (
    <div className="app">
      {auth && auth.mode === "live" && !auth.has_token && (
        <div className="strip" role="status">
          <span className="grow">Conecte sua conta de teste do iFood para montar carrinhos de verdade.</span>
          <a className="btn-secondary" href="/api/auth/login">Entrar no iFood</a>
        </div>
      )}
      {auth && auth.mode !== "live" && (
        <div className="strip subtle">
          <span className="grow">
            Modo {auth.mode === "fake" ? "simulador" : auth.mode}: {auth.mode === "replay"
              ? "respostas gravadas do sandbox do iFood"
              : auth.mode === "fake" ? "catálogo local, sem chamar o iFood" : "gravando chamadas"}
          </span>
        </div>
      )}
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/listas/:id" element={<ListPage />} />
        <Route path="/listas/:id/itens/:itemId" element={<ItemPage />} />
        <Route path="/preferencias" element={<Preferences />} />
        <Route path="/listas/:id/preferencias" element={<Preferences />} />
        <Route path="/carrinho/:planId" element={<AssistantPage />} />
        <Route path="/carrinho/:planId/confirmar" element={<CartPage />} />
        <Route path="/pedido/:planId" element={<OrderPage />} />
      </Routes>
    </div>
  );
}
