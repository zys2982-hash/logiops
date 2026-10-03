/**
 * 统一 HTTP 层（基线 §10.1 / §12.3）：
 * - 前缀 /api/v1（Vite dev 代理 /api → http://127.0.0.1:8000）
 * - Authorization: Bearer <jwt>
 * - X-Workspace-Id: <id>
 * - 成功响应直接返回资源对象或分页对象（不套 code/data）
 * - 错误体 {"error":{"code","message","details"}} 统一在此解析
 * - 401 → 登出；409 → 提示“数据已被他人更新，已为你刷新”
 * - 后端未就绪（网络错误 / 5xx）且**显式** VITE_USE_MOCKS=true 时，走 src/mocks 本地 fixture 兜底
 * - 404 绝不兜底：本系统里 404 有语义（跨租户越权、资源不存在），兜底会把真实结果渲染成假数据
 */
import axios, {
  AxiosError,
  type AxiosInstance,
  type AxiosRequestConfig,
  type AxiosResponse,
  type InternalAxiosRequestConfig,
} from 'axios'
import { ElMessage } from 'element-plus'

import { authSnapshot, notifyConflict, notifyUnauthorized } from './runtime'
import { resolveMock } from '@/mocks/registry'
import type { ApiErrorBody } from '@/types'

export const API_PREFIX = '/api/v1'

const USE_MOCKS = import.meta.env.VITE_USE_MOCKS === 'true'

/** 错误码 → 中文文案（§10.2） */
export const ERROR_CODE_TEXT: Record<string, string> = {
  AUTH_INVALID_CREDENTIALS: '账号或密码错误',
  AUTH_TOKEN_EXPIRED: '登录已过期，请重新登录',
  AUTH_TOKEN_INVALID: '登录状态无效，请重新登录',
  PERM_DENIED: '当前角色权限不足',
  PERM_WORKSPACE_NOT_MEMBER: '你不是该工作区成员',
  RESOURCE_NOT_FOUND: '资源不存在',
  VALIDATION_ERROR: '参数校验失败',
  STATE_TRANSITION_INVALID: '当前状态不允许该操作',
  OPTIMISTIC_LOCK_CONFLICT: '数据已被他人更新，已为你刷新',
  DUPLICATE_ENTITY: '唯一键冲突（编号已存在）',
  OPEN_EXCEPTION_EXISTS: '该订单已有未关闭异常',
  AI_ANALYSIS_IN_PROGRESS: '该异常已有 AI 分析在执行',
  APPROVAL_ALREADY_DECIDED: '该审批单已被处理',
  AI_OUTPUT_INVALID: 'AI 输出未通过校验，已拦截',
  LLM_UNAVAILABLE: 'AI 暂不可用，可重试或手工处理',
  RATE_LIMITED: '操作过于频繁，请稍后再试（AI 分析限 1 次/5 秒）',
  INTERNAL_ERROR: '服务内部错误',
}

export class ApiError extends Error {
  readonly code: string
  readonly status: number
  readonly details: Record<string, unknown>

  constructor(code: string, message: string, status: number, details: Record<string, unknown> = {}) {
    super(message)
    this.name = 'ApiError'
    this.code = code
    this.status = status
    this.details = details
  }
}

/** 标记"本响应由本地 fixture 兜底"，页面可据此展示“本地演示数据”提示 */
export interface MockMeta {
  mocked: boolean
  reason?: string
}

export interface MockedResponse<T> {
  data: T
  meta: MockMeta
}

const MOCK_FLAG = '__logiopsMock'

let mockNoticeShown = false

function markMock(response: AxiosResponse, reason: string): AxiosResponse {
  ;(response as AxiosResponse & { [MOCK_FLAG]?: MockMeta })[MOCK_FLAG] = { mocked: true, reason }
  if (!mockNoticeShown) {
    mockNoticeShown = true
    // 只在第一次兜底时提示，避免刷屏
    ElMessage({
      message: '后端未就绪，已使用本地演示数据（fixture）渲染页面',
      type: 'warning',
      duration: 4000,
    })
  }
  return response
}

export function isMocked(response: AxiosResponse | undefined): boolean {
  return Boolean((response as AxiosResponse & { [MOCK_FLAG]?: MockMeta })?.[MOCK_FLAG]?.mocked)
}

export function mockReason(response: AxiosResponse | undefined): string | undefined {
  return (response as AxiosResponse & { [MOCK_FLAG]?: MockMeta })?.[MOCK_FLAG]?.reason
}

export const http: AxiosInstance = axios.create({
  baseURL: API_PREFIX,
  timeout: 20000,
  headers: { 'Content-Type': 'application/json' },
})

