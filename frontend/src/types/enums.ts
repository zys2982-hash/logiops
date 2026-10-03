/**
 * 全局枚举字典（基线 §7.2）——与后端 `backend/app/models/enums.py` 逐字对齐。
 * 注意：JSON 字段一律 snake_case，前端不做运行时转换。
 */

export const Role = {
  OWNER: 'OWNER',
  ADMIN: 'ADMIN',
  OPERATOR: 'OPERATOR',
  VIEWER: 'VIEWER',
} as const
export type Role = (typeof Role)[keyof typeof Role]

export const MemberStatus = {
  ACTIVE: 'ACTIVE',
  INVITED: 'INVITED',
  REMOVED: 'REMOVED',
} as const
export type MemberStatus = (typeof MemberStatus)[keyof typeof MemberStatus]

export const CustomerLevel = {
  NORMAL: 'NORMAL',
  VIP: 'VIP',
  SVIP: 'SVIP',
} as const
export type CustomerLevel = (typeof CustomerLevel)[keyof typeof CustomerLevel]

export const CarrierStatus = {
  ACTIVE: 'ACTIVE',
  SUSPENDED: 'SUSPENDED',
} as const
export type CarrierStatus = (typeof CarrierStatus)[keyof typeof CarrierStatus]

export const VehicleStatus = {
  IDLE: 'IDLE',
  IN_TRANSIT: 'IN_TRANSIT',
  REPAIRING: 'REPAIRING',
  OFFLINE: 'OFFLINE',
} as const
export type VehicleStatus = (typeof VehicleStatus)[keyof typeof VehicleStatus]

export const DriverStatus = {
  AVAILABLE: 'AVAILABLE',
  ON_TRIP: 'ON_TRIP',
  OFF_DUTY: 'OFF_DUTY',
} as const
export type DriverStatus = (typeof DriverStatus)[keyof typeof DriverStatus]

export const OrderStatus = {
  CREATED: 'CREATED',
  DISPATCHED: 'DISPATCHED',
  IN_TRANSIT: 'IN_TRANSIT',
  DELIVERED: 'DELIVERED',
  CLOSED: 'CLOSED',
  CANCELLED: 'CANCELLED',
} as const
export type OrderStatus = (typeof OrderStatus)[keyof typeof OrderStatus]

export const TrackingEventType = {
  DEPART: 'DEPART',
  ARRIVE: 'ARRIVE',
  STOP: 'STOP',
  RESUME: 'RESUME',
  REPAIR_START: 'REPAIR_START',
  REPAIR_END: 'REPAIR_END',
  DELIVER: 'DELIVER',
  NOTE: 'NOTE',
} as const
export type TrackingEventType = (typeof TrackingEventType)[keyof typeof TrackingEventType]

export const TrackingSource = {
  MOCK: 'MOCK',
  DRIVER: 'DRIVER',
  CARRIER: 'CARRIER',
  OPERATOR: 'OPERATOR',
  SYSTEM: 'SYSTEM',
} as const
export type TrackingSource = (typeof TrackingSource)[keyof typeof TrackingSource]

export const SlaScopeType = {
  DEFAULT: 'DEFAULT',
  CUSTOMER_LEVEL: 'CUSTOMER_LEVEL',
  CUSTOMER: 'CUSTOMER',
} as const
export type SlaScopeType = (typeof SlaScopeType)[keyof typeof SlaScopeType]

export const ExceptionType = {
  VEHICLE_BREAKDOWN: 'VEHICLE_BREAKDOWN',
  DELAY_RISK: 'DELAY_RISK',
} as const
export type ExceptionType = (typeof ExceptionType)[keyof typeof ExceptionType]

export const ExceptionLevel = {
  LOW: 'LOW',
  MEDIUM: 'MEDIUM',
  HIGH: 'HIGH',
  CRITICAL: 'CRITICAL',
} as const
export type ExceptionLevel = (typeof ExceptionLevel)[keyof typeof ExceptionLevel]

export const ExceptionStatus = {
  // 对外 4 状态（4 状态模型）
  DETECTED: 'DETECTED',
  PROCESSING: 'PROCESSING',
  RESOLVED: 'RESOLVED',
  CLOSED: 'CLOSED',
  // 历史状态：旧数据仍可能出现（后端读取时按"处理中"处理），不出现在下拉选项里
  CONFIRMING: 'CONFIRMING',
  ANALYZING: 'ANALYZING',
} as const
export type ExceptionStatus = (typeof ExceptionStatus)[keyof typeof ExceptionStatus]

