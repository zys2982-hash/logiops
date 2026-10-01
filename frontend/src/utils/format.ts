/**
 * 展示格式化与枚举映射（基线 §12.3：utils/format.ts 含 riskLevel 颜色与文案映射）。
 */
import type {
  ApprovalAction,
  ApprovalStatus,
  CarrierStatus,
  CustomerLevel,
  DriverStatus,
  ExceptionEventType,
  ExceptionLevel,
  ExceptionStatus,
  ExceptionType,
  FollowupStatus,
  MemberStatus,
  MessageChannel,
  NotificationStatus,
  OrderStatus,
  ParseStatus,
  SlaScopeType,
  TrackingEventType,
  TrackingSource,
  VehicleStatus,
  AnalysisStatus,
  StepStatus,
} from '@/types'

export type TagType = 'primary' | 'success' | 'info' | 'warning' | 'danger'

export interface Labeled {
  label: string
  type: TagType
}

/* ------------------------------------------------------------ 风险等级 */

/** 风险等级 → 颜色/文案（§8.6：LOW/MEDIUM/HIGH/CRITICAL） */
export const RISK_LEVEL_MAP: Record<ExceptionLevel, Labeled & { color: string }> = {
  LOW: { label: '低风险', type: 'info', color: '#909399' },
  MEDIUM: { label: '中风险', type: 'warning', color: '#e6a23c' },
  HIGH: { label: '高风险', type: 'danger', color: '#f56c6c' },
  CRITICAL: { label: '严重', type: 'danger', color: '#c0392b' },
}

export function riskLevelLabel(level?: ExceptionLevel | null): string {
  return level ? (RISK_LEVEL_MAP[level]?.label ?? level) : '—'
}

export function riskLevelType(level?: ExceptionLevel | null): TagType {
  return level ? (RISK_LEVEL_MAP[level]?.type ?? 'info') : 'info'
}

export function riskLevelColor(level?: ExceptionLevel | null): string {
  return level ? (RISK_LEVEL_MAP[level]?.color ?? '#909399') : '#909399'
}

/** 风险分 → 等级文案（0→LOW 1-2→MEDIUM 3→HIGH 4→CRITICAL，§8.6） */
export function riskScoreLabel(score?: number | null): string {
  if (score === null || score === undefined) return '—'
  return `${score} 分`
}

/* -------------------------------------------------------------- 异常 */

export const EXCEPTION_STATUS_MAP: Record<ExceptionStatus, Labeled> = {
  DETECTED: { label: '待确认', type: 'info' },
  CONFIRMING: { label: '确认中', type: 'primary' },
  ANALYZING: { label: 'AI 分析中', type: 'warning' },
  PROCESSING: { label: '处理中', type: 'warning' },
  RESOLVED: { label: '已解决', type: 'success' },
  CLOSED: { label: '已关闭', type: 'info' },
}

export const EXCEPTION_STATUS_OPTIONS = Object.entries(EXCEPTION_STATUS_MAP).map(([value, v]) => ({
  value,
  label: v.label,
}))

export function exceptionStatusLabel(status?: ExceptionStatus | null): string {
  return status ? (EXCEPTION_STATUS_MAP[status]?.label ?? status) : '—'
}

export function exceptionStatusType(status?: ExceptionStatus | null): TagType {
  return status ? (EXCEPTION_STATUS_MAP[status]?.type ?? 'info') : 'info'
}

export const EXCEPTION_TYPE_MAP: Record<ExceptionType, string> = {
  VEHICLE_BREAKDOWN: '车辆故障',
  DELAY_RISK: '延误风险',
}

export function exceptionTypeLabel(type?: ExceptionType | null): string {
  return type ? (EXCEPTION_TYPE_MAP[type] ?? type) : '—'
}

export const LEVEL_OPTIONS = (['CRITICAL', 'HIGH', 'MEDIUM', 'LOW'] as ExceptionLevel[]).map(
  (value) => ({ value, label: RISK_LEVEL_MAP[value].label }),
)

/* -------------------------------------------------------------- 订单 */

export const ORDER_STATUS_MAP: Record<OrderStatus, Labeled> = {
  CREATED: { label: '待发车', type: 'info' },
  DISPATCHED: { label: '已派车', type: 'primary' },
  IN_TRANSIT: { label: '运输中', type: 'warning' },
  DELIVERED: { label: '已送达', type: 'success' },
  CLOSED: { label: '已关闭', type: 'info' },
  CANCELLED: { label: '已取消', type: 'info' },
}

