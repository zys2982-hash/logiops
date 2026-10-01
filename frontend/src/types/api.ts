/**
 * 接口契约类型（基线 §10 + §7.4 字段定义）。
 * 手写、显式、snake_case；与后端 schema 一一对应，遵守"前端在 types 层做映射，不做运行时转换"。
 *
 * ⚠️ 命名以 `backend/app/schemas/*` 的**实际响应**为准（2026-09-30 用真实后端逐端点核对过）：
 *    - AI 分析结果字段是 `output`（不是 output_json）；step 参数是 `args`
 *    - 审批单是 `ai_payload / final_payload / diff / execution_result`（不带 _json 后缀）
 *    - 承运商消息是 `parse_result`（不是 parse_result_json）
 *    - 异常时间线是 `detail`（不是 detail_json）
 *    - 订单是**扁平字段**（customer_name/vehicle_plate/carrier_name/driver_name/customer_level/sla）
 *    - dashboard 用 `pending`；trend 点是 `detected/breached`
 */

import type {
  ActorType,
  AnalysisStatus,
  ApprovalAction,
  ApprovalStatus,
  CarrierStatus,
  CustomerLevel,
  DetectionRule,
  DriverStatus,
  ExceptionEventType,
  ExceptionLevel,
  ExceptionStatus,
  ExceptionType,
  FollowupSource,
  FollowupStatus,
  MemberStatus,
  MessageChannel,
  NotificationChannel,
  NotificationStatus,
  OrderStatus,
  ParseStatus,
  Perm,
  Role,
  SlaScopeType,
  StepStatus,
  TrackingEventType,
  TrackingSource,
  VehicleStatus,
} from './enums'

/* ------------------------------------------------------------------ 通用 */

/** 分页响应（§10.1）：{items,total,page,page_size}；部分端点只返回 {items,total} */
export interface Page<T> {
  items: T[]
  total: number
  page?: number
  page_size?: number
}

/** 统一错误体（§10.1）：{"error":{"code","message","details"}} */
export interface ApiErrorBody {
  error: {
    code: string
    message: string
    details?: Record<string, unknown>
  }
}

/** 列表通用查询参数 */
export interface PageQuery {
  page?: number
  page_size?: number
  /** 排序，如 "-risk_score,created_at"（- 为倒序；非法字段后端 422） */
  sort?: string
}

/* ------------------------------------------------------------------ 认证 */

export interface User {
  id: number
  email: string
  name: string
  phone?: string | null
  avatar_url?: string | null
  status: string
  last_login_at?: string | null
  created_at?: string | null
  updated_at?: string | null
}

export interface RegisterPayload {
  email: string
  password: string
  name: string
}

export interface LoginPayload {
  email: string
  password: string
}

export interface LoginResult {
  access_token: string
  token_type: string
  expires_in: number
  user: User
}

/** GET /auth/me → {user, role, permissions, workspace_id, workspace, workspaces}（实测形状） */
export interface MeResult {
  user: User
  role: Role
  permissions: Perm[]
  workspace_id: number | null
  /** 当前工作区（单数；workspaces 数组里的每一项也带 role） */
  workspace: Workspace | null
  workspaces: Workspace[]
}

/* ---------------------------------------------------------------- 工作区 */

export interface Workspace {
  id: number
  name: string
  code: string
  owner_user_id?: number
  status?: string
  /** /auth/me 与 /workspaces 返回的"我在该工作区的角色" */
  role?: Role
  created_at?: string | null
  updated_at?: string | null
}

/** GET /workspaces/current/members（实测：平数组，无 workspace_id） */
export interface WorkspaceMember {
  id: number
  user_id: number
  email: string
  name: string
  role: Role
  status: MemberStatus
  joined_at?: string | null
  created_at?: string | null
  workspace_id?: number
  invited_by?: number | null
}

export interface MemberCreatePayload {
  email: string
  role: Role
  name?: string
}

export interface MemberUpdatePayload {
  role: Role
  expected_version?: number
}

/* ---------------------------------------------------------------- 主数据 */

export interface Customer {
  id: number
  workspace_id: number
  code: string
  name: string
  level: CustomerLevel
  contact_name?: string | null
  /** 后端已脱敏（138****0005） */
  contact_phone?: string | null
  contact_email?: string | null
  notify_pref?: string | null
  remark?: string | null
  status?: string | null
  version?: number
  is_deleted?: boolean
  created_at?: string | null
  updated_at?: string | null
}

