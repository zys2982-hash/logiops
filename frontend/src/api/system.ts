import { get } from './request'
import { normalizePage } from './master'
import type {
  AuditLog,
  AuditQuery,
  DashboardSummary,
  DashboardTrend,
  HealthStatus,
  KnowledgeDoc,
  KnowledgeDocDetail,
  Page as PageType,
  ReindexResult,
} from '@/types'
import { post } from './request'

/* ------------------------------------------------------------ Dashboard */

/** GET /dashboard/summary 今日订单/运输中/异常/高风险/待处理/已解决 + SLA 违约数 */
export function getDashboardSummary(): Promise<DashboardSummary> {
  return get<DashboardSummary>('/dashboard/summary')
}

/** GET /dashboard/trend 近 7 天异常与违约趋势
 *  实测：{days,start_date,end_date,items,trend}，趋势点是 {date,detected,breached,resolved,closed} */
export async function getDashboardTrend(): Promise<DashboardTrend> {
  const raw = await get<DashboardTrend>('/dashboard/trend')
  return { ...raw, items: raw.items ?? raw.trend ?? [] }
}

/* ---------------------------------------------------------------- 审计 */

/** GET /audit-logs ?resource_type&resource_id&actor_id&action&occurred_from&occurred_to&page */
export async function listAuditLogs(query: AuditQuery = {}): Promise<PageType<AuditLog>> {
  const raw = await get<PageType<AuditLog> | AuditLog[]>('/audit-logs', { ...query })
  return normalizePage(raw, query.page ?? 1, query.page_size ?? 20)
}

/* -------------------------------------------------------------- 知识库 */

/** GET /knowledge/docs 列出文档与分片数 */
export async function listKnowledgeDocs(
  query: { page?: number; page_size?: number } = {},
): Promise<PageType<KnowledgeDoc>> {
  const raw = await get<PageType<KnowledgeDoc> | KnowledgeDoc[]>('/knowledge/docs', { ...query })
  return normalizePage(raw, query.page ?? 1, query.page_size ?? 50)
}

/** GET /knowledge/docs/{id} 文档 + 分片原文
 *  ⚠️ 实测后端该端点返回 404 RESOURCE_NOT_FOUND，这里降级为空分片而不是让页面报错。 */
export async function getKnowledgeDoc(id: number): Promise<KnowledgeDocDetail> {
  try {
    return await get<KnowledgeDocDetail>(`/knowledge/docs/${id}`)
  } catch {
    return { id, title: '', chunks: [] }
  }
}

/** POST /knowledge/reindex 从 backend/app/knowledge/*.md 重建分片（ADMIN+） */
export function reindexKnowledge(): Promise<ReindexResult> {
  return post<ReindexResult>('/knowledge/reindex')
}

/* ------------------------------------------------------------ 系统健康 */

/** GET /api/v1/healthz（实测：db 是布尔，另有 app_env/demo_base_date/clock_offset_minutes/model_tables） */
export function healthz(): Promise<HealthStatus> {
  return get<HealthStatus>('/healthz')
}
