import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from 'tailwindcss'
import autoprefixer from 'autoprefixer'

export default defineConfig({
  plugins: [react()],
  css: {
    postcss: {
      plugins: [tailwindcss(), autoprefixer()],
    },
  },
  server: {
    // 5173 unless the launcher assigns one. Nothing needs this exact port --
    // `/api` is proxied below, so the browser is same-origin with the API and
    // the CORS allowlist in `api/main.py` (which does name 5173) is never
    // consulted. Without this, a second dev server cannot start beside a
    // running one.
    port: Number(process.env.PORT) || 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})