export interface Carrier {
  id: number
  workspace_id: number
  code?: string | null
  name: string
  contact_name?: string | null
  contact_phone?: string | null
  service_level?: string | null
  status: CarrierStatus
  remark?: string | null
  version?: number
  created_at?: string | null
  updated_at?: string | null
}

export interface Vehicle {
  id: number
  workspace_id: number
  plate_no: string
  vehicle_type?: string | null
  capacity_ton?: number | null
  carrier_id?: number | null
  status: VehicleStatus
  current_driver_id?: number | null
  current_city?: string | null
  remark?: string | null
  version?: number
  created_at?: string | null
  updated_at?: string | null
}

export interface Driver {
  id: number
  workspace_id: number
  name: string
  phone?: string | null
  carrier_id?: number | null
  license_no?: string | null
  status: DriverStatus
  version?: number
  created_at?: string | null
  updated_at?: string | null
}

export interface CustomerQuery extends PageQuery {
  q?: string
  level?: CustomerLevel | ''
}

export interface CarrierQuery extends PageQuery {
  q?: string
  status?: CarrierStatus | ''
}

export interface VehicleQuery extends PageQuery {
  /** 实测/后端契约：车辆按 plate_no 过滤（不是 q） */
  plate_no?: string
  status?: VehicleStatus | ''
  carrier_id?: number | ''
  driver_id?: number | ''
}

export interface DriverQuery extends PageQuery {
  q?: string
  status?: DriverStatus | ''
  carrier_id?: number | ''
}

/* ------------------------------------------------------------------ SLA */

export interface SlaRule {
  id: number
  workspace_id: number
  name: string
  scope_type: SlaScopeType
  scope_value?: string | null
  deadline_offset_hours: number
  max_delay_minutes: number
  priority: number
  action_policy_json?: Record<string, unknown> | null
  description?: string | null
  is_active: boolean
  version?: number
  created_at?: string | null
  updated_at?: string | null
}

/** 订单/异常详情里内嵌的 SLA 快照（实测字段） */
export interface SlaSnapshot {
  rule_id?: number
  rule_name?: string
  scope_type?: SlaScopeType
  scope_value?: string | null
  deadline_offset_hours?: number
  max_delay_minutes?: number
  promised_delivery_at?: string | null
  expected_eta_at?: string | null
  delay_minutes?: number | null
  breached?: boolean
}

/* ------------------------------------------------------------------ 订单 */

export interface TrackingEvent {
  id: number
  workspace_id?: number
  order_id: number
  event_type: TrackingEventType
  city?: string | null
  address?: string | null
  lat?: number | null
  lng?: number | null
  occurred_at: string
  source: TrackingSource
  speed_kmh?: number | null
  payload_json?: Record<string, unknown> | null
  created_at?: string | null
}

/**
 * GET /orders 列表项与 GET /orders/{id} 都是**扁平字段**（实测）：
 * customer_name/customer_code/customer_level/vehicle_plate/carrier_name/driver_name + sla + open_exception
 */
export interface Order {
  id: number
  workspace_id?: number
  order_no: string
  status: OrderStatus
  customer_id: number
  customer_name?: string | null
  customer_code?: string | null
  customer_level?: CustomerLevel | null
  carrier_id?: number | null
  carrier_name?: string | null
  vehicle_id?: number | null
  vehicle_plate?: string | null
  driver_id?: number | null
  driver_name?: string | null
  origin_city?: string | null
  dest_city?: string | null
  cargo_desc?: string | null
  weight_ton?: number | null
  distance_km?: number | null
  sla_rule_id?: number | null
  dispatched_at?: string | null
  promised_delivery_at?: string | null
  original_eta_at?: string | null
  current_eta_at?: string | null
  delivered_at?: string | null
  last_tracking_at?: string | null
  remark?: string | null
  version?: number
  created_at?: string | null
  updated_at?: string | null
  /** 详情附带：SLA 快照与当前未关闭异常摘要 */
  sla?: SlaSnapshot | null
  open_exception?: ExceptionBrief | null
}

export type OrderBrief = Order

export interface OrderQuery extends PageQuery {
  status?: OrderStatus | ''
  customer_id?: number | ''
  order_no?: string
  created_from?: string
  created_to?: string
}

