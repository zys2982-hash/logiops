/** 全局错误兜底：避免后端未就绪时页面白屏 */
import { ElMessage } from 'element-plus'

export function errorHandler(error: unknown): void {
  const message = error instanceof Error ? error.message : String(error)
  // 调试期保留控制台细节；生产构建下 Vite 会剔除这段（见下方说明）
  if (import.meta.env.DEV) console.error('[LogiOps]', error)
  ElMessage.error(message || '前端出现未捕获异常')
}
