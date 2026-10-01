import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Бэкенд слушает 8000 (см. run.bat / uvicorn). Раньше прокси указывал на 8001,
// из-за чего все запросы к /api падали, если фронтенд открывали через dev-сервер.
const BACKEND = 'http://127.0.0.1:8000'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  resolve: {
    dedupe: ['react', 'react-dom'],
  },
  server: {
    proxy: {
      '/api': BACKEND,
      '/uploads': BACKEND,
      '/clips': BACKEND,
      '/thumbs': BACKEND,
      '/ads': BACKEND,
      '/ws': {
        target: BACKEND.replace('http', 'ws'),
        ws: true,
      },
    },
  },
})
