import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 服务端把构建产物挂在 /ui/ 下；Tauri 里则用相对路径加载。
export default defineConfig({
  plugins: [react()],
  base: process.env.TAURI_BUILD ? "./" : "/ui/",
  build: { outDir: "dist", emptyOutDir: true, sourcemap: true },
  server: { port: 5173, strictPort: true },
});