export const ExceptionEventType = {
  DETECTED: 'DETECTED',
  MESSAGE_ADDED: 'MESSAGE_ADDED',
  CONFIRMED: 'CONFIRMED',
  ANALYSIS_REQUESTED: 'ANALYSIS_REQUESTED',
  ANALYSIS_READY: 'ANALYSIS_READY',
  ANALYSIS_FAILED: 'ANALYSIS_FAILED',
  APPROVED: 'APPROVED',
  REJECTED: 'REJECTED',
  EXECUTED: 'EXECUTED',
  EXECUTE_FAILED: 'EXECUTE_FAILED',
  ETA_UPDATED: 'ETA_UPDATED',
  STATUS_CHANGED: 'STATUS_CHANGED',
  COMMENT: 'COMMENT',
  FOLLOWUP_DONE: 'FOLLOWUP_DONE',
  CLOSED: 'CLOSED',
} as const
export type ExceptionEventType = (typeof ExceptionEventType)[keyof typeof ExceptionEventType]

export const ActorType = {
  USER: 'USER',
  SYSTEM: 'SYSTEM',
  AI: 'AI',
} as const
export type ActorType = (typeof ActorType)[keyof typeof ActorType]

export const MessageChannel = {
  MANUAL_PASTE: 'MANUAL_PASTE',
  MOCK_WECHAT: 'MOCK_WECHAT',
  MOCK_SMS: 'MOCK_SMS',
} as const
export type MessageChannel = (typeof MessageChannel)[keyof typeof MessageChannel]

export const ParseStatus = {
  PENDING: 'PENDING',
  PARSED: 'PARSED',
  FAILED: 'FAILED',
} as const
export type ParseStatus = (typeof ParseStatus)[keyof typeof ParseStatus]

export const AnalysisStatus = {
  PENDING: 'PENDING',
  RUNNING: 'RUNNING',
  READY: 'READY',
  FAILED: 'FAILED',
} as const
export type AnalysisStatus = (typeof AnalysisStatus)[keyof typeof AnalysisStatus]

export const StepStatus = {
  RUNNING: 'RUNNING',
  OK: 'OK',
  ERROR: 'ERROR',
} as const
export type StepStatus = (typeof StepStatus)[keyof typeof StepStatus]

export const ApprovalAction = {
  UPDATE_ETA: 'UPDATE_ETA',
  CREATE_FOLLOWUP: 'CREATE_FOLLOWUP',
  SAVE_NOTICE: 'SAVE_NOTICE',
  SEND_NOTICE: 'SEND_NOTICE',
  CLOSE_EXCEPTION: 'CLOSE_EXCEPTION',
} as const
export type ApprovalAction = (typeof ApprovalAction)[keyof typeof ApprovalAction]

export const ApprovalStatus = {
  PENDING: 'PENDING',
  APPROVED: 'APPROVED',
  REJECTED: 'REJECTED',
  EXPIRED: 'EXPIRED',
  EXECUTED: 'EXECUTED',
  FAILED: 'FAILED',
} as const
export type ApprovalStatus = (typeof ApprovalStatus)[keyof typeof ApprovalStatus]

export const FollowupStatus = {
  OPEN: 'OPEN',
  DONE: 'DONE',
  CANCELLED: 'CANCELLED',
} as const
export type FollowupStatus = (typeof FollowupStatus)[keyof typeof FollowupStatus]

export const FollowupSource = {
  AI_SUGGESTED: 'AI_SUGGESTED',
  MANUAL: 'MANUAL',
  SYSTEM: 'SYSTEM',
} as const
export type FollowupSource = (typeof FollowupSource)[keyof typeof FollowupSource]

export const NotificationChannel = {
  MANUAL_COPY: 'MANUAL_COPY',
  MOCK_EMAIL: 'MOCK_EMAIL',
} as const
export type NotificationChannel = (typeof NotificationChannel)[keyof typeof NotificationChannel]

export const NotificationStatus = {
  DRAFT: 'DRAFT',
  APPROVED: 'APPROVED',
  SENT_MOCK: 'SENT_MOCK',
  SKIPPED: 'SKIPPED',
} as const
export type NotificationStatus = (typeof NotificationStatus)[keyof typeof NotificationStatus]