export interface OrderCreatePayload {
  order_no?: string
  customer_id: number
  origin_city: string
  dest_city: string
  cargo_desc?: string
  weight_ton?: number
  distance_km?: number
  carrier_id?: number | null
  vehicle_id?: number | null
  driver_id?: number | null
  promised_delivery_at?: string
  remark?: string
}

export interface OrderUpdatePayload {
  expected_version?: number
  carrier_id?: number | null
  vehicle_id?: number | null
  driver_id?: number | null
  remark?: string
  status?: OrderStatus
}

export interface TrackingEventCreatePayload {
  event_type: TrackingEventType
  city: string
  address?: string
  occurred_at?: string
  source?: TrackingSource
  speed_kmh?: number
  payload_json?: Record<string, unknown>
}

/* ------------------------------------------------------------------ 异常 */

/** 根因（实测是对象，不是 root_cause_code/root_cause_note 两个字段） */
export interface RootCause {
  code: string
  note?: string | null
}

/** 风险因子（§8.6：risk_factors = [{code,label,weight,detail}]） */
export interface RiskFactor {
  code: string
  label: string
  weight: number
  detail?: string | null
}

/** 列表/详情共用的异常字段（列表为扁平摘要，详情额外带嵌套对象） */
export interface ExceptionListItem {
  id: number
  case_no: string
  order_id: number
  order_no?: string | null
  customer_id: number
  customer_name?: string | null
  /** 列表不返回，详情才返回（列表请用 customer?.level 或忽略） */
  customer_level?: CustomerLevel | null
  vehicle_id?: number | null
  vehicle_plate?: string | null
  carrier_id?: number | null
  type: ExceptionType
  level: ExceptionLevel
  status: ExceptionStatus
  detected_by?: ActorType | string | null
  detection_rule?: DetectionRule | string | null
  occurred_at: string
  stall_since?: string | null
  root_cause?: RootCause | null
  impact_summary?: string | null
  promised_delivery_at?: string | null
  current_eta_at?: string | null
  expected_eta_at?: string | null
  sla_delay_minutes?: number | null
  sla_breached: boolean
  risk_score?: number | null
  risk_factors?: RiskFactor[] | null
  assigned_to?: number | null
  resolved_at?: string | null
  closed_at?: string | null
  close_reason?: string | null
  merged_count?: number | null
  version?: number
  created_at?: string | null
  updated_at?: string | null
  /** 详情附带（列表为 null） */
  order?: Order | null
  customer?: Customer | null
  vehicle?: Vehicle | null
  sla?: SlaSnapshot | null
  tracking_events?: TrackingEvent[] | null
  latest_carrier_message?: CarrierMessage | null
  history?: ExceptionHistory | null
  latest_analysis?: AnalysisSummary | null
  counts?: ExceptionCounts | null
}

export type ExceptionBrief = ExceptionListItem
export type ExceptionDetail = ExceptionListItem

/** 详情里的计数摘要（实测 counts） */
export interface ExceptionCounts {
  messages?: number
  followups_open?: number
  notifications?: number
  approvals_pending?: number
}

/** 详情里的近 90 天历史摘要（实测 history） */
export interface ExceptionHistory {
  days?: number
  total?: number
  open?: number
  avg_resolve_minutes?: number | null
  recent?: Array<{
    case_no: string
    type: ExceptionType
    level: ExceptionLevel
    status: ExceptionStatus
    closed_reason?: string | null
  }>
}

export interface ExceptionQuery extends PageQuery {
  /** 后端当前未实现（实测 q 被忽略），仍按契约发送 */
  q?: string
  status?: ExceptionStatus | ''
  level?: ExceptionLevel | ''
  type?: ExceptionType | ''
  customer_id?: number | ''
  sla_breached?: boolean | ''
  assigned_to?: number | ''
}

export interface ExceptionCreatePayload {
  order_id: number
  type: ExceptionType
  level: ExceptionLevel
  occurred_at: string
  note: string
}

/** POST /exceptions/{id}/analyze → 202 新建 / 200 复用（实测还带 reused/exception_status） */
export interface AnalyzeStartResult {
  analysis_id: number
  status: AnalysisStatus
  reused?: boolean
  reused_from_id?: number | null
  exception_status?: ExceptionStatus
  input_hash?: string
  error_code?: string | null
  error_message?: string | null
}

export interface ReasonPayload {
  note: string
  expected_version?: number
}

export interface ClosePayload {
  reason_code: 'DELIVERED' | 'INVALID' | 'MANUAL' | string
  note: string
  expected_version?: number
}

