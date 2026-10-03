/**
 * 后端未就绪时的本地 fixture 路由（兜底，不是 mock server）。
 *
 * 触发条件见 api/request.ts：**显式** VITE_USE_MOCKS=true 且（网络错误 / 5xx）；404 永不兜底。
 * 作用：保证 /exceptions、/exceptions/:id 等页面在无后端时仍渲染出骨架，而不是白屏报错。
 *
 * 形状与 backend 实际响应一一对应（2026-09-30 逐端点核对）：
 *   - 列表/详情为扁平 + 详情附加嵌套；followups/events/tracking 是分页对象；approvals/messages/notifications 是平数组
 *   - AI：output / steps[].args；审批：ai_payload / final_payload / diff(fields 为 map) / execution_result
 *   - 消息：parse_result；时间线：detail
 */
import type { AxiosRequestConfig, AxiosResponse } from 'axios'

import {
  CASE_A,
  aiSteps,
  approvals as approvalsFixture,
  auditLogs as auditLogsFixture,
  carrierMessages as messagesFixture,
  customers as customersFixture,
  carriers as carriersFixture,
  dashboardSummary,
  dashboardTrend,
  demoState,
  drivers as driversFixture,
  exceptionAnalysisSummary,
  exceptionCounts,
  exceptionEvents as eventsFixture,
  exceptionList,
  exceptionMain,
  fixtureLogin,
  fixtureMe,
  followups as followupsFixture,
  knowledgeDocChunks,
  knowledgeDocs,
  makeAiAnalysis,
  members as membersFixture,
  notifications as notificationsFixture,
  orders as ordersFixture,
  slaRules as slaRulesFixture,
  trackingEventsMain,
  vehicles as vehiclesFixture,
} from './fixtures'
import type {
  AiAnalysis,
  AiAnalysisStep,
  Approval,
  CarrierMessage,
  ExceptionDetail,
  ExceptionEvent,
  FollowupTask,
  KnowledgeChunk,
  Notification,
  Order,
  Page,
} from '@/types'

/* --------------------------------------------------------- 可变本地状态 */

interface MockState {
  exception: ExceptionDetail
  exceptions: typeof exceptionList
  approvals: Approval[]
  notifications: Notification[]
  followups: FollowupTask[]
  messages: CarrierMessage[]
  events: ExceptionEvent[]
  analysis: AiAnalysis | null
  /** analysis_id → 模拟开始时间，用于按轮询次数逐条点亮 steps */
  analysisRuns: Map<number, { startedAt: number }>
  analysisSeq: number
  order: Order
  orderList: Order[]
}

export const mockState: MockState = {
  // 与真实 seed 一致：CASE-A 已有一次 READY 分析（详情只带摘要，前端会再拉 /ai-analyses/{id}）
  exception: { ...exceptionMain, latest_analysis: exceptionAnalysisSummary, counts: exceptionCounts },
  exceptions: [...exceptionList],
  approvals: approvalsFixture.map((a) => ({ ...a })),
  notifications: notificationsFixture.map((n) => ({ ...n })),
  followups: followupsFixture.map((f) => ({ ...f })),
  messages: messagesFixture.map((m) => ({ ...m })),
  events: eventsFixture.map((e) => ({ ...e })),
  analysis: null,
  analysisRuns: new Map(),
  analysisSeq: 2,
  order: { ...ordersFixture[0] },
  orderList: ordersFixture.map((o) => ({ ...o })),
}

const ANALYSIS_STEP_INTERVAL_MS = 1500

/** 演示用：把本地状态重置为 seed 初始态（对应 /demo/actions/reset） */
export function resetMockState(): void {
  mockState.exception = { ...exceptionMain, latest_analysis: exceptionAnalysisSummary, counts: exceptionCounts }
  mockState.exceptions = [...exceptionList]
  mockState.approvals = approvalsFixture.map((a) => ({ ...a }))
  mockState.notifications = notificationsFixture.map((n) => ({ ...n }))
  mockState.followups = followupsFixture.map((f) => ({ ...f }))
  mockState.messages = messagesFixture.map((m) => ({ ...m }))
  mockState.events = eventsFixture.map((e) => ({ ...e }))
  mockState.analysis = null
  mockState.analysisRuns.clear()
  mockState.analysisSeq = 2
  mockState.order = { ...ordersFixture[0] }
  mockState.orderList = ordersFixture.map((o) => ({ ...o }))
  demoTickOffset = 0
}

/** 按轮询经过时间计算"逐步点亮"的分析结果（step 内容来自 fixture，不做假动画） */
function currentAnalysis(id: number): AiAnalysis {
  const run = mockState.analysisRuns.get(id)
  const elapsed = run ? Date.now() - run.startedAt : Number.MAX_SAFE_INTEGER
  const done = Math.min(aiSteps.length, Math.floor(elapsed / ANALYSIS_STEP_INTERVAL_MS) + 1)
  const finished = done >= aiSteps.length

  const steps: AiAnalysisStep[] = aiSteps.map((step, index) => {
    if (index < done - 1) return { ...step, status: 'OK' }
    if (index === done - 1 && !finished) return { ...step, status: 'RUNNING', duration_ms: null }
    if (index < done) return { ...step, status: 'OK' }
    return { ...step, status: 'RUNNING', duration_ms: null, result_summary: null }
  })

  const base = makeAiAnalysis(id)
  if (!finished) {
    return {
      ...base,
      status: 'RUNNING',
      output: null,
      risk_level_calculated: null,
      finished_at: null,
      steps,
    }
  }
  return { ...base, status: 'READY', steps: aiSteps.map((s) => ({ ...s })) }
}

