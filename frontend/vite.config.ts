import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      // 前端 /api → FastAPI 后端 :8000
      '/api': { target: 'http://localhost:8000', changeOrigin: true },
    },
  },
})
