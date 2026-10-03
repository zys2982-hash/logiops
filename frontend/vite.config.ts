import { fileURLToPath, URL } from 'node:url'

import vue from '@vitejs/plugin-vue'
import { defineConfig, loadEnv } from 'vite'

// 后端地址（基线 §10：前缀 /api/v1，后端监听 127.0.0.1:8000）
const BACKEND = process.env.VITE_BACKEND_ORIGIN ?? 'http://127.0.0.1:8000'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), 'VITE_')

  // 生产构建禁止开启 mock 兜底：否则后端异常时会把真实错误静默渲染成演示数据，
  // 而且"构建成功"会掩盖这个问题——所以这里直接让构建失败。
  if (mode === 'production' && env.VITE_USE_MOCKS === 'true') {
    throw new Error(
      '生产构建禁止 VITE_USE_MOCKS=true：会把真实 404/5xx 渲染成演示数据，掩盖后端问题。' +
        '请删除该变量，或在 .env.production 中显式设为 false。',
    )
  }

  return {
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
  }
})