/** T12 exception_event（实测：明细字段名是 detail，不是 detail_json；无 actor_name） */
export interface ExceptionEvent {
  id: number
  workspace_id?: number
  exception_id: number
  event_type: ExceptionEventType
  actor_type: ActorType
  actor_id?: number | null
  actor_name?: string | null
  from_status?: ExceptionStatus | null
  to_status?: ExceptionStatus | null
  detail?: Record<string, unknown> | null
  note?: string | null
  occurred_at: string
}

/** T13 carrier_message（实测：解析结果字段名是 parse_result） */
export interface CarrierMessage {
  id: number
  workspace_id?: number
  exception_id: number
  order_id: number
  channel: MessageChannel
  sender_name?: string | null
  sender_role?: string | null
  raw_text: string
  received_at: string
  parse_status: ParseStatus
  /** T1 输出：{exception_type,location,status,estimated_recovery_at,confidence,missing_info[]} */
  parse_result?: ParseResult | null
  parser_version?: string | null
  parse_error?: string | null
  attachment_urls_json?: string[] | null
  created_by?: number | null
  created_at?: string | null
}

/** T1 PARSE_MESSAGE 输出（§11.2 表 2） */
export interface ParseResult {
  exception_type?: string
  location?: string
  status?: string
  estimated_recovery_at?: string | null
  confidence?: number
  missing_info?: string[]
  /** 兼容旧 seed 的 missing */
  missing?: string[]
}

export interface CarrierMessageCreatePayload {
  raw_text: string
  channel?: MessageChannel
  sender_name?: string
  sender_role?: string
  received_at?: string
}

/**
 * POST /exceptions/{id}/messages 的响应（实测**不是** CarrierMessage 对象）：
 * {message_id, parse_status, exception_id, exception_status, transitioned, parse_result, eta, error_code, error_message}
 * 完整消息（含 parse_result 明细）请再 GET /exceptions/{id}/messages 取。
 */
export interface CarrierMessageCreateResult {
  message_id: number
  parse_status: ParseStatus
  exception_id?: number
  exception_status?: ExceptionStatus
  transitioned?: boolean
  parse_result?: Record<string, unknown> | null
  /** 解析后重算的 ETA：{eta_at, method, remaining_km, avg_speed_kmh, resume_at, detail} */
  eta?: Record<string, unknown> | null
  error_code?: string | null
  error_message?: string | null
}

/* ------------------------------------------------------------- AI 分析 */

/** T15 ai_analysis_step（实测：参数名是 args，非 args_json） */
export interface AiAnalysisStep {
  step_no: number
  step_type: 'LLM' | 'TOOL' | 'VALIDATE'
  tool_name?: string | null
  args?: Record<string, unknown> | null
  result_summary?: string | null
  status: StepStatus
  duration_ms?: number | null
  error?: string | null
  created_at?: string | null
}

/** T2 ANALYZE_EXCEPTION 输出（§11.2 表 2） */
export interface AiRootCause {
  code: 'VEHICLE_BREAKDOWN' | 'TRAFFIC' | 'WEATHER' | 'CUSTOMS' | 'CUSTOMER' | 'UNKNOWN'
  note: string
}

export interface AiImpact {
  delay_minutes: number
  sla_breached: boolean
  affected_customer_level?: CustomerLevel | string | null
}

export interface AiSuggestion {
  code: ApprovalAction
  title: string
  rationale?: string
  assignee_role?: Role | string | null
  payload?: Record<string, unknown>
}

export interface AiEvidenceRef {
  type:
    | 'ORDER'
    | 'TRACKING_EVENT'
    | 'CARRIER_MESSAGE'
    | 'KNOWLEDGE_CHUNK'
    | 'VEHICLE'
    | 'SLA_RULE'
    | string
  id: number
  note?: string | null
  doc?: string | null
  section?: string | null
}

export interface AiAnalysisOutput {
  summary: string
  root_cause: AiRootCause
  impact: AiImpact
  suggestions: AiSuggestion[]
  open_questions: string[]
  evidence_refs: AiEvidenceRef[]
}

