import { fileURLToPath, URL } from 'node:url'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

// The FastAPI server (mecha serve) listens here; see src/mecha/cli.py.
const API_TARGET = 'http://127.0.0.1:7878'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  build: {
    // Served by FastAPI and bundled into the wheel (git-ignored).
    outDir: '../src/mecha/static',
    emptyOutDir: true,
    assetsDir: 'assets',
  },
  server: {
    port: 5173,
    strictPort: true,
    host: 'localhost',
    proxy: {
      // changeOrigin rewrites Host to 127.0.0.1:7878 so the backend Host check passes.
      '/api': { target: API_TARGET, changeOrigin: true },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
  },
})
