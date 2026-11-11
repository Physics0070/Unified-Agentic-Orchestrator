import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    // In dev the API runs separately; in production the API shares the origin.
    proxy: {
      '/health': 'http://localhost:7860',
      '/api': 'http://localhost:7860',
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
  },
})
