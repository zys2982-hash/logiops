/**
 * 认证/工作区的运行时状态桥（避免 axios 拦截器与 Pinia store 循环依赖）。
 * Pinia store 在初始化时注入读写函数，request.ts 只通过这里取 token / workspace。
 */
import type { Perm } from '@/types'

export interface AuthSnapshot {
  token: string | null
  workspaceId: number | null
  permissions: Perm[]
}

type Getter = () => AuthSnapshot
type OnUnauthorized = () => void
type OnConflict = (message: string) => void

let getter: Getter = () => ({ token: null, workspaceId: null, permissions: [] })
let onUnauthorized: OnUnauthorized = () => {}
let onConflict: OnConflict = () => {}

export function bindAuthRuntime(options: {
  get: Getter
  onUnauthorized?: OnUnauthorized
  onConflict?: OnConflict
}): void {
  getter = options.get
  if (options.onUnauthorized) onUnauthorized = options.onUnauthorized
  if (options.onConflict) onConflict = options.onConflict
}

export function authSnapshot(): AuthSnapshot {
  return getter()
}

export function notifyUnauthorized(): void {
  onUnauthorized()
}

export function notifyConflict(message: string): void {
  onConflict(message)
}
