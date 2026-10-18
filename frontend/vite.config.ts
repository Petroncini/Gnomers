import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// o back-end roda na 8080 (mesma porta da redirect do OAuth cadastrada no agente);
// GNOMERS_API aponta para outro, ex.: GNOMERS_API=http://localhost:8090 npm run dev
const api = process.env.GNOMERS_API ?? "http://localhost:8080";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: api, changeOrigin: true, rewrite: (p) => p.replace(/^\/api/, "") },
    },
  },
});
