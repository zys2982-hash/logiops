import { get, patch, post } from './request'
import { normalizePage } from './master'
import type {
  AiAnalysis,
  AiAnalysisStep,
  AnalyzeStartResult,
  Approval,
  ApprovalApprovePayload,
  ApprovalDecisionResult,
  ApprovalRejectPayload,
  BatchApprovePayload,
  BatchApproveResult,
  CarrierMessage,
  CarrierMessageCreatePayload,
  CarrierMessageCreateResult,
  ClosePayload,
  ExceptionCreatePayload,
  ExceptionDetail,
  ExceptionEvent,
  ExceptionListItem,
  ExceptionQuery,
  FollowupCreatePayload,
  FollowupTask,
  FollowupUpdatePayload,
  Notification,
  NotificationUpdatePayload,
  Page as PageType,
  ReasonPayload,
  SkipPayload,
} from '@/types'

/* ---------------------------------------------------------------- 异常 */

/** GET /exceptions ?status&level&type&customer_id&sla_breached&assigned_to&sort&page */
export async function listExceptions(
  query: ExceptionQuery = {},
): Promise<PageType<ExceptionListItem>> {
  const raw = await get<PageType<ExceptionListItem> | ExceptionListItem[]>('/exceptions', {
    ...query,
  })
  return normalizePage(raw, query.page ?? 1, query.page_size ?? 20)
}

/** GET /exceptions/{id} → 主单 + 订单/客户/车辆快照 + 最新 ETA + 最新分析摘要 */
export function getException(id: number): Promise<ExceptionDetail> {
  return get<ExceptionDetail>(`/exceptions/${id}`)
}

/** POST /exceptions 手工建单（MANUAL，ADMIN+） */
export function createException(payload: ExceptionCreatePayload): Promise<ExceptionDetail> {
  return post<ExceptionDetail>('/exceptions', payload)
}

/** PATCH /exceptions/{id}（改 assigned_to / remark，不改 status） */
export function updateException(
  id: number,
  payload: { assigned_to?: number | null; remark?: string; expected_version?: number },
): Promise<ExceptionDetail> {
  return patch<ExceptionDetail>(`/exceptions/${id}`, payload)
}

/* ------------------------------------------------------ 异常状态机动作 */

/** POST /exceptions/{id}/confirm  DETECTED → CONFIRMING */
export function confirmException(
  id: number,
  payload: { expected_version?: number } = {},
): Promise<ExceptionDetail> {
  return post<ExceptionDetail>(`/exceptions/${id}/confirm`, payload)
}

/** POST /exceptions/{id}/analyze  CONFIRMING → ANALYZING，202 + {analysis_id} */
export function analyzeException(
  id: number,
  payload: { expected_version?: number } = {},
): Promise<AnalyzeStartResult> {
  return post<AnalyzeStartResult>(`/exceptions/${id}/analyze`, payload)
}

/** POST /exceptions/{id}/resolve  → RESOLVED（body: note） */
export function resolveException(id: number, payload: ReasonPayload): Promise<ExceptionDetail> {
  return post<ExceptionDetail>(`/exceptions/${id}/resolve`, payload)
}

/** POST /exceptions/{id}/delay  录入/修改人工延误（人报事实；是否违约仍由规则判） */
export function recordDelay(
  id: number,
  payload: { expected_version: number; delay_minutes: number; note?: string },
): Promise<ExceptionDetail> {
  return post<ExceptionDetail>(`/exceptions/${id}/delay`, payload)
}

/** POST /exceptions/{id}/close  → CLOSED（body: reason_code, note） */
export function closeException(id: number, payload: ClosePayload): Promise<ExceptionDetail> {
  return post<ExceptionDetail>(`/exceptions/${id}/close`, payload)
}

/* ------------------------------------------------------------ 时间线 */

/** GET /exceptions/{id}/events（分页） */
export async function listExceptionEvents(
  id: number,
  query: { page?: number; page_size?: number } = {},
): Promise<PageType<ExceptionEvent>> {
  const raw = await get<PageType<ExceptionEvent> | ExceptionEvent[]>(`/exceptions/${id}/events`, {
    ...query,
  })
  return normalizePage(raw, query.page ?? 1, query.page_size ?? 50)
}

/** GET /exceptions/{id}/messages */
export function listCarrierMessages(id: number): Promise<CarrierMessage[]> {
  return get<CarrierMessage[]>(`/exceptions/${id}/messages`)
}

/** POST /exceptions/{id}/messages（录入消息 → 触发解析；实测返回 {message_id,parse_status,parse_result,eta,...}） */
export function createCarrierMessage(
  id: number,
  payload: CarrierMessageCreatePayload,
): Promise<CarrierMessageCreateResult> {
  return post<CarrierMessageCreateResult>(`/exceptions/${id}/messages`, payload)
}