/* ------------------------------------------------------------ 请求拦截 */

// 后端契约（基线 §10.1）：分页 page_size ≤ 100，越界会返回 422 VALIDATION_ERROR。
// 在请求层统一夹紧，避免任何页面写错数字就把整页打成"参数校验失败"（曾发生在车辆页的司机下拉框）。
const MAX_PAGE_SIZE = 100

function clampPagination(params: unknown): unknown {
  if (!params || typeof params !== 'object') return params
  const record = { ...(params as Record<string, unknown>) }
  const size = Number(record.page_size)
  if (Number.isFinite(size) && size > MAX_PAGE_SIZE) {
    record.page_size = MAX_PAGE_SIZE
  }
  return record
}

http.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  const { token, workspaceId } = authSnapshot()
  if (token) config.headers.set('Authorization', `Bearer ${token}`)
  if (workspaceId) config.headers.set('X-Workspace-Id', String(workspaceId))
  config.params = clampPagination(config.params)
  return config
})

/* ------------------------------------------------------------ 响应拦截 */

function shouldFallbackToMock(status: number | undefined, code: string | undefined): boolean {
  if (!USE_MOCKS) return false
  // 404 不兜底：跨租户越权 / 资源不存在在本系统里是**有语义的真实结果**，
  // 渲染成 fixture 会掩盖多租户隔离与「资源不存在」的真实表现。
  if (status === 404) return false
  if (status === undefined) return true // 网络错误 / 后端未起
  if (status >= 500 && code !== 'AI_OUTPUT_INVALID' && code !== 'LLM_UNAVAILABLE') return true
  return false
}

http.interceptors.response.use(
  (response: AxiosResponse) => response,
  async (error: AxiosError<ApiErrorBody>) => {
    const status = error.response?.status
    const body = error.response?.data
    const code = body?.error?.code

    // 后端未就绪 → 本地 fixture 兜底（保证页面渲染骨架而不是白屏）
    if (shouldFallbackToMock(status, code)) {
      const config = error.config as AxiosRequestConfig | undefined
      if (config) {
        const reason = status === undefined ? 'NETWORK_UNREACHABLE' : `HTTP_${status}`
        const mocked = await resolveMock(config)
        if (mocked) return markMock(mocked, reason)
      }
    }

    const envelopeCode = code ?? (status === 401 ? 'AUTH_TOKEN_INVALID' : 'INTERNAL_ERROR')
    const message =
      body?.error?.message ||
      ERROR_CODE_TEXT[envelopeCode] ||
      (error.code === 'ECONNABORTED' ? '请求超时' : error.message) ||
      '请求失败'

    if (status === 401) {
      notifyUnauthorized()
      ElMessage.error(ERROR_CODE_TEXT[envelopeCode] ?? '登录已过期，请重新登录')
    } else if (status === 409) {
      // §12.2-3：收到 409 时提示“数据已被他人更新，已为你刷新”
      notifyConflict(body?.error?.message || ERROR_CODE_TEXT.OPTIMISTIC_LOCK_CONFLICT)
      ElMessage.warning(body?.error?.message || ERROR_CODE_TEXT.OPTIMISTIC_LOCK_CONFLICT)
    } else if (code === 'RATE_LIMITED') {
      ElMessage.warning(message)
    } else if (status === 403) {
      ElMessage.error(message)
    } else if (status !== 404) {
      ElMessage.error(message)
    }

    return Promise.reject(
      new ApiError(envelopeCode, message, status ?? 0, body?.error?.details ?? {}),
    )
  },
)

/* -------------------------------------------------------------- 工具方法 */

/** 返回 data 与"是否本地兜底"标记 */
export async function requestWithMeta<T>(config: AxiosRequestConfig): Promise<MockedResponse<T>> {
  const response = await http.request<T>(config)
  return {
    data: response.data,
    meta: { mocked: isMocked(response), reason: mockReason(response) },
  }
}

/** 普通请求：直接返回 data */
export async function request<T>(config: AxiosRequestConfig): Promise<T> {
  const response = await http.request<T>(config)
  return response.data
}

export const get = <T>(url: string, params?: Record<string, unknown>, config?: AxiosRequestConfig) =>
  request<T>({ ...config, url, method: 'GET', params })

export const post = <T>(url: string, data?: unknown, config?: AxiosRequestConfig) =>
  request<T>({ ...config, url, method: 'POST', data })

export const patch = <T>(url: string, data?: unknown, config?: AxiosRequestConfig) =>
  request<T>({ ...config, url, method: 'PATCH', data })

export const del = <T>(url: string, config?: AxiosRequestConfig) =>
  request<T>({ ...config, url, method: 'DELETE' })

export default http