/** GET /ai-analyses/{id}（前端 1.5s 轮询此接口；结果字段是 output） */
export interface AiAnalysis {
  id: number
  workspace_id?: number
  exception_id: number
  analysis_no?: string | null
  task_type: 'PARSE_MESSAGE' | 'ANALYZE_EXCEPTION' | 'DRAFT_NOTICE'
  status: AnalysisStatus
  triggered_by?: number | null
  input_hash?: string | null
  model?: string | null
  prompt_version?: string | null
  /** 结论：{summary,root_cause,impact,suggestions,open_questions,evidence_refs} */
  output?: AiAnalysisOutput | null
  risk_level_calculated?: ExceptionLevel | null
  error_code?: string | null
  error_message?: string | null
  tokens_in?: number | null
  tokens_out?: number | null
  latency_ms?: number | null
  reused_from_id?: number | null
  is_replay?: boolean
  started_at?: string | null
  finished_at?: string | null
  created_at?: string | null
  steps?: AiAnalysisStep[]
}

/** 异常详情里内嵌的"最新分析摘要"（实测只有扁平字段 + summary，没有 output/steps） */
export interface AnalysisSummary {
  id: number
  analysis_no?: string | null
  task_type?: string
  status: AnalysisStatus
  is_replay?: boolean
  risk_level_calculated?: ExceptionLevel | null
  error_code?: string | null
  error_message?: string | null
  reused_from_id?: number | null
  started_at?: string | null
  finished_at?: string | null
  summary?: string | null
}

/* ------------------------------------------------------------- 审批单 */

/** T16 approval：AI 原值 / 人工值 / diff（实测字段无 _json 后缀） */
export interface ApprovalDiffField {
  ai: unknown
  final: unknown
}

export interface ApprovalDiff {
  changed?: string[]
  /** 字段名 → {ai, final}（实测是 map，不是数组） */
  fields?: Record<string, ApprovalDiffField>
  ai_value?: unknown
  final_value?: unknown
  ai_payload?: Record<string, unknown>
  final_payload?: Record<string, unknown>
}

export interface Approval {
  id: number
  workspace_id?: number
  exception_id: number
  analysis_id?: number | null
  action_type: ApprovalAction
  target_type?: string | null
  target_id?: number | null
  /** AI 原始建议（不可改，留痕） */
  ai_payload?: Record<string, unknown> | null
  /** 人工修改后的实际执行内容 */
  final_payload?: Record<string, unknown> | null
  diff?: ApprovalDiff | null
  status: ApprovalStatus
  decided_by?: number | null
  decided_at?: string | null
  reject_reason?: string | null
  executed_at?: string | null
  execution_result?: Record<string, unknown> | null
  error_message?: string | null
  retry_count?: number
  expires_at?: string | null
  created_at?: string | null
  updated_at?: string | null
  version: number
}

/** POST /approvals/{id}/approve 与 /reject 的响应（实测形状） */
export interface ApprovalDecisionResult {
  id: number
  status: ApprovalStatus
  diff?: ApprovalDiff | null
  execution_result?: Record<string, unknown> | null
  error_message?: string | null
  reject_reason?: string | null
}

export interface ApprovalApprovePayload {
  expected_version: number
  final_payload?: Record<string, unknown>
}

export interface ApprovalRejectPayload {
  expected_version: number
  reason: string
}

export interface BatchApprovePayload {
  exception_id: number
  approval_ids: number[]
  auto_execute?: boolean
}

export interface BatchApproveItemResult {
  approval_id: number
  status: ApprovalStatus
  error?: string | null
  execution_result?: Record<string, unknown> | null
}

export interface BatchApproveResult {
  items: BatchApproveItemResult[]
  succeeded: number
  failed: number
}

/* ------------------------------------------------------------- 跟进任务 */

export interface FollowupTask {
  id: number
  workspace_id?: number
  exception_id: number
  title: string
  content?: string | null
  assignee_user_id?: number | null
  assignee_name?: string | null
  due_at?: string | null
  priority?: string
  status: FollowupStatus
  source: FollowupSource
  source_approval_id?: number | null
  done_at?: string | null
  done_by?: number | null
  remark?: string | null
  version?: number
  created_at?: string | null
  updated_at?: string | null
}

export interface FollowupCreatePayload {
  exception_id: number
  title: string
  content?: string
  assignee_user_id?: number | null
  due_at?: string | null
  priority?: string
}

export interface FollowupUpdatePayload {
  expected_version?: number
  title?: string
  content?: string
  assignee_user_id?: number | null
  due_at?: string | null
  status?: FollowupStatus
  remark?: string
}

/* --------------------------------------------------------------- 通知 */