export const ORDER_STATUS_OPTIONS = Object.entries(ORDER_STATUS_MAP).map(([value, v]) => ({
  value,
  label: v.label,
}))

export function orderStatusLabel(status?: OrderStatus | null): string {
  return status ? (ORDER_STATUS_MAP[status]?.label ?? status) : '—'
}

export function orderStatusType(status?: OrderStatus | null): TagType {
  return status ? (ORDER_STATUS_MAP[status]?.type ?? 'info') : 'info'
}

/* ------------------------------------------------------------ 轨迹事件 */

export const TRACKING_EVENT_MAP: Record<TrackingEventType, string> = {
  DEPART: '发车',
  ARRIVE: '到达',
  STOP: '停靠',
  RESUME: '恢复行驶',
  REPAIR_START: '开始维修',
  REPAIR_END: '维修完成',
  DELIVER: '送达',
  NOTE: '备注',
}

export function trackingEventLabel(type?: TrackingEventType | null): string {
  return type ? (TRACKING_EVENT_MAP[type] ?? type) : '—'
}

export const TRACKING_SOURCE_MAP: Record<TrackingSource, string> = {
  MOCK: '模拟',
  DRIVER: '司机上报',
  CARRIER: '承运商',
  OPERATOR: '运营录入',
  SYSTEM: '系统',
}

export function trackingSourceLabel(source?: TrackingSource | null): string {
  return source ? (TRACKING_SOURCE_MAP[source] ?? source) : '—'
}

export const TRACKING_EVENT_OPTIONS = Object.entries(TRACKING_EVENT_MAP).map(([value, label]) => ({
  value,
  label,
}))

/* -------------------------------------------------------------- 主数据 */

export const CUSTOMER_LEVEL_MAP: Record<CustomerLevel, Labeled> = {
  NORMAL: { label: '普通', type: 'info' },
  VIP: { label: 'VIP', type: 'warning' },
  SVIP: { label: 'SVIP', type: 'danger' },
}

export function customerLevelLabel(level?: CustomerLevel | null): string {
  return level ? (CUSTOMER_LEVEL_MAP[level]?.label ?? level) : '—'
}

export function customerLevelType(level?: CustomerLevel | null): TagType {
  return level ? (CUSTOMER_LEVEL_MAP[level]?.type ?? 'info') : 'info'
}

export const CUSTOMER_LEVEL_OPTIONS = Object.entries(CUSTOMER_LEVEL_MAP).map(([value, v]) => ({
  value,
  label: v.label,
}))

export const CARRIER_STATUS_MAP: Record<CarrierStatus, Labeled> = {
  ACTIVE: { label: '合作中', type: 'success' },
  SUSPENDED: { label: '已暂停', type: 'danger' },
}

export function carrierStatusLabel(status?: CarrierStatus | null): string {
  return status ? (CARRIER_STATUS_MAP[status]?.label ?? status) : '—'
}

export function carrierStatusType(status?: CarrierStatus | null): TagType {
  return status ? (CARRIER_STATUS_MAP[status]?.type ?? 'info') : 'info'
}

export const VEHICLE_STATUS_MAP: Record<VehicleStatus, Labeled> = {
  IDLE: { label: '空闲', type: 'info' },
  IN_TRANSIT: { label: '在途', type: 'warning' },
  REPAIRING: { label: '维修中', type: 'danger' },
  OFFLINE: { label: '离线', type: 'info' },
}

export function vehicleStatusLabel(status?: VehicleStatus | null): string {
  return status ? (VEHICLE_STATUS_MAP[status]?.label ?? status) : '—'
}

export function vehicleStatusType(status?: VehicleStatus | null): TagType {
  return status ? (VEHICLE_STATUS_MAP[status]?.type ?? 'info') : 'info'
}

export const DRIVER_STATUS_MAP: Record<DriverStatus, Labeled> = {
  AVAILABLE: { label: '可派', type: 'success' },
  ON_TRIP: { label: '在途', type: 'warning' },
  OFF_DUTY: { label: '休息', type: 'info' },
}

export function driverStatusLabel(status?: DriverStatus | null): string {
  return status ? (DRIVER_STATUS_MAP[status]?.label ?? status) : '—'
}

