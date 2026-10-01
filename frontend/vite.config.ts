import { fileURLToPath, URL } from 'node:url'

import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

// 后端地址（基线 §10：前缀 /api/v1，后端监听 127.0.0.1:8000）
const BACKEND = process.env.VITE_BACKEND_ORIGIN ?? 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    port: 5173,
    strictPort: false,
    // .tmp 只放构建/自测临时文件（例如本机 esbuild 需要的可写 TEMP、截图脚本）；
    // 同时忽略编辑器/工具写入时产生的 `.xxx.tmpdir/` 与 `*.tmp` 临时文件，
    // 它们会被 Windows 占用（EBUSY）从而让 watcher 抛错退出。
    watch: {
      ignored: [/[/\\]\.tmp([/\\]|$)/, /[/\\]\.[^/\\]*\.tmpdir([/\\]|$)/, /\.tmp$/],
    },
    proxy: {
      '/api': {
        target: BACKEND,
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
    chunkSizeWarningLimit: 1500,
  },
})