export interface Notification {
  id: number
  workspace_id?: number
  exception_id: number
  customer_id: number
  customer_name?: string | null
  channel: NotificationChannel
  subject?: string | null
  content: string
  ai_draft_content?: string | null
  status: NotificationStatus
  approved_by?: number | null
  approved_at?: string | null
  sent_at?: string | null
  source_approval_id?: number | null
  version?: number
  created_at?: string | null
  updated_at?: string | null
}

export interface NotificationUpdatePayload {
  expected_version?: number
  subject?: string
  content: string
}

export interface SkipPayload {
  reason: string
}

/* --------------------------------------------------------------- 审计 */

export interface AuditLog {
  id: number
  workspace_id: number
  actor_type: ActorType
  actor_id?: number | null
  /** 后端未返回，前端可用成员列表映射 */
  actor_name?: string | null
  action: string
  resource_type?: string | null
  resource_id?: number | null
  before_json?: Record<string, unknown> | null
  after_json?: Record<string, unknown> | null
  source?: string | null
  request_id?: string | null
  ip?: string | null
  user_agent?: string | null
  occurred_at: string
}

export interface AuditQuery extends PageQuery {
  resource_type?: string
  resource_id?: number | ''
  actor_id?: number | ''
  action?: string
  occurred_from?: string
  occurred_to?: string
}

/* ------------------------------------------------------------- 知识库 */

export interface KnowledgeDoc {
  id: number
  workspace_id?: number | null
  doc_code?: string | null
  title: string
  category?: string | null
  version?: string | null
  source_path?: string | null
  status?: string | null
  checksum?: string | null
  /** 列表返回每个文档的分片数 */
  chunk_count?: number | null
  updated_at?: string | null
  created_at?: string | null
}

export interface KnowledgeChunk {
  id: number
  doc_id: number
  chunk_no: number
  section_path?: string | null
  content: string
  token_estimate?: number | null
  score?: number | null
}

/** GET /knowledge/docs/{id}（当前后端返回 404，前端做好降级） */
export interface KnowledgeDocDetail extends KnowledgeDoc {
  chunks?: KnowledgeChunk[]
}

export interface ReindexResult {
  indexed?: number
  docs: number
  chunks: number
  duration_ms?: number
  /** 索引不可用时的告警（仍返回 200） */
  warning?: string | null
}

/* ------------------------------------------------------------ Dashboard */

/** 实测字段：pending（不是 pending_handling）、sla_breached_open、by_level/by_status/trend 等 */
export interface DashboardSummary {
  generated_at?: string
  now_utc?: string
  business_date?: string
  today_orders: number
  in_transit: number
  delayed_orders?: number
  exceptions_total?: number
  open_exceptions: number
  high_risk: number
  /** 待处理 */
  pending: number
  processing?: number
  resolving?: number
  resolved: number
  closed?: number
  sla_breached: number
  sla_breached_open?: number
  by_level?: Record<string, number>
  by_status?: Record<string, number>
  high_risk_top?: ExceptionListItem[]
  trend?: DashboardTrendPoint[]
}

/** 趋势点（实测：detected / breached / resolved / closed） */
export interface DashboardTrendPoint {
  date: string
  detected?: number
  breached?: number
  resolved?: number
  closed?: number
  /** 兼容旧命名 */
  exceptions?: number
  sla_breached?: number
}

export interface DashboardTrend {
  days?: number
  start_date?: string
  end_date?: string
  items?: DashboardTrendPoint[]
  trend?: DashboardTrendPoint[]
}

/* ------------------------------------------------------------------ Demo */

/** GET /demo/state（实测形状，与 backend/app/api/v1/demo.py 一致） */
export interface DemoState {
  base_date: string
  offset_minutes: number
  now_utc: string
  clock_mode: string
  ai_mode: 'replay' | 'live' | string
  workspace_id?: number | null
  seed_available?: boolean
  scenario?: string | null
}

export interface DemoTickResult {
  offset_minutes: number
  now_utc: string
  [key: string]: unknown
}

/** GET /api/v1/healthz（实测：db 是布尔，另带 app_env/demo_base_date/clock_offset_minutes/model_tables） */
export interface HealthStatus {
  status: string
  db: boolean | string
  db_error?: string | null
  app_env?: string
  clock_mode?: string
  ai_mode?: string
  demo_base_date?: string
  clock_offset_minutes?: number
  now_utc?: string
  model_tables?: number
}