export function driverStatusType(status?: DriverStatus | null): TagType {
  return status ? (DRIVER_STATUS_MAP[status]?.type ?? 'info') : 'info'
}

export const SLA_SCOPE_MAP: Record<SlaScopeType, string> = {
  DEFAULT: '默认规则',
  CUSTOMER_LEVEL: '按客户等级',
  CUSTOMER: '指定客户',
}

export function slaScopeLabel(scope?: SlaScopeType | null): string {
  return scope ? (SLA_SCOPE_MAP[scope] ?? scope) : '—'
}

/* ------------------------------------------------------------ AI 分析 */

export const ANALYSIS_STATUS_MAP: Record<AnalysisStatus, Labeled> = {
  PENDING: { label: '排队中', type: 'info' },
  RUNNING: { label: '分析中', type: 'warning' },
  READY: { label: '已完成', type: 'success' },
  FAILED: { label: '失败', type: 'danger' },
}

export function analysisStatusLabel(status?: AnalysisStatus | null): string {
  return status ? (ANALYSIS_STATUS_MAP[status]?.label ?? status) : '—'
}

export function analysisStatusType(status?: AnalysisStatus | null): TagType {
  return status ? (ANALYSIS_STATUS_MAP[status]?.type ?? 'info') : 'info'
}

export const STEP_STATUS_MAP: Record<StepStatus, Labeled> = {
  RUNNING: { label: '进行中', type: 'warning' },
  OK: { label: '完成', type: 'success' },
  ERROR: { label: '失败', type: 'danger' },
}

/** Tool 名 → 中文说明（§11.2 表 3） */
export const TOOL_NAME_MAP: Record<string, string> = {
  get_order: '读取订单',
  get_tracking_events: '读取轨迹时间线',
  get_customer: '读取客户',
  get_customer_sla: '读取 SLA 规则',
  get_vehicle: '读取车辆状态',
  get_exception_history: '读取历史异常',
  search_knowledge: '检索知识库规范',
}

export function toolNameLabel(name?: string | null): string {
  if (!name) return '校验'
  return TOOL_NAME_MAP[name] ?? name
}

/* -------------------------------------------------------------- 审批 */

export const APPROVAL_ACTION_MAP: Record<ApprovalAction, string> = {
  UPDATE_ETA: '更新 ETA',
  CREATE_FOLLOWUP: '创建跟进任务',
  SAVE_NOTICE: '生成客户通知草稿',
  SEND_NOTICE: '发送客户通知',
  CLOSE_EXCEPTION: '关闭异常',
}

export function approvalActionLabel(action?: ApprovalAction | string | null): string {
  if (!action) return '—'
  return APPROVAL_ACTION_MAP[action as ApprovalAction] ?? action
}

export const APPROVAL_STATUS_MAP: Record<ApprovalStatus, Labeled> = {
  PENDING: { label: '待决策', type: 'warning' },
  APPROVED: { label: '已批准', type: 'success' },
  REJECTED: { label: '已驳回', type: 'info' },
  EXPIRED: { label: '已过期', type: 'info' },
  EXECUTED: { label: '已执行', type: 'success' },
  FAILED: { label: '执行失败', type: 'danger' },
}

export function approvalStatusLabel(status?: ApprovalStatus | null): string {
  return status ? (APPROVAL_STATUS_MAP[status]?.label ?? status) : '—'
}

export function approvalStatusType(status?: ApprovalStatus | null): TagType {
  return status ? (APPROVAL_STATUS_MAP[status]?.type ?? 'info') : 'info'
}

/* ---------------------------------------------------------- 跟进 / 通知 */

export const FOLLOWUP_STATUS_MAP: Record<FollowupStatus, Labeled> = {
  OPEN: { label: '待办', type: 'warning' },
  DONE: { label: '已完成', type: 'success' },
  CANCELLED: { label: '已取消', type: 'info' },
}

export function followupStatusLabel(status?: FollowupStatus | null): string {
  return status ? (FOLLOWUP_STATUS_MAP[status]?.label ?? status) : '—'
}

export function followupStatusType(status?: FollowupStatus | null): TagType {
  return status ? (FOLLOWUP_STATUS_MAP[status]?.type ?? 'info') : 'info'
}