/* --------------------------------------------------------------- 工具 */

function ok<T>(config: AxiosRequestConfig, data: T, status = 200): AxiosResponse<T> {
  return {
    data,
    status,
    statusText: 'OK (local fixture)',
    headers: { 'x-logiops-fixture': '1' },
    config: config as never,
  }
}

function paginate<T>(items: T[], params: Record<string, unknown>): Page<T> {
  const page = Number(params.page ?? 1) || 1
  const pageSize = Number(params.page_size ?? 20) || 20
  const start = (page - 1) * pageSize
  return {
    items: items.slice(start, start + pageSize),
    total: items.length,
    page,
    page_size: pageSize,
  }
}

function parseBody<T>(data: unknown): T {
  if (typeof data === 'string') {
    try {
      return JSON.parse(data) as T
    } catch {
      return {} as T
    }
  }
  return (data ?? {}) as T
}

function textMatch(haystack: string | null | undefined, needle: string): boolean {
  if (!needle) return true
  return (haystack ?? '').toLowerCase().includes(needle.toLowerCase())
}

function nowIso(): string {
  return new Date().toISOString()
}

/* ------------------------------------------------------- 列表查询过滤 */

function filterExceptions(params: Record<string, unknown>): typeof exceptionList {
  let items = [...mockState.exceptions]
  const q = String(params.q ?? '')
  if (q) {
    items = items.filter(
      (item) => textMatch(item.order_no, q) || textMatch(item.case_no, q) || textMatch(item.customer_name, q),
    )
  }
  if (params.status) items = items.filter((item) => item.status === params.status)
  if (params.level) items = items.filter((item) => item.level === params.level)
  if (params.type) items = items.filter((item) => item.type === params.type)
  if (params.customer_id) items = items.filter((item) => item.customer_id === Number(params.customer_id))
  if (params.sla_breached !== undefined && params.sla_breached !== '') {
    const flag = params.sla_breached === true || params.sla_breached === 'true'
    items = items.filter((item) => item.sla_breached === flag)
  }
  // 排序（默认 -risk_score,-created_at：主案例在首行，与真实后端一致）
  const sort = String(params.sort ?? '-risk_score,-created_at')
  items.sort((a, b) => {
    for (const raw of sort.split(',')) {
      const field = raw.trim()
      if (!field) continue
      const desc = field.startsWith('-')
      const key = (desc ? field.slice(1) : field) as keyof typeof a
      const av = a[key] ?? 0
      const bv = b[key] ?? 0
      const cmp = String(av).localeCompare(String(bv), 'zh-CN', { numeric: true })
      if (cmp !== 0) return desc ? -cmp : cmp
    }
    return 0
  })
  return items
}

/* ----------------------------------------------------------- 路由匹配 */

interface MockRoute {
  method: string
  pattern: RegExp
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  handler: (m: RegExpMatchArray, config: AxiosRequestConfig, params: Record<string, unknown>) => AxiosResponse<any>
}

