import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    // 显式绑定 IPv4，避免只监听 [::1] 导致 127.0.0.1 访问不到
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    // 开发环境把 /api 代理到 FastAPI(8010)，免去跨域烦恼
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8010',
        changeOrigin: true,
      },
    },
  },
})