export const NOTIFICATION_STATUS_MAP: Record<NotificationStatus, Labeled> = {
  DRAFT: { label: '草稿', type: 'info' },
  APPROVED: { label: '已批准', type: 'primary' },
  SENT_MOCK: { label: '已模拟发送', type: 'success' },
  SKIPPED: { label: '已跳过', type: 'info' },
}

export function notificationStatusLabel(status?: NotificationStatus | null): string {
  return status ? (NOTIFICATION_STATUS_MAP[status]?.label ?? status) : '—'
}

export function notificationStatusType(status?: NotificationStatus | null): TagType {
  return status ? (NOTIFICATION_STATUS_MAP[status]?.type ?? 'info') : 'info'
}

export const PARSE_STATUS_MAP: Record<ParseStatus, Labeled> = {
  PENDING: { label: '解析中', type: 'warning' },
  PARSED: { label: '已解析', type: 'success' },
  FAILED: { label: '解析失败', type: 'danger' },
}

export function parseStatusLabel(status?: ParseStatus | null): string {
  return status ? (PARSE_STATUS_MAP[status]?.label ?? status) : '—'
}

export function parseStatusType(status?: ParseStatus | null): TagType {
  return status ? (PARSE_STATUS_MAP[status]?.type ?? 'info') : 'info'
}

export const MESSAGE_CHANNEL_MAP: Record<MessageChannel, string> = {
  MANUAL_PASTE: '人工粘贴',
  MOCK_WECHAT: '模拟微信',
  MOCK_SMS: '模拟短信',
}

export function messageChannelLabel(channel?: MessageChannel | null): string {
  return channel ? (MESSAGE_CHANNEL_MAP[channel] ?? channel) : '—'
}

export const MESSAGE_CHANNEL_OPTIONS = Object.entries(MESSAGE_CHANNEL_MAP).map(
  ([value, label]) => ({ value, label }),
)

/* ---------------------------------------------------------- 异常时间线 */

export const EXCEPTION_EVENT_MAP: Record<ExceptionEventType, string> = {
  DETECTED: '异常检出',
  MESSAGE_ADDED: '录入承运商消息',
  CONFIRMED: '人工确认',
  ANALYSIS_REQUESTED: '请求 AI 分析',
  ANALYSIS_READY: 'AI 分析完成',
  ANALYSIS_FAILED: 'AI 分析失败',
  APPROVED: '审批通过',
  REJECTED: '审批驳回',
  EXECUTED: '动作已执行',
  EXECUTE_FAILED: '执行失败',
  ETA_UPDATED: 'ETA 已更新',
  STATUS_CHANGED: '状态变更',
  COMMENT: '备注',
  FOLLOWUP_DONE: '跟进完成',
  CLOSED: '异常关闭',
}

export function exceptionEventLabel(type?: ExceptionEventType | string | null): string {
  if (!type) return '—'
  return EXCEPTION_EVENT_MAP[type as ExceptionEventType] ?? type
}

/* -------------------------------------------------------------- 其他 */

export const MEMBER_STATUS_MAP: Record<MemberStatus, Labeled> = {
  ACTIVE: { label: '正常', type: 'success' },
  INVITED: { label: '已邀请', type: 'warning' },
  REMOVED: { label: '已移除', type: 'info' },
}

export function memberStatusLabel(status?: MemberStatus | null): string {
  return status ? (MEMBER_STATUS_MAP[status]?.label ?? status) : '—'
}

export function memberStatusType(status?: MemberStatus | null): TagType {
  return status ? (MEMBER_STATUS_MAP[status]?.type ?? 'info') : 'info'
}

/** 手机号脱敏（§8.7）：13800000001 → 138****0001 */
export function maskPhone(phone?: string | null): string {
  if (!phone) return '—'
  const value = String(phone)
  if (value.length < 7) return value
  return `${value.slice(0, 3)}****${value.slice(-4)}`
}

/** 数组 → 逗号分隔文本 */
export function joinText(values?: Array<string | null | undefined> | null, sep = '、'): string {
  if (!values || values.length === 0) return '—'
  return values.filter((v): v is string => !!v).join(sep) || '—'
}

/** 数值保留 n 位，null/undefined → — */
export function formatNumber(value?: number | null, digits = 0): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return value.toFixed(digits)
}

/** 字节/耗时统一文案 */
export function formatDuration(ms?: number | null): string {
  if (ms === null || ms === undefined) return '—'
  if (ms < 1000) return `${ms} ms`
  return `${(ms / 1000).toFixed(1)} s`
}
