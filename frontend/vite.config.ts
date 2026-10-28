import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// 多頁建置：draw / display / admin。
// VITE_PAGES=draw 只建置指定頁面；VITE_OUT_DIR 指定輸出目錄（預設 dist）。
const ALL_PAGES = {
  exhibition: 'exhibition/index.html',
  draw: 'draw/index.html',
  display: 'display/index.html',
  admin: 'admin/index.html',
} as const;

type PageName = keyof typeof ALL_PAGES;

const root = fileURLToPath(new URL('.', import.meta.url));
const pages = (process.env.VITE_PAGES ?? 'exhibition,admin')
  .split(',')
  .map((name) => name.trim())
  .filter((name): name is PageName => name in ALL_PAGES);

const backend = process.env.VITE_BACKEND ?? 'http://127.0.0.1:8080';

export default defineConfig({
  plugins: [react()],
  base: '/',
  build: {
    outDir: process.env.VITE_OUT_DIR ?? 'dist',
    emptyOutDir: true,
    rollupOptions: {
      input: Object.fromEntries(pages.map((name) => [name, root + ALL_PAGES[name]])),
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': backend,
      '/media': backend,
      '/ws': { target: backend.replace(/^http/, 'ws'), ws: true },
    },
  },
});
