import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// o back-end roda na 8080 (mesma porta da redirect do OAuth cadastrada no agente)
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://localhost:8080", changeOrigin: true, rewrite: (p) => p.replace(/^\/api/, "") },
    },
  },
});
