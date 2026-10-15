import { fileURLToPath, URL } from 'node:url'
import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

// 开发时把 /api 代理到后端入口（Caddy 的 HTTPS 地址）。
// 会话 Cookie 带 Secure，所以必须走 https；本地证书是 Caddy 内部 CA 签发的，不校验。
const apiTarget = process.env.VITE_DEV_PROXY ?? 'https://localhost:8443'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': { target: apiTarget, changeOrigin: true, secure: false },
    },
  },
})