/* ------------------------------------------------------------ AI 分析 */

/** GET /ai-analyses/{id}（前端每 1.5s 轮询此接口） */
export function getAiAnalysis(id: number): Promise<AiAnalysis> {
  return get<AiAnalysis>(`/ai-analyses/${id}`)
}

/** GET /ai-analyses/{id}/steps 工具调用明细 */
export function listAiAnalysisSteps(id: number): Promise<AiAnalysisStep[]> {
  return get<AiAnalysisStep[]>(`/ai-analyses/${id}/steps`)
}

/** POST /ai-analyses/{id}/retry  FAILED 时重跑（复用 input_hash） */
export function retryAiAnalysis(id: number): Promise<AiAnalysis> {
  return post<AiAnalysis>(`/ai-analyses/${id}/retry`)
}

/* -------------------------------------------------------------- 审批 */

/** GET /exceptions/{id}/approvals */
export async function listApprovals(
  exceptionId: number,
  query: { page?: number; page_size?: number } = {},
): Promise<PageType<Approval>> {
  const raw = await get<PageType<Approval> | Approval[]>(`/exceptions/${exceptionId}/approvals`, {
    ...query,
  })
  return normalizePage(raw, query.page ?? 1, query.page_size ?? 50)
}

/** POST /approvals/{id}/approve body: {expected_version, final_payload}（可修改 AI 建议）
 *  实测响应：{id,status,diff,execution_result,error_message,reject_reason} */
export function approveApproval(
  id: number,
  payload: ApprovalApprovePayload,
): Promise<ApprovalDecisionResult> {
  return post<ApprovalDecisionResult>(`/approvals/${id}/approve`, payload)
}

/** POST /approvals/{id}/reject body: {expected_version, reason} */
export function rejectApproval(
  id: number,
  payload: ApprovalRejectPayload,
): Promise<ApprovalDecisionResult> {
  return post<ApprovalDecisionResult>(`/approvals/${id}/reject`, payload)
}

/** POST /approvals/batch-approve → 逐条执行，返回每条成败 */
export function batchApprove(payload: BatchApprovePayload): Promise<BatchApproveResult> {
  return post<BatchApproveResult>('/approvals/batch-approve', payload)
}

/** POST /approvals/{id}/execute（针对 FAILED 的重试执行） */
export function executeApproval(id: number): Promise<ApprovalDecisionResult> {
  return post<ApprovalDecisionResult>(`/approvals/${id}/execute`)
}

/* ------------------------------------------------------------ 跟进任务 */

/** GET /exceptions/{id}/followups → 实测返回 {items,total}（兼容平数组） */
export async function listFollowups(exceptionId: number): Promise<FollowupTask[]> {
  const raw = await get<PageType<FollowupTask> | FollowupTask[]>(
    `/exceptions/${exceptionId}/followups`,
  )
  return Array.isArray(raw) ? raw : (raw?.items ?? [])
}

/** POST /followups 手工建（source=MANUAL） */
export function createFollowup(payload: FollowupCreatePayload): Promise<FollowupTask> {
  return post<FollowupTask>('/followups', payload)
}

/** PATCH /followups/{id} 改 assignee/due_at/status（DONE 记 done_by/done_at） */
export function updateFollowup(id: number, payload: FollowupUpdatePayload): Promise<FollowupTask> {
  return patch<FollowupTask>(`/followups/${id}`, payload)
}

/* -------------------------------------------------------------- 通知 */

/** GET /exceptions/{id}/notifications */
export function listNotifications(exceptionId: number): Promise<Notification[]> {
  return get<Notification[]>(`/exceptions/${exceptionId}/notifications`)
}

/** GET /notifications/{id} */
export function getNotification(id: number): Promise<Notification> {
  return get<Notification>(`/notifications/${id}`)
}

/** PATCH /notifications/{id} 编辑正文（仅 DRAFT 可改，保留 ai_draft_content） */
export function updateNotification(
  id: number,
  payload: NotificationUpdatePayload,
): Promise<Notification> {
  return patch<Notification>(`/notifications/${id}`, payload)
}

/** POST /notifications/{id}/mark-sent 模拟发送 → SENT_MOCK + 审计 */
export function markNotificationSent(id: number): Promise<Notification> {
  return post<Notification>(`/notifications/${id}/mark-sent`)
}

/** POST /notifications/{id}/skip → SKIPPED（需 reason） */
export function skipNotification(id: number, payload: SkipPayload): Promise<Notification> {
  return post<Notification>(`/notifications/${id}/skip`, payload)
}
