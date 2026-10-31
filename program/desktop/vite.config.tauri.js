import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Tauri 内嵌用：相对路径 + 独立输出目录（不能和给浏览器用的 /ui/ 构建混用）
export default defineConfig({
  plugins: [react()],
  base: "./",
  build: { outDir: "dist-tauri", emptyOutDir: true },
});
