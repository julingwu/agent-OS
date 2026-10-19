import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// "./" keeps one build usable in both homes:
//   FastAPI  -> /app/                (relative assets resolve under /app/assets/…)
//   Tauri    -> tauri://localhost/   (relative assets resolve at the root)
export default defineConfig({
  plugins: [react()],
  base: "./",
  build: { outDir: "dist", emptyOutDir: true },
  server: { proxy: { "/ui": "http://127.0.0.1:8077" } },
});