const ROUTES: MockRoute[] = [
  /* ------------------------------------------------------------ 系统 */
  { method: 'GET', pattern: /^\/healthz$/, handler: (_m, config) =>
      ok(config, { status: 'ok', db: true, db_error: null, app_env: 'local', clock_mode: 'replay', ai_mode: 'replay', demo_base_date: '2026-09-30T09:00:00+08:00', clock_offset_minutes: demoTickOffset, now_utc: demoNowIso(), model_tables: 22 }) },

  /* -------------------------------------------------------------- 认证 */
  { method: 'POST', pattern: /^\/auth\/register$/, handler: (_m, config) => ok(config, fixtureMe.user, 201) },
  { method: 'POST', pattern: /^\/auth\/login$/, handler: (_m, config) => ok(config, fixtureLogin) },
  { method: 'GET', pattern: /^\/auth\/me$/, handler: (_m, config) => ok(config, fixtureMe) },
  { method: 'POST', pattern: /^\/auth\/logout$/, handler: (_m, config) => ok(config, { ok: true }) },

  /* ------------------------------------------------------------ 工作区 */
  { method: 'GET', pattern: /^\/workspaces$/, handler: (_m, config) => ok(config, [{ ...fixtureMe.workspace, role: 'OPERATOR' }]) },
  { method: 'POST', pattern: /^\/workspaces$/, handler: (_m, config, p) => ok(config, { ...fixtureMe.workspace, name: String(p.name ?? '新工作区') }, 201) },
  { method: 'GET', pattern: /^\/workspaces\/current$/, handler: (_m, config) => ok(config, fixtureMe.workspace) },
  { method: 'GET', pattern: /^\/workspaces\/current\/members$/, handler: (_m, config) => ok(config, membersFixture) },
  { method: 'POST', pattern: /^\/workspaces\/current\/members$/, handler: (_m, config, p) =>
      ok(config, { id: Date.now(), user_id: 99, email: String(p.email ?? ''), name: String(p.name ?? '新成员'), role: p.role ?? 'VIEWER', status: 'ACTIVE', joined_at: nowIso() }, 201) },
  { method: 'PATCH', pattern: /^\/workspaces\/current\/members\/(\d+)$/, handler: (m, config, p) => {
      const target = membersFixture.find((x) => x.id === Number(m[1]))
      return ok(config, { ...(target ?? membersFixture[0]), role: p.role ?? target?.role })
    } },
  { method: 'DELETE', pattern: /^\/workspaces\/current\/members\/(\d+)$/, handler: (_m, config) => ok(config, { ok: true }) },

  /* ------------------------------------------------------------ Dashboard */
  { method: 'GET', pattern: /^\/dashboard\/summary$/, handler: (_m, config) => ok(config, dashboardSummary) },
  { method: 'GET', pattern: /^\/dashboard\/trend$/, handler: (_m, config) => ok(config, dashboardTrend) },

  /* ------------------------------------------------------------ 主数据 */
  { method: 'GET', pattern: /^\/customers$/, handler: (_m, config, p) => {
      let items = customersFixture
      if (p.q) items = items.filter((c) => textMatch(c.name, String(p.q)) || textMatch(c.code, String(p.q)))
      if (p.level) items = items.filter((c) => c.level === p.level)
      return ok(config, paginate(items, p))
    } },
  { method: 'GET', pattern: /^\/customers\/(\d+)$/, handler: (m, config) => ok(config, customersFixture.find((c) => c.id === Number(m[1])) ?? customersFixture[0]) },
  { method: 'POST', pattern: /^\/customers$/, handler: (_m, config, p) => ok(config, { id: Date.now(), workspace_id: 1, version: 1, level: 'NORMAL', status: 'ACTIVE', ...p }, 201) },
  { method: 'PATCH', pattern: /^\/customers\/(\d+)$/, handler: (m, config, p) => ok(config, { ...(customersFixture.find((c) => c.id === Number(m[1])) ?? customersFixture[0]), ...p }) },

  { method: 'GET', pattern: /^\/carriers$/, handler: (_m, config, p) => {
      let items = carriersFixture
      if (p.q) items = items.filter((c) => textMatch(c.name, String(p.q)) || textMatch(c.code, String(p.q)))
      if (p.status) items = items.filter((c) => c.status === p.status)
      return ok(config, paginate(items, p))
    } },
  { method: 'GET', pattern: /^\/carriers\/(\d+)$/, handler: (m, config) => ok(config, carriersFixture.find((c) => c.id === Number(m[1])) ?? carriersFixture[0]) },
  { method: 'POST', pattern: /^\/carriers$/, handler: (_m, config, p) => ok(config, { id: Date.now(), workspace_id: 1, version: 1, status: 'ACTIVE', ...p }, 201) },
  { method: 'PATCH', pattern: /^\/carriers\/(\d+)$/, handler: (m, config, p) => ok(config, { ...(carriersFixture.find((c) => c.id === Number(m[1])) ?? carriersFixture[0]), ...p }) },

  { method: 'GET', pattern: /^\/vehicles$/, handler: (_m, config, p) => {
      let items = vehiclesFixture
      if (p.q) items = items.filter((v) => textMatch(v.plate_no, String(p.q)))
      if (p.status) items = items.filter((v) => v.status === p.status)
      if (p.carrier_id) items = items.filter((v) => v.carrier_id === Number(p.carrier_id))
      return ok(config, paginate(items, p))
    } },
  { method: 'GET', pattern: /^\/vehicles\/(\d+)$/, handler: (m, config) => ok(config, vehiclesFixture.find((v) => v.id === Number(m[1])) ?? vehiclesFixture[0]) },
  { method: 'POST', pattern: /^\/vehicles$/, handler: (_m, config, p) => ok(config, { id: Date.now(), workspace_id: 1, version: 1, status: 'IDLE', ...p }, 201) },
  { method: 'PATCH', pattern: /^\/vehicles\/(\d+)$/, handler: (m, config, p) => ok(config, { ...(vehiclesFixture.find((v) => v.id === Number(m[1])) ?? vehiclesFixture[0]), ...p }) },

  { method: 'GET', pattern: /^\/drivers$/, handler: (_m, config, p) => {
      let items = driversFixture
      if (p.q) items = items.filter((d) => textMatch(d.name, String(p.q)))
      if (p.status) items = items.filter((d) => d.status === p.status)
      if (p.carrier_id) items = items.filter((d) => d.carrier_id === Number(p.carrier_id))
      return ok(config, paginate(items, p))
    } },
  { method: 'GET', pattern: /^\/drivers\/(\d+)$/, handler: (m, config) => ok(config, driversFixture.find((d) => d.id === Number(m[1])) ?? driversFixture[0]) },
  { method: 'POST', pattern: /^\/drivers$/, handler: (_m, config, p) => ok(config, { id: Date.now(), workspace_id: 1, version: 1, status: 'AVAILABLE', ...p }, 201) },
  { method: 'PATCH', pattern: /^\/drivers\/(\d+)$/, handler: (m, config, p) => ok(config, { ...(driversFixture.find((d) => d.id === Number(m[1])) ?? driversFixture[0]), ...p }) },

  /* -------------------------------------------------------------- SLA */
  // 实测返回平数组
  { method: 'GET', pattern: /^\/sla-rules$/, handler: (_m, config) => ok(config, slaRulesFixture) },
  { method: 'POST', pattern: /^\/sla-rules$/, handler: (_m, config, p) => ok(config, { id: Date.now(), workspace_id: 1, version: 1, is_active: true, priority: 100, ...p }, 201) },
  { method: 'PATCH', pattern: /^\/sla-rules\/(\d+)$/, handler: (m, config, p) => ok(config, { ...(slaRulesFixture.find((r) => r.id === Number(m[1])) ?? slaRulesFixture[0]), ...p }) },

  /* ------------------------------------------------------------- 订单 */
  { method: 'GET', pattern: /^\/orders$/, handler: (_m, config, p) => {
      let items = mockState.orderList
      if (p.status) items = items.filter((o) => o.status === p.status)
      if (p.customer_id) items = items.filter((o) => o.customer_id === Number(p.customer_id))
      if (p.order_no) items = items.filter((o) => textMatch(o.order_no, String(p.order_no)))
      return ok(config, paginate(items, p))
    } },
  { method: 'GET', pattern: /^\/orders\/(\d+)$/, handler: (m, config) => {
      const id = Number(m[1])
      const found = mockState.orderList.find((o) => o.id === id)
      if (found) return ok(config, found)
      return ok(config, mockState.order)
    } },
  { method: 'POST', pattern: /^\/orders$/, handler: (_m, config, p) => ok(config, { id: Date.now(), workspace_id: 1, version: 1, status: 'CREATED', ...p }, 201) },
  { method: 'PATCH', pattern: /^\/orders\/(\d+)$/, handler: (m, config, p) => {
      const id = Number(m[1])
      const target = mockState.orderList.find((o) => o.id === id) ?? mockState.order
      const updated = { ...target, ...p, version: (target.version ?? 1) + 1 }
      mockState.orderList = mockState.orderList.map((o) => (o.id === id ? updated : o))
      if (id === mockState.order.id) mockState.order = updated
      return ok(config, updated)
    } },
  // 实测返回分页对象
  { method: 'GET', pattern: /^\/orders\/(\d+)\/tracking-events$/, handler: (_m, config, p) =>
      ok(config, { items: trackingEventsMain, total: trackingEventsMain.length, page: 1, page_size: Number(p.page_size ?? 20) }) },
  { method: 'POST', pattern: /^\/orders\/(\d+)\/tracking-events$/, handler: (m, config, p) => ok(config, {
      id: Date.now(), workspace_id: 1, order_id: Number(m[1]), event_type: p.event_type ?? 'NOTE',
      city: p.city ?? '—', address: p.address ?? null, occurred_at: p.occurred_at ?? nowIso(), source: p.source ?? 'OPERATOR',
      speed_kmh: p.speed_kmh ?? null,
      payload_json: { fixture: true, eta_recalculated: true, detection_ran: true },
    }, 201) },
  // 实测返回平数组（不是 {items:[...]}）
  { method: 'GET', pattern: /^\/orders\/(\d+)\/exceptions$/, handler: (_m, config) =>
      ok(config, mockState.exceptions.filter((e) => e.order_id === mockState.order.id)) },

  /* ------------------------------------------------------------- 异常 */
  { method: 'GET', pattern: /^\/exceptions$/, handler: (_m, config, p) => ok(config, paginate(filterExceptions(p), p)) },
  { method: 'GET', pattern: /^\/exceptions\/(\d+)$/, handler: (m, config) => {
      const id = Number(m[1])
      return ok(config, id === mockState.exception.id ? mockState.exception : { ...mockState.exception, id })
    } },
  { method: 'POST', pattern: /^\/exceptions$/, handler: (_m, config, p) => ok(config, { ...mockState.exception, ...p }, 201) },
  { method: 'PATCH', pattern: /^\/exceptions\/(\d+)$/, handler: (_m, config, p) => {
      mockState.exception = { ...mockState.exception, ...p, version: (mockState.exception.version ?? 1) + 1, updated_at: nowIso() }
      return ok(config, mockState.exception)
    } },
  { method: 'POST', pattern: /^\/exceptions\/(\d+)\/confirm$/, handler: (_m, config) => {
      const from = mockState.exception.status
      mockState.exception = { ...mockState.exception, status: 'PROCESSING', version: (mockState.exception.version ?? 1) + 1, updated_at: nowIso() }
      mockState.events = [...mockState.events, {
        id: Date.now(), workspace_id: 1, exception_id: 1, event_type: 'CONFIRMED', actor_type: 'USER',
        actor_id: 3, from_status: from, to_status: 'PROCESSING', note: '人工确认，进入处理中（本地 fixture）', detail: null, occurred_at: nowIso(),
      }]
      return ok(config, mockState.exception)
    } },
  { method: 'POST', pattern: /^\/exceptions\/(\d+)\/analyze$/, handler: (_m, config) => {
      mockState.analysisSeq += 1
      const analysisId = mockState.analysisSeq
      mockState.analysisRuns.set(analysisId, { startedAt: Date.now() })
      mockState.analysis = currentAnalysis(analysisId)
      // 4 状态模型：分析期间异常状态不变（仍为处理中）
      return ok(config, { analysis_id: analysisId, status: 'PENDING', reused: false, reused_from_id: null, exception_status: mockState.exception.status, input_hash: makeAiAnalysis(analysisId).input_hash, error_code: null, error_message: null }, 202)
    } },
  { method: 'POST', pattern: /^\/exceptions\/(\d+)\/resolve$/, handler: (_m, config, p) => {
      const from = mockState.exception.status
      mockState.exception = { ...mockState.exception, status: 'RESOLVED', resolved_at: nowIso(), version: (mockState.exception.version ?? 1) + 1, updated_at: nowIso() }
      mockState.events = [...mockState.events, {
        id: Date.now(), workspace_id: 1, exception_id: 1, event_type: 'STATUS_CHANGED', actor_type: 'USER',
        actor_id: 3, from_status: from, to_status: 'RESOLVED', note: String(p.note ?? ''), detail: null, occurred_at: nowIso(),
      }]
      return ok(config, mockState.exception)
    } },
  { method: 'POST', pattern: /^\/exceptions\/(\d+)\/close$/, handler: (_m, config, p) => {
      const from = mockState.exception.status
      mockState.exception = {
        ...mockState.exception, status: 'CLOSED', closed_at: nowIso(),
        close_reason: String(p.reason_code ?? 'MANUAL'), version: (mockState.exception.version ?? 1) + 1, updated_at: nowIso(),
      }
      mockState.events = [...mockState.events, {
        id: Date.now(), workspace_id: 1, exception_id: 1, event_type: 'CLOSED', actor_type: 'USER', actor_id: 3,
        from_status: from, to_status: 'CLOSED',
        note: `${String(p.reason_code ?? '')} ${String(p.note ?? '')}`.trim(),
        detail: { reason_code: p.reason_code ?? null }, occurred_at: nowIso(),
      }]
      return ok(config, mockState.exception)
    } },
  { method: 'GET', pattern: /^\/exceptions\/(\d+)\/events$/, handler: (_m, config, p) =>
      ok(config, paginate([...mockState.events].sort((a, b) => Date.parse(b.occurred_at) - Date.parse(a.occurred_at)), p)) },
  { method: 'GET', pattern: /^\/exceptions\/(\d+)\/messages$/, handler: (_m, config) => ok(config, mockState.messages) },
  { method: 'POST', pattern: /^\/exceptions\/(\d+)\/messages$/, handler: (m, config, p) => {
      const raw = String(p.raw_text ?? '')
      const hasRecovery = /(恢复|好了|换好|修好)/.test(raw)
      const message: CarrierMessage = {
        id: Date.now(), workspace_id: 1, exception_id: Number(m[1]), order_id: mockState.order.id,
        channel: (p.channel as CarrierMessage['channel']) ?? 'MANUAL_PASTE',
        sender_name: p.sender_name ? String(p.sender_name) : '本地演示录入',
        sender_role: 'CARRIER', raw_text: raw,
        received_at: p.received_at ? String(p.received_at) : nowIso(),
        parse_status: 'PARSED',
        parse_result: {
          exception_type: /爆胎|故障|坏/.test(raw) ? 'VEHICLE_BREAKDOWN' : 'DELAY_RISK',
          location: /济南/.test(raw) ? '济南' : '未知',
          status: hasRecovery ? 'MOVING' : 'REPAIRING',
          estimated_recovery_at: hasRecovery ? CASE_A.recoveryAt : '2026-09-30T13:00:00Z',
          confidence: 0.78,
          missing_info: hasRecovery ? [] : ['预计恢复时间是否已确认'],
        },
        parser_version: 'v1-fixture',
        created_at: nowIso(),
      }
      mockState.messages = [message, ...mockState.messages]
      mockState.events = [...mockState.events, {
        id: Date.now() + 1, workspace_id: 1, exception_id: Number(m[1]), event_type: 'MESSAGE_ADDED',
        actor_type: 'USER', actor_id: 3, note: '录入承运商消息（本地 fixture，解析结果为规则模拟）',
        detail: { carrier_message_id: message.id, parse_status: 'PARSED' }, occurred_at: nowIso(),
      }]
      return ok(config, message, 201)
    } },

  /* ---------------------------------------------------------- AI 分析 */
  { method: 'GET', pattern: /^\/ai-analyses\/(\d+)$/, handler: (m, config) => {
      const id = Number(m[1])
      const analysis = currentAnalysis(id)
      // 4 状态模型：分析完成时异常已在「处理中」，这里只补 latest_analysis
      if (analysis.status === 'READY') {
        mockState.exception = { ...mockState.exception, status: 'PROCESSING', latest_analysis: exceptionAnalysisSummary }
      }
      return ok(config, analysis)
    } },
  { method: 'GET', pattern: /^\/ai-analyses\/(\d+)\/steps$/, handler: (m, config) =>
      ok(config, currentAnalysis(Number(m[1])).steps ?? aiSteps) },
  { method: 'POST', pattern: /^\/ai-analyses\/(\d+)\/retry$/, handler: (m, config) => {
      const id = Number(m[1])
      mockState.analysisRuns.set(id, { startedAt: Date.now() })
      return ok(config, currentAnalysis(id))
    } },

  /* ------------------------------------------------------------ 审批单 */
  // 实测返回平数组
  { method: 'GET', pattern: /^\/exceptions\/(\d+)\/approvals$/, handler: (_m, config) => ok(config, mockState.approvals) },
  { method: 'POST', pattern: /^\/approvals\/(\d+)\/approve$/, handler: (m, config, p) => {
      const id = Number(m[1])
      const target = mockState.approvals.find((a) => a.id === id)
      if (!target) return ok(config, { error: { code: 'RESOURCE_NOT_FOUND', message: '审批单不存在' } }, 404)
      const aiPayload = target.ai_payload ?? {}
      const finalPayload = parseBody<Record<string, unknown>>(p.final_payload ?? {})
      const merged = { ...aiPayload, ...finalPayload }
      const changed = Object.keys(finalPayload).filter(
        (key) => JSON.stringify(aiPayload[key]) !== JSON.stringify(finalPayload[key]),
      )
      // diff 形状与真实后端一致：fields 是 {field:{ai,final}} 的 map
      const diff = {
        changed,
        fields: Object.fromEntries(
          changed.map((field) => [field, { ai: aiPayload[field], final: finalPayload[field] }]),
        ),
        ai_value: aiPayload,
        final_value: merged,
        ai_payload: aiPayload,
        final_payload: merged,
      }
      const executionResult = {
        order_id: mockState.order.id,
        updated_fields: changed.length ? changed : ['current_eta_at'],
        current_eta_at: merged.eta_at ?? mockState.order.current_eta_at ?? null,
        audit_log_id: Date.now(),
        fixture: true,
      }
      const updated: Approval = {
        ...target,
        status: 'EXECUTED',
        final_payload: merged,
        diff,
        decided_by: 3,
        decided_at: nowIso(),
        executed_at: nowIso(),
        execution_result: executionResult,
        version: target.version + 1,
        updated_at: nowIso(),
      }
      mockState.approvals = mockState.approvals.map((a) => (a.id === id ? updated : a))
      if (typeof merged.eta_at === 'string') {
        mockState.order = { ...mockState.order, current_eta_at: merged.eta_at }
        mockState.exception = { ...mockState.exception, current_eta_at: merged.eta_at, expected_eta_at: merged.eta_at, status: 'PROCESSING' }
        mockState.orderList = mockState.orderList.map((o) => (o.id === mockState.order.id ? mockState.order : o))
      }
      mockState.events = [...mockState.events,
        { id: Date.now(), workspace_id: 1, exception_id: 1, event_type: 'APPROVED', actor_type: 'USER', actor_id: 3, note: `批准审批单 #${id}`, detail: { approval_id: id, changed }, occurred_at: nowIso() },
        { id: Date.now() + 1, workspace_id: 1, exception_id: 1, event_type: 'EXECUTED', actor_type: 'SYSTEM', actor_id: null, note: `执行审批单 #${id}（本地 fixture）`, detail: executionResult, occurred_at: nowIso() },
      ]
      // 真实 approve 的响应形状
      return ok(config, { id, status: 'EXECUTED', diff, execution_result: executionResult, error_message: null, reject_reason: null })
    } },
  { method: 'POST', pattern: /^\/approvals\/(\d+)\/reject$/, handler: (m, config, p) => {
      const id = Number(m[1])
      const target = mockState.approvals.find((a) => a.id === id)
      if (!target) return ok(config, { error: { code: 'RESOURCE_NOT_FOUND', message: '审批单不存在' } }, 404)
      const updated: Approval = {
        ...target,
        status: 'REJECTED',
        reject_reason: String(p.reason ?? ''),
        decided_by: 3,
        decided_at: nowIso(),
        version: target.version + 1,
        updated_at: nowIso(),
      }
      mockState.approvals = mockState.approvals.map((a) => (a.id === id ? updated : a))
      mockState.events = [...mockState.events, {
        id: Date.now(), workspace_id: 1, exception_id: 1, event_type: 'REJECTED', actor_type: 'USER',
        actor_id: 3, note: `驳回审批单 #${id}：${String(p.reason ?? '')}`,
        detail: { approval_id: id, reason: String(p.reason ?? '') }, occurred_at: nowIso(),
      }]
      return ok(config, { id, status: 'REJECTED', diff: null, execution_result: null, error_message: null, reject_reason: String(p.reason ?? '') })
    } },
  { method: 'POST', pattern: /^\/approvals\/batch-approve$/, handler: (_m, config, p) => {
      const ids = (p.approval_ids as number[]) ?? []
      const items = ids.map((id) => {
        const target = mockState.approvals.find((a) => a.id === id)
        if (!target) return { approval_id: id, status: 'FAILED' as const, error: '审批单不存在' }
        mockState.approvals = mockState.approvals.map((a) =>
          a.id === id ? { ...a, status: 'EXECUTED' as const, executed_at: nowIso(), version: a.version + 1 } : a,
        )
        return { approval_id: id, status: 'EXECUTED' as const, execution_result: { fixture: true } }
      })
      return ok(config, {
        items,
        succeeded: items.filter((i) => i.status === 'EXECUTED').length,
        failed: items.filter((i) => i.status !== 'EXECUTED').length,
      })
    } },
  { method: 'POST', pattern: /^\/approvals\/(\d+)\/execute$/, handler: (m, config) => {
      const id = Number(m[1])
      const target = mockState.approvals.find((a) => a.id === id) ?? mockState.approvals[0]
      const updated: Approval = {
        ...target, status: 'EXECUTED', executed_at: nowIso(),
        retry_count: (target.retry_count ?? 0) + 1, version: target.version + 1,
      }
      mockState.approvals = mockState.approvals.map((a) => (a.id === id ? updated : a))
      return ok(config, { id, status: 'EXECUTED', diff: updated.diff ?? null, execution_result: updated.execution_result ?? null, error_message: null, reject_reason: null })
    } },

  /* ---------------------------------------------------------- 跟进任务 */
  // 实测返回 {items,total}
  { method: 'GET', pattern: /^\/exceptions\/(\d+)\/followups$/, handler: (_m, config) =>
      ok(config, { items: mockState.followups, total: mockState.followups.length }) },
  { method: 'POST', pattern: /^\/followups$/, handler: (_m, config, p) => {
      const task: FollowupTask = {
        id: Date.now(), workspace_id: 1, exception_id: Number(p.exception_id ?? 1), title: String(p.title ?? '跟进任务'),
        content: p.content ? String(p.content) : null, assignee_user_id: (p.assignee_user_id as number) ?? 3,
        assignee_name: '张三', due_at: (p.due_at as string) ?? null, priority: String(p.priority ?? 'NORMAL'),
        status: 'OPEN', source: 'MANUAL', version: 1, created_at: nowIso(), updated_at: nowIso(),
      }
      mockState.followups = [...mockState.followups, task]
      return ok(config, task, 201)
    } },
  { method: 'PATCH', pattern: /^\/followups\/(\d+)$/, handler: (m, config, p) => {
      const id = Number(m[1])
      const target = mockState.followups.find((f) => f.id === id)
      const updated: FollowupTask = {
        ...(target ?? mockState.followups[0]),
        ...(p as Partial<FollowupTask>),
        done_at: p.status === 'DONE' ? nowIso() : target?.done_at ?? null,
        done_by: p.status === 'DONE' ? 3 : target?.done_by ?? null,
        version: (target?.version ?? 1) + 1,
        updated_at: nowIso(),
      }
      mockState.followups = target ? mockState.followups.map((f) => (f.id === id ? updated : f)) : [...mockState.followups, updated]
      if (p.status === 'DONE') {
        mockState.events = [...mockState.events, {
          id: Date.now(), workspace_id: 1, exception_id: 1, event_type: 'FOLLOWUP_DONE', actor_type: 'USER',
          actor_id: 3, note: `完成跟进任务：${updated.title}`, detail: { followup_task_id: updated.id }, occurred_at: nowIso(),
        }]
      }
      return ok(config, updated)
    } },

  /* -------------------------------------------------------------- 通知 */
  // 实测返回平数组
  { method: 'GET', pattern: /^\/exceptions\/(\d+)\/notifications$/, handler: (_m, config) => ok(config, mockState.notifications) },
  { method: 'GET', pattern: /^\/notifications\/(\d+)$/, handler: (m, config) =>
      ok(config, mockState.notifications.find((n) => n.id === Number(m[1])) ?? mockState.notifications[0]) },
  { method: 'PATCH', pattern: /^\/notifications\/(\d+)$/, handler: (m, config, p) => {
      const id = Number(m[1])
      const target = mockState.notifications.find((n) => n.id === id)
      const updated: Notification = {
        ...(target ?? mockState.notifications[0]),
        content: String(p.content ?? target?.content ?? ''),
        subject: p.subject ? String(p.subject) : target?.subject ?? null,
        status: 'DRAFT',
        version: (target?.version ?? 1) + 1,
        updated_at: nowIso(),
      }
      mockState.notifications = target ? mockState.notifications.map((n) => (n.id === id ? updated : n)) : [updated]
      return ok(config, updated)
    } },
  { method: 'POST', pattern: /^\/notifications\/(\d+)\/mark-sent$/, handler: (m, config) => {
      const id = Number(m[1])
      const target = mockState.notifications.find((n) => n.id === id) ?? mockState.notifications[0]
      const updated: Notification = { ...target, status: 'SENT_MOCK', sent_at: nowIso(), version: (target.version ?? 1) + 1 }
      mockState.notifications = mockState.notifications.map((n) => (n.id === id ? updated : n))
      return ok(config, updated)
    } },
  { method: 'POST', pattern: /^\/notifications\/(\d+)\/skip$/, handler: (m, config) => {
      const id = Number(m[1])
      const target = mockState.notifications.find((n) => n.id === id) ?? mockState.notifications[0]
      const updated: Notification = { ...target, status: 'SKIPPED', version: (target.version ?? 1) + 1 }
      mockState.notifications = mockState.notifications.map((n) => (n.id === id ? updated : n))
      return ok(config, updated)
    } },

  /* -------------------------------------------------------------- 审计 */
  { method: 'GET', pattern: /^\/audit-logs$/, handler: (_m, config, p) => {
      let items = auditLogsFixture
      if (p.action) items = items.filter((a) => textMatch(a.action, String(p.action)))
      if (p.actor_id) items = items.filter((a) => a.actor_id === Number(p.actor_id))
      if (p.resource_type) items = items.filter((a) => a.resource_type === p.resource_type)
      if (p.resource_id) items = items.filter((a) => a.resource_id === Number(p.resource_id))
      return ok(config, paginate(items, p))
    } },

  /* ------------------------------------------------------------ 知识库 */
  { method: 'GET', pattern: /^\/knowledge\/docs$/, handler: (_m, config, p) => {
      const page = paginate(knowledgeDocs, p)
      const chunkTotal = knowledgeDocs.reduce((sum, d) => sum + (d.chunk_count ?? 0), 0)
      return ok(config, { ...page, chunk_total: chunkTotal })
    } },
  { method: 'GET', pattern: /^\/knowledge\/docs\/(\d+)$/, handler: (m, config) => {
      const id = Number(m[1])
      const doc = knowledgeDocs.find((d) => d.id === id) ?? knowledgeDocs[0]
      const chunks: KnowledgeChunk[] = knowledgeDocChunks[doc.id] ?? []
      return ok(config, { ...doc, chunks })
    } },
  { method: 'POST', pattern: /^\/knowledge\/reindex$/, handler: (_m, config) =>
      ok(config, { docs: knowledgeDocs.length, chunks: knowledgeDocs.reduce((sum, d) => sum + (d.chunk_count ?? 0), 0), duration_ms: 420 }) },

  /* --------------------------------------------------------------- Demo */
  { method: 'GET', pattern: /^\/demo\/state$/, handler: (_m, config) => ok(config, demoStateResponse()) },
  { method: 'POST', pattern: /^\/demo\/actions\/tick$/, handler: (_m, config, p) => {
      const minutes = Number(p.minutes ?? 60)
      demoTickOffset += minutes
      return ok(config, { ...demoStateResponse(), events: { tracking_created: minutes, exceptions_detected: 0, approvals_expired: 0, exceptions_closed: 0 } })
    } },
  { method: 'POST', pattern: /^\/demo\/actions\/advance-to-less$/, handler: (_m, config) => {
      demoTickOffset += 600
      mockState.exception = { ...mockState.exception, status: 'CLOSED', closed_at: nowIso(), close_reason: 'DELIVERED', version: (mockState.exception.version ?? 1) + 1 }
      mockState.events = [...mockState.events, {
        id: Date.now(), workspace_id: 1, exception_id: 1, event_type: 'CLOSED', actor_type: 'SYSTEM', actor_id: null,
        from_status: 'PROCESSING', to_status: 'CLOSED', note: '订单送达 24h 后自动关闭（本地 fixture）',
        detail: { reason_code: 'DELIVERED' }, occurred_at: nowIso(),
      }]
      return ok(config, { ...demoStateResponse(), delivered: true, closed_exceptions: 1 })
    } },
  { method: 'POST', pattern: /^\/demo\/actions\/reset$/, handler: (_m, config, p) => {
      resetMockState()
      return ok(config, { reset: true, scenario: String(p.scenario ?? 'case-a'), summary: { fixture: true } })
    } },
]

let demoTickOffset = 0

function demoNowIso(): string {
  return new Date(Date.parse(demoState.now_utc) + demoTickOffset * 60000).toISOString()
}

function demoStateResponse() {
  return { ...demoState, offset_minutes: demoTickOffset, now_utc: demoNowIso() }
}

/* --------------------------------------------------------------- 入口 */

export async function resolveMock(config: AxiosRequestConfig): Promise<AxiosResponse | null> {
  const url = (config.url ?? '').split('?')[0]
  const method = (config.method ?? 'GET').toUpperCase()
  const params = (config.params ?? {}) as Record<string, unknown>

  for (const route of ROUTES) {
    if (route.method !== method) continue
    const match = url.match(route.pattern)
    if (!match) continue
    // 轻微延迟，模拟网络往返（让 loading 骨架可见）
    await new Promise((resolve) => setTimeout(resolve, 60))
    return route.handler(match, config, params)
  }
  return null
}

/** 供页面查询：本地兜底是否可用 */
export const MOCK_AVAILABLE = ROUTES.length > 0