export const DetectionRule = {
  STALL_OVER_THRESHOLD: 'STALL_OVER_THRESHOLD',
  ETA_BREACH_SLA: 'ETA_BREACH_SLA',
  MANUAL: 'MANUAL',
} as const
export type DetectionRule = (typeof DetectionRule)[keyof typeof DetectionRule]

/** 操作权限码（基线 §9.2 / backend/app/core/permissions.py 的 Perm 枚举，逐字对齐） */
export const Perm = {
  DASHBOARD_VIEW: 'dashboard.view',
  CUSTOMER_VIEW: 'customer.view',
  CARRIER_VIEW: 'carrier.view',
  VEHICLE_VIEW: 'vehicle.view',
  DRIVER_VIEW: 'driver.view',
  ORDER_VIEW: 'order.view',
  TRACKING_VIEW: 'tracking.view',
  SLA_VIEW: 'sla.view',
  EXCEPTION_VIEW: 'exception.view',
  AUDIT_VIEW: 'audit.view',
  KNOWLEDGE_VIEW: 'knowledge.view',
  MEMBER_VIEW: 'member.view',
  CUSTOMER_MANAGE: 'customer.manage',
  CARRIER_MANAGE: 'carrier.manage',
  VEHICLE_MANAGE: 'vehicle.manage',
  DRIVER_MANAGE: 'driver.manage',
  ORDER_MANAGE: 'order.manage',
  TRACKING_WRITE: 'tracking.write',
  SLA_MANAGE: 'sla.manage',
  EXCEPTION_CREATE: 'exception.create',
  EXCEPTION_HANDLE: 'exception.handle',
  EXCEPTION_FORCE_CLOSE: 'exception.force_close',
  APPROVAL_DECIDE: 'approval.decide',
  FOLLOWUP_WRITE: 'followup.write',
  NOTIFICATION_APPROVE: 'notification.approve',
  MEMBER_MANAGE: 'member.manage',
  KNOWLEDGE_MANAGE: 'knowledge.manage',
  WORKSPACE_DELETE: 'workspace.delete',
  DEMO_CONTROL: 'demo.control',
} as const
export type Perm = (typeof Perm)[keyof typeof Perm]

/**
 * 前端本地权限矩阵（与后端 ROLE_PERMS 一致）。
 * 后端返回 403 时以前端矩阵为兜底，避免"点了才报错"。
 */
const VIEW_PERMS: Perm[] = [
  Perm.DASHBOARD_VIEW,
  Perm.CUSTOMER_VIEW,
  Perm.CARRIER_VIEW,
  Perm.VEHICLE_VIEW,
  Perm.DRIVER_VIEW,
  Perm.ORDER_VIEW,
  Perm.TRACKING_VIEW,
  Perm.SLA_VIEW,
  Perm.EXCEPTION_VIEW,
  Perm.AUDIT_VIEW,
  Perm.KNOWLEDGE_VIEW,
  Perm.MEMBER_VIEW,
]

const OPERATOR_PERMS: Perm[] = [
  ...VIEW_PERMS,
  Perm.TRACKING_WRITE,
  Perm.EXCEPTION_CREATE,
  Perm.EXCEPTION_HANDLE,
  Perm.APPROVAL_DECIDE,
  Perm.FOLLOWUP_WRITE,
  Perm.NOTIFICATION_APPROVE,
  Perm.DEMO_CONTROL,
]

const ADMIN_PERMS: Perm[] = [
  ...OPERATOR_PERMS,
  Perm.CUSTOMER_MANAGE,
  Perm.CARRIER_MANAGE,
  Perm.VEHICLE_MANAGE,
  Perm.DRIVER_MANAGE,
  Perm.ORDER_MANAGE,
  Perm.SLA_MANAGE,
  Perm.EXCEPTION_FORCE_CLOSE,
  Perm.MEMBER_MANAGE,
  Perm.KNOWLEDGE_MANAGE,
]

export const ROLE_PERMS: Record<Role, Perm[]> = {
  VIEWER: VIEW_PERMS,
  OPERATOR: OPERATOR_PERMS,
  ADMIN: ADMIN_PERMS,
  OWNER: Object.values(Perm) as Perm[],
}
