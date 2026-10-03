/**
 * 本地演示 fixture（基线 §13.3「CASE-A 权威数值」+ 真实后端响应形状）。
 *
 * 仅在后端未就绪（网络错误 / 404 / 5xx）时由 mocks/registry.ts 兜底返回。
 * 字段名与 backend/app/schemas 的**实际响应**一致（已逐端点核对）：
 *   output / ai_payload / final_payload / diff / execution_result / parse_result / detail / args
 *   orders 用扁平字段、dashboard 用 pending、trend 点用 detected/breached。
 *
 * CASE-A 权威值（Asia/Shanghai → UTC）：
 *   dispatched 2026-09-30 01:30 → 2026-09-29T17:30:00Z
 *   promised   2026-10-01 01:30 → 2026-09-30T17:30:00Z
 *   expected   2026-10-01 06:00 → 2026-09-30T22:00:00Z
 *   sla_delay_minutes=270、sla_breached=true、risk_score=4 → CRITICAL
 */
import type {
  AiAnalysis,
  AiAnalysisOutput,
  AiAnalysisStep,
  AnalysisSummary,
  Approval,
  AuditLog,
  CarrierMessage,
  Customer,
  Carrier,
  DashboardSummary,
  DashboardTrend,
  DemoState,
  Driver,
  ExceptionCounts,
  ExceptionDetail,
  ExceptionEvent,
  ExceptionHistory,
  ExceptionListItem,
  FollowupTask,
  KnowledgeChunk,
  KnowledgeDoc,
  KnowledgeDocDetail,
  MeResult,
  Notification,
  Order,
  RiskFactor,
  SlaRule,
  TrackingEvent,
  User,
  Vehicle,
  Workspace,
  WorkspaceMember,
} from '@/types'

/* ------------------------------------------------------------ 时间基准 */

/** 业务基准日 2026-09-30（§13.1 DEMO_BASE_DATE） */
export const DEMO_BASE_DATE = '2026-09-30'
/** 业务基准时间 09:00 (+08) = 2026-09-30T01:00:00Z（实测后端 /demo/state 同值） */
export const DEMO_NOW_UTC = '2026-09-30T01:00:00Z'

/**
 * CASE-A 权威数值（UTC）——与 docs/00-项目基线-MVP.md §13.3 的真库实测值。
 * 展示口径（Asia/Shanghai）：发车 09-30 01:30、承诺到达 10-01 01:30、预计到达 10-01 06:00、延误 270 分钟。
 */
export const CASE_A = {
  dispatchedAt: '2026-09-29T17:30:00Z', // +08 09-30 01:30
  promisedAt: '2026-09-30T17:30:00Z', // +08 10-01 01:30
  expectedAt: '2026-09-30T22:00:00Z', // +08 10-01 06:00
  recoveryAt: '2026-09-30T20:00:00+08:00', // +08 09-30 20:00
  delayMinutes: 270,
  riskScore: 4,
} as const

export const demoState: DemoState = {
  base_date: DEMO_NOW_UTC,
  offset_minutes: 0,
  now_utc: DEMO_NOW_UTC,
  clock_mode: 'replay',
  ai_mode: 'replay',
  workspace_id: 1,
  seed_available: true,
  scenario: 'case-a',
}

/* ------------------------------------------------------------ 用户/工作区 */

export const users: User[] = [
  { id: 1, email: 'owner@logiops.dev', name: '王总', phone: '138****0001', status: 'ACTIVE', last_login_at: DEMO_NOW_UTC, created_at: DEMO_NOW_UTC },
  { id: 2, email: 'admin@logiops.dev', name: '李管理', phone: '138****0002', status: 'ACTIVE', last_login_at: DEMO_NOW_UTC, created_at: DEMO_NOW_UTC },
  { id: 3, email: 'operator@logiops.dev', name: '张三', phone: '138****0003', status: 'ACTIVE', last_login_at: DEMO_NOW_UTC, created_at: DEMO_NOW_UTC },
  { id: 4, email: 'viewer@logiops.dev', name: '赵敏', phone: '138****0004', status: 'ACTIVE', last_login_at: DEMO_NOW_UTC, created_at: DEMO_NOW_UTC },
]

export const workspace: Workspace = {
  id: 1,
  name: '顺捷物流',
  code: 'SJ',
  owner_user_id: 1,
  status: 'ACTIVE',
  role: 'OPERATOR',
  created_at: DEMO_NOW_UTC,
}

export const members: WorkspaceMember[] = [
  { id: 1, user_id: 1, email: 'owner@logiops.dev', name: '王总', role: 'OWNER', status: 'ACTIVE', joined_at: DEMO_NOW_UTC },
  { id: 2, user_id: 2, email: 'admin@logiops.dev', name: '李管理', role: 'ADMIN', status: 'ACTIVE', joined_at: DEMO_NOW_UTC },
  { id: 3, user_id: 3, email: 'operator@logiops.dev', name: '张三', role: 'OPERATOR', status: 'ACTIVE', joined_at: DEMO_NOW_UTC },
  { id: 4, user_id: 4, email: 'viewer@logiops.dev', name: '赵敏', role: 'VIEWER', status: 'ACTIVE', joined_at: DEMO_NOW_UTC },
]

/** 默认登录用户：OPERATOR（与真实 seed 的 operator@logiops.dev 一致） */
export const fixtureMe: MeResult = {
  user: users[2],
  role: 'OPERATOR',
  permissions: [
    'dashboard.view', 'customer.view', 'carrier.view', 'vehicle.view', 'driver.view', 'order.view',
    'tracking.view', 'sla.view', 'exception.view', 'audit.view', 'knowledge.view', 'member.view',
    'tracking.write', 'exception.create', 'exception.handle', 'approval.decide', 'followup.write',
    'notification.approve', 'demo.control',
  ],
  workspace_id: 1,
  workspace,
  workspaces: [workspace],
}

export const fixtureLogin = {
  access_token: 'mock-jwt-token-for-local-demo',
  token_type: 'bearer',
  expires_in: 43200,
  user: users[2],
}

/* ---------------------------------------------------------------- 主数据 */

export const customers: Customer[] = [
  { id: 1, workspace_id: 1, code: 'VIP-01', name: '远洋集团', level: 'VIP', contact_name: '刘经理', contact_phone: '138****0001', contact_email: 'l***@yuanyang.example.com', notify_pref: 'MANUAL_COPY', remark: 'SLA 专属规则：发车后 24h', status: 'ACTIVE', version: 1, created_at: DEMO_NOW_UTC, updated_at: DEMO_NOW_UTC },
  { id: 2, workspace_id: 1, code: 'NORM-01', name: '华北贸易', level: 'NORMAL', contact_name: '陈主管', contact_phone: '138****0002', notify_pref: 'MANUAL_COPY', status: 'ACTIVE', version: 1, created_at: DEMO_NOW_UTC, updated_at: DEMO_NOW_UTC },
  { id: 3, workspace_id: 1, code: 'SVIP-01', name: '华东医药', level: 'SVIP', contact_name: '孙总', contact_phone: '138****0003', contact_email: 's***@huadong.example.com', notify_pref: 'MOCK_EMAIL', remark: '冷链药品，时效最严', status: 'ACTIVE', version: 1, created_at: DEMO_NOW_UTC, updated_at: DEMO_NOW_UTC },
  { id: 4, workspace_id: 1, code: 'NORM-02', name: '珠江实业', level: 'NORMAL', contact_name: '周工', contact_phone: '138****0004', notify_pref: 'MANUAL_COPY', status: 'ACTIVE', version: 1, created_at: DEMO_NOW_UTC, updated_at: DEMO_NOW_UTC },
  { id: 5, workspace_id: 1, code: 'VIP-02', name: '中远海运', level: 'VIP', contact_name: '吴总监', contact_phone: '138****0005', notify_pref: 'MANUAL_COPY', status: 'ACTIVE', version: 1, created_at: DEMO_NOW_UTC, updated_at: DEMO_NOW_UTC },
]

export const carriers: Carrier[] = [
  { id: 1, workspace_id: 1, code: 'CAR-01', name: '顺达运输', contact_name: '马队长', contact_phone: '138****0011', service_level: 'A', status: 'ACTIVE', version: 1, created_at: DEMO_NOW_UTC, updated_at: DEMO_NOW_UTC },
  { id: 2, workspace_id: 1, code: 'CAR-02', name: '捷运物流', contact_name: '钱师傅', contact_phone: '138****0012', service_level: 'B', status: 'ACTIVE', version: 1, created_at: DEMO_NOW_UTC, updated_at: DEMO_NOW_UTC },
  { id: 3, workspace_id: 1, code: 'CAR-03', name: '恒通货运', contact_name: '孙队长', contact_phone: '138****0013', service_level: 'B', status: 'SUSPENDED', remark: '上月时效不达标', version: 1, created_at: DEMO_NOW_UTC, updated_at: DEMO_NOW_UTC },
  { id: 4, workspace_id: 1, code: 'CAR-04', name: '陆港专线', contact_name: '李调度', contact_phone: '138****0014', service_level: 'A', status: 'ACTIVE', version: 1, created_at: DEMO_NOW_UTC, updated_at: DEMO_NOW_UTC },
]

export const vehicles: Vehicle[] = [
  { id: 1, workspace_id: 1, plate_no: '津A·12345', vehicle_type: '9.6米厢车', capacity_ton: 18, carrier_id: 1, status: 'REPAIRING', current_driver_id: 1, current_city: '济南', remark: '右后轮爆胎', version: 1, created_at: DEMO_NOW_UTC, updated_at: DEMO_NOW_UTC },
  { id: 2, workspace_id: 1, plate_no: '沪B·88001', vehicle_type: '13米高栏', capacity_ton: 30, carrier_id: 2, status: 'IN_TRANSIT', current_driver_id: 2, current_city: '南京', version: 1, created_at: DEMO_NOW_UTC, updated_at: DEMO_NOW_UTC },
  { id: 3, workspace_id: 1, plate_no: '鲁C·66009', vehicle_type: '9.6米厢车', capacity_ton: 18, carrier_id: 1, status: 'IDLE', current_city: '济南', version: 1, created_at: DEMO_NOW_UTC, updated_at: DEMO_NOW_UTC },
  { id: 4, workspace_id: 1, plate_no: '苏D·31002', vehicle_type: '冷藏车', capacity_ton: 12, carrier_id: 4, status: 'IN_TRANSIT', current_driver_id: 3, current_city: '徐州', version: 1, created_at: DEMO_NOW_UTC, updated_at: DEMO_NOW_UTC },
  { id: 8, workspace_id: 1, plate_no: '沪B·12353', vehicle_type: '17.5米挂车', capacity_ton: 32, carrier_id: 4, status: 'OFFLINE', version: 1, created_at: DEMO_NOW_UTC, updated_at: DEMO_NOW_UTC },
]

export const drivers: Driver[] = [
  { id: 1, workspace_id: 1, name: '李四', phone: '138****0021', carrier_id: 1, license_no: 'A1234567', status: 'ON_TRIP', version: 1, created_at: DEMO_NOW_UTC, updated_at: DEMO_NOW_UTC },
  { id: 2, workspace_id: 1, name: '王五', phone: '138****0022', carrier_id: 2, license_no: 'A2234567', status: 'ON_TRIP', version: 1, created_at: DEMO_NOW_UTC, updated_at: DEMO_NOW_UTC },
  { id: 3, workspace_id: 1, name: '赵六', phone: '138****0023', carrier_id: 4, license_no: 'A3234567', status: 'ON_TRIP', version: 1, created_at: DEMO_NOW_UTC, updated_at: DEMO_NOW_UTC },
  { id: 4, workspace_id: 1, name: '孙七', phone: '138****0024', carrier_id: 1, license_no: 'A4234567', status: 'AVAILABLE', version: 1, created_at: DEMO_NOW_UTC, updated_at: DEMO_NOW_UTC },
]

export const slaRules: SlaRule[] = [
  { id: 2, workspace_id: 1, name: '默认规则（发车后 30h / 允许延迟 30min）', scope_type: 'DEFAULT', scope_value: null, deadline_offset_hours: 30, max_delay_minutes: 30, priority: 100, description: '兜底规则', is_active: true, version: 1, created_at: DEMO_NOW_UTC, updated_at: DEMO_NOW_UTC },
  { id: 1, workspace_id: 1, name: 'VIP 客户等级规则', scope_type: 'CUSTOMER_LEVEL', scope_value: 'VIP', deadline_offset_hours: 24, max_delay_minutes: 0, priority: 10, action_policy_json: { force_notify_customer: true }, is_active: true, version: 1, created_at: DEMO_NOW_UTC, updated_at: DEMO_NOW_UTC },
  { id: 3, workspace_id: 1, name: 'VIP-01 专属规则', scope_type: 'CUSTOMER', scope_value: 'VIP-01', deadline_offset_hours: 24, max_delay_minutes: 0, priority: 1, description: '远洋集团：发车后 24h，0 分钟容忍', is_active: true, version: 1, created_at: DEMO_NOW_UTC, updated_at: DEMO_NOW_UTC },
]

/* ---------------------------------------------------------------- 订单 */

export const orderMain: Order = {
  id: 21,
  workspace_id: 1,
  order_no: 'SO20260930021',
  status: 'IN_TRANSIT',
  customer_id: 1,
  customer_name: '远洋集团',
  customer_code: 'VIP-01',
  customer_level: 'VIP',
  carrier_id: 1,
  carrier_name: '顺达运输',
  vehicle_id: 1,
  vehicle_plate: '津A·12345',
  driver_id: 1,
  driver_name: '李四',
  origin_city: '天津',
  dest_city: '上海',
  cargo_desc: '汽车配件',
  weight_ton: 4.31,
  distance_km: 800,
  sla_rule_id: 3,
  dispatched_at: CASE_A.dispatchedAt,
  promised_delivery_at: CASE_A.promisedAt,
  original_eta_at: CASE_A.promisedAt,
  current_eta_at: CASE_A.expectedAt,
  delivered_at: null,
  last_tracking_at: '2026-09-29T22:15:00Z',
  remark: 'CASE-A 主案例（VIP-01，车辆故障）',
  version: 1,
  created_at: '2026-09-26T01:20:00Z',
  updated_at: DEMO_NOW_UTC,
  sla: {
    rule_id: 3,
    rule_name: 'VIP-01 专属规则',
    scope_type: 'CUSTOMER',
    scope_value: 'VIP-01',
    deadline_offset_hours: 24,
    max_delay_minutes: 0,
    promised_delivery_at: CASE_A.promisedAt,
    expected_eta_at: CASE_A.expectedAt,
    delay_minutes: CASE_A.delayMinutes,
    breached: true,
  },
}

export const orders: Order[] = [
  orderMain,
  {
    id: 22, workspace_id: 1, order_no: 'SO20260930022', status: 'IN_TRANSIT', customer_id: 3,
    customer_name: '华东医药', customer_code: 'SVIP-01', customer_level: 'SVIP',
    carrier_id: 4, carrier_name: '陆港专线', vehicle_id: 4, vehicle_plate: '苏D·31002', driver_id: 3, driver_name: '赵六',
    origin_city: '青岛', dest_city: '杭州', cargo_desc: '冷链药品', weight_ton: 8, distance_km: 760,
    sla_rule_id: 1, dispatched_at: '2026-09-30T00:00:00Z', promised_delivery_at: '2026-10-01T00:00:00Z',
    original_eta_at: '2026-09-30T18:00:00Z', current_eta_at: '2026-09-30T17:35:00Z',
    last_tracking_at: '2026-09-30T00:30:00Z', version: 2, created_at: '2026-09-29T06:00:00Z', updated_at: DEMO_NOW_UTC,
  },
  {
    id: 23, workspace_id: 1, order_no: 'SO20260930023', status: 'IN_TRANSIT', customer_id: 2,
    customer_name: '华北贸易', customer_code: 'NORM-01', customer_level: 'NORMAL',
    carrier_id: 2, carrier_name: '捷运物流', vehicle_id: 2, vehicle_plate: '沪B·88001', driver_id: 2, driver_name: '王五',
    origin_city: '天津', dest_city: '郑州', cargo_desc: '机械设备', weight_ton: 22, distance_km: 700,
    sla_rule_id: 2, dispatched_at: '2026-09-30T01:00:00Z', promised_delivery_at: '2026-10-01T07:00:00Z',
    original_eta_at: '2026-10-01T03:00:00Z', current_eta_at: '2026-10-01T02:55:00Z',
    version: 1, created_at: '2026-09-29T08:00:00Z', updated_at: DEMO_NOW_UTC,
  },
  {
    id: 24, workspace_id: 1, order_no: 'SO20260929900', status: 'DELIVERED', customer_id: 4,
    customer_name: '珠江实业', customer_code: 'NORM-02', customer_level: 'NORMAL',
    carrier_id: 2, carrier_name: '捷运物流', vehicle_id: 2, vehicle_plate: '沪B·88001', driver_id: 2, driver_name: '王五',
    origin_city: '青岛', dest_city: '上海', cargo_desc: '家电整机', weight_ton: 25, distance_km: 720,
    sla_rule_id: 2, dispatched_at: '2026-09-29T02:00:00Z', promised_delivery_at: '2026-09-30T08:00:00Z',
    delivered_at: '2026-09-30T05:40:00Z', current_eta_at: '2026-09-30T05:40:00Z',
    version: 4, created_at: '2026-09-28T09:00:00Z', updated_at: DEMO_NOW_UTC,
  },
  {
    id: 25, workspace_id: 1, order_no: 'SO20260930025', status: 'DISPATCHED', customer_id: 1,
    customer_name: '远洋集团', customer_code: 'VIP-01', customer_level: 'VIP',
    carrier_id: 1, carrier_name: '顺达运输', vehicle_id: 3, vehicle_plate: '鲁C·66009', driver_id: 4, driver_name: '孙七',
    origin_city: '天津', dest_city: '苏州', cargo_desc: '电子元件', weight_ton: 9, distance_km: 1050,
    sla_rule_id: 3, dispatched_at: '2026-09-30T02:00:00Z', promised_delivery_at: '2026-10-01T02:00:00Z',
    current_eta_at: '2026-10-01T00:30:00Z', version: 2, created_at: '2026-09-30T01:00:00Z', updated_at: DEMO_NOW_UTC,
  },
]

/* -------------------------------------------------------------- 轨迹 */

/** 真库关键段（+08）：01:30 DEPART 天津 → 03:30 ARRIVE 滨州 → STOP/RESUME 滨州 → 06:00 ARRIVE 济南（最后一条移动类轨迹）→ 06:15 STOP 济南 */
export const trackingEventsMain: TrackingEvent[] = [
  { id: 115, workspace_id: 1, order_id: 21, event_type: 'STOP', city: '济南', address: '济南物流园', occurred_at: '2026-09-29T22:15:00Z', source: 'MOCK', speed_kmh: 0, payload_json: { stall_minutes: 180, note: '停滞 ≥120min → STALL_OVER_THRESHOLD' } },
  { id: 114, workspace_id: 1, order_id: 21, event_type: 'ARRIVE', city: '济南', address: '济南物流园', occurred_at: '2026-09-29T22:00:00Z', source: 'MOCK', speed_kmh: 74, payload_json: { note: '最后一条移动类轨迹（+08 06:00）' } },
  { id: 113, workspace_id: 1, order_id: 21, event_type: 'RESUME', city: '滨州', address: '滨州物流园', occurred_at: '2026-09-29T20:30:00Z', source: 'MOCK', speed_kmh: 55 },
  { id: 112, workspace_id: 1, order_id: 21, event_type: 'STOP', city: '滨州', address: '滨州物流园', occurred_at: '2026-09-29T20:00:00Z', source: 'MOCK', speed_kmh: 0 },
  { id: 111, workspace_id: 1, order_id: 21, event_type: 'ARRIVE', city: '滨州', address: '滨州物流园', occurred_at: '2026-09-29T19:30:00Z', source: 'MOCK', speed_kmh: 62 },
  { id: 110, workspace_id: 1, order_id: 21, event_type: 'DEPART', city: '天津', address: '天津西青物流园', occurred_at: '2026-09-29T17:30:00Z', source: 'MOCK', speed_kmh: 0 },
]

/* ---------------------------------------------------------------- 异常 */

export const exceptionRiskFactors: RiskFactor[] = [
  { code: 'DELAY_BASE', label: '延误时长', weight: 2, detail: `预计延误 ${CASE_A.delayMinutes} 分钟` },
  { code: 'CUSTOMER_VIP', label: 'VIP 客户', weight: 1, detail: 'VIP 客户需优先处理' },
  { code: 'VEHICLE_BREAKDOWN', label: '车辆故障', weight: 1, detail: '车辆故障通常需要外部资源介入' },
  { code: 'SLA_BREACH', label: 'SLA 已违约', weight: 1, detail: '已超出客户承诺时间' },
]

export const exceptionCounts: ExceptionCounts = {
  messages: 1,
  followups_open: 1,
  notifications: 1,
  approvals_pending: 3,
}

export const exceptionHistory: ExceptionHistory = {
  days: 90,
  total: 6,
  open: 6,
  avg_resolve_minutes: 45,
  recent: [
    { case_no: 'EX20260929012', type: 'DELAY_RISK', level: 'CRITICAL', status: 'DETECTED', closed_reason: null },
    { case_no: 'EX20260929007', type: 'VEHICLE_BREAKDOWN', level: 'CRITICAL', status: 'PROCESSING', closed_reason: null },
    { case_no: 'EX20260926004', type: 'VEHICLE_BREAKDOWN', level: 'CRITICAL', status: 'PROCESSING', closed_reason: null },
  ],
}

export const carrierMessages: CarrierMessage[] = [
  {
    id: 1,
    workspace_id: 1,
    exception_id: 1,
    order_id: 21,
    channel: 'MANUAL_PASTE',
    sender_name: '顺达运输-马队长',
    sender_role: 'CARRIER',
    raw_text: '车在济南爆胎了，现在联系修理厂，预计晚上 8 点恢复。',
    received_at: '2026-09-30T00:10:00Z',
    parse_status: 'PARSED',
    parse_result: {
      exception_type: 'VEHICLE_BREAKDOWN',
      location: '济南',
      status: 'REPAIRING',
      estimated_recovery_at: CASE_A.recoveryAt,
      confidence: 0.93,
      missing_info: [],
    },
    parser_version: 'v1',
    parse_error: null,
    created_at: '2026-09-30T00:10:00Z',
  },
  {
    id: 2,
    workspace_id: 1,
    exception_id: 1,
    order_id: 21,
    channel: 'MOCK_WECHAT',
    sender_name: '顺达运输-马队长',
    sender_role: 'CARRIER',
    raw_text: '修理厂说配件要调，可能还要再等一小时。',
    received_at: '2026-09-30T00:40:00Z',
    parse_status: 'PARSED',
    parse_result: {
      exception_type: 'VEHICLE_BREAKDOWN',
      location: '济南',
      status: 'WAITING_PARTS',
      estimated_recovery_at: '2026-09-30T21:00:00+08:00',
      confidence: 0.58,
      missing_info: ['是否已确认配件到货时间'],
    },
    parser_version: 'v1',
    created_at: '2026-09-30T00:40:00Z',
  },
]

export const exceptionMain: ExceptionDetail = {
  id: 1,
  case_no: 'EX20260930001',
  order_id: 21,
  order_no: 'SO20260930021',
  customer_id: 1,
  customer_name: '远洋集团',
  customer_level: 'VIP',
  vehicle_id: 1,
  vehicle_plate: '津A·12345',
  carrier_id: 1,
  type: 'VEHICLE_BREAKDOWN',
  level: 'CRITICAL',
  status: 'PROCESSING',
  detected_by: 'SYSTEM',
  detection_rule: 'STALL_OVER_THRESHOLD',
  occurred_at: '2026-09-29T22:00:00Z',
  stall_since: '2026-09-29T22:00:00Z',
  root_cause: { code: 'VEHICLE_BREAKDOWN', note: '承运商反馈右后轮爆胎，正在联系修理厂' },
  impact_summary: `SO20260930021 天津→上海：车辆故障；客户 远洋集团（VIP）承诺 2026-10-01 01:30，预计 2026-10-01 06:00 到达，延误 ${CASE_A.delayMinutes} 分钟，已超出承诺时间；车辆 津A·12345(REPAIRING)`,
  promised_delivery_at: CASE_A.promisedAt,
  current_eta_at: CASE_A.expectedAt,
  expected_eta_at: CASE_A.expectedAt,
  sla_delay_minutes: CASE_A.delayMinutes,
  sla_breached: true,
  risk_score: CASE_A.riskScore,
  risk_factors: exceptionRiskFactors,
  assigned_to: 3,
  resolved_at: null,
  closed_at: null,
  close_reason: null,
  merged_count: 0,
  version: 1,
  created_at: '2026-09-30T00:00:00Z',
  updated_at: DEMO_NOW_UTC,
  order: orderMain,
  customer: customers[0],
  vehicle: vehicles[0],
  sla: orderMain.sla,
  tracking_events: trackingEventsMain,
  latest_carrier_message: carrierMessages[0],
  history: exceptionHistory,
  latest_analysis: null,
  counts: exceptionCounts,
}

/** 最新分析摘要（异常详情内嵌；实测只有扁平字段 + summary） */
export const exceptionAnalysisSummary: AnalysisSummary = {
  id: 2,
  analysis_no: 'AI20260930000001',
  task_type: 'ANALYZE_EXCEPTION',
  status: 'READY',
  is_replay: true,
  risk_level_calculated: 'CRITICAL',
  error_code: null,
  error_message: null,
  reused_from_id: null,
  started_at: DEMO_NOW_UTC,
  finished_at: DEMO_NOW_UTC,
  summary: exceptionMain.impact_summary,
}

/** 其余异常（列表演示用；主案例 created_at 最新，配合 -risk_score,-created_at 排首行） */
function otherExceptions(): ExceptionListItem[] {
  const base: ExceptionListItem[] = [
    { id: 40, case_no: 'EX20260930008', order_id: 987, order_no: 'SO20260930987', customer_id: 3, customer_name: '华东医药', type: 'DELAY_RISK', level: 'CRITICAL', status: 'DETECTED', detected_by: 'SYSTEM', detection_rule: 'ETA_BREACH_SLA', occurred_at: '2026-09-29T21:22:00Z', sla_delay_minutes: 400, sla_breached: true, risk_score: 4, assigned_to: 3, merged_count: 0, version: 1, created_at: '2026-09-29T21:22:00Z', updated_at: '2026-09-30T00:30:00Z', vehicle_plate: '苏D·31002', impact_summary: 'SVIP 冷链单预计延误 400 分钟' },
    { id: 27, case_no: 'EX20260924003', order_id: 439, order_no: 'SO20260930439', customer_id: 5, customer_name: '中远海运', type: 'VEHICLE_BREAKDOWN', level: 'CRITICAL', status: 'PROCESSING', detected_by: 'SYSTEM', detection_rule: 'STALL_OVER_THRESHOLD', occurred_at: '2026-09-24T03:20:00Z', sla_delay_minutes: 300, sla_breached: true, risk_score: 4, assigned_to: 3, merged_count: 0, version: 1, created_at: '2026-09-24T03:20:00Z', updated_at: '2026-09-30T00:10:00Z', vehicle_plate: '沪B·12353', impact_summary: '车辆离线导致停滞' },
    { id: 47, case_no: 'EX20260926011', order_id: 490, order_no: 'SO20260930490', customer_id: 2, customer_name: '华北贸易', type: 'VEHICLE_BREAKDOWN', level: 'CRITICAL', status: 'PROCESSING', detected_by: 'OPERATOR', detection_rule: 'MANUAL', occurred_at: '2026-09-26T10:00:00Z', sla_delay_minutes: 300, sla_breached: true, risk_score: 4, assigned_to: 2, merged_count: 1, version: 2, created_at: '2026-09-26T10:00:00Z', updated_at: '2026-09-29T23:00:00Z', vehicle_plate: '沪B·88001', impact_summary: '运营手工建单后确认车辆故障' },
    { id: 49, case_no: 'EX20260929011', order_id: 646, order_no: 'SO20260930646', customer_id: 4, customer_name: '珠江实业', type: 'DELAY_RISK', level: 'HIGH', status: 'RESOLVED', detected_by: 'SYSTEM', detection_rule: 'ETA_BREACH_SLA', occurred_at: '2026-09-29T05:00:00Z', sla_delay_minutes: 25, sla_breached: false, risk_score: 3, assigned_to: null, merged_count: 0, version: 1, created_at: '2026-09-29T05:00:00Z', updated_at: '2026-09-29T09:00:00Z', vehicle_plate: '鲁C·66009', impact_summary: 'CASE-D 边界：延误 25min < 允许 30min' },
    { id: 46, case_no: 'EX20260929010', order_id: 424, order_no: 'SO20260930424', customer_id: 1, customer_name: '远洋集团', type: 'VEHICLE_BREAKDOWN', level: 'CRITICAL', status: 'CLOSED', detected_by: 'SYSTEM', detection_rule: 'STALL_OVER_THRESHOLD', occurred_at: '2026-09-29T02:00:00Z', sla_delay_minutes: 200, sla_breached: true, risk_score: 4, assigned_to: 3, merged_count: 0, version: 3, created_at: '2026-09-29T02:00:00Z', updated_at: '2026-09-29T12:00:00Z', closed_at: '2026-09-29T12:00:00Z', close_reason: 'DELIVERED', vehicle_plate: '津A·12345', impact_summary: 'CASE-B 已闭环案例（分析/审批/通知/跟进/关闭）' },
    { id: 30, case_no: 'EX20260925003', order_id: 803, order_no: 'SO20260930803', customer_id: 2, customer_name: '华北贸易', type: 'DELAY_RISK', level: 'CRITICAL', status: 'CLOSED', detected_by: 'OPERATOR', detection_rule: 'MANUAL', occurred_at: '2026-09-25T02:00:00Z', sla_delay_minutes: 400, sla_breached: true, risk_score: 4, assigned_to: 2, merged_count: 0, version: 4, created_at: '2026-09-25T02:00:00Z', updated_at: '2026-09-25T10:00:00Z', closed_at: '2026-09-25T10:00:00Z', close_reason: 'INVALID', impact_summary: 'CASE-C 误报：装卸排队，人工关闭 INVALID' },
    { id: 39, case_no: 'EX20260928003', order_id: 455, order_no: 'SO20260930455', customer_id: 4, customer_name: '珠江实业', type: 'DELAY_RISK', level: 'HIGH', status: 'PROCESSING', detected_by: 'SYSTEM', detection_rule: 'ETA_BREACH_SLA', occurred_at: '2026-09-28T06:00:00Z', sla_delay_minutes: 100, sla_breached: true, risk_score: 3, assigned_to: 3, merged_count: 0, version: 1, created_at: '2026-09-28T06:00:00Z', updated_at: '2026-09-28T11:00:00Z', vehicle_plate: '沪B·88001', impact_summary: '预计延误 100 分钟' },
  ]
  const levels = ['LOW', 'MEDIUM', 'HIGH'] as const
  const statuses = ['DETECTED', 'PROCESSING', 'RESOLVED', 'CLOSED'] as const
  const generated: ExceptionListItem[] = []
  for (let i = 0; i < 43; i += 1) {
    const level = levels[i % levels.length]
    const status = statuses[(i * 3) % statuses.length]
    const breached = i % 4 === 0
    const customer = customers[i % customers.length]
    generated.push({
      id: 100 + i,
      case_no: `EX202609${String(2000 + i).slice(1)}`,
      order_id: 300 + i,
      order_no: `SO2026093${String(1000 + i).slice(-4)}`,
      customer_id: customer.id,
      customer_name: customer.name,
      type: i % 3 === 0 ? 'DELAY_RISK' : 'VEHICLE_BREAKDOWN',
      level,
      status,
      detected_by: i % 5 === 0 ? 'OPERATOR' : 'SYSTEM',
      detection_rule: i % 3 === 0 ? 'ETA_BREACH_SLA' : 'STALL_OVER_THRESHOLD',
      occurred_at: `2026-09-2${(i % 9) + 1}T0${i % 10}:${String((i * 7) % 60).padStart(2, '0')}:00Z`,
      stall_since: null,
      promised_delivery_at: '2026-10-01T02:00:00Z',
      current_eta_at: '2026-10-01T01:00:00Z',
      expected_eta_at: `2026-10-01T0${(i % 8) + 1}:00:00Z`,
      sla_delay_minutes: breached ? 60 + i : 0,
      sla_breached: breached,
      risk_score: level === 'HIGH' ? 3 : level === 'MEDIUM' ? 2 : 1,
      impact_summary: '演示用异常记录',
      assigned_to: i % 2 === 0 ? 3 : 2,
      merged_count: 0,
      version: 1,
      created_at: `2026-09-2${(i % 9) + 1}T0${i % 10}:00:00Z`,
      updated_at: `2026-09-30T1${i % 10}:00:00Z`,
      vehicle_plate: vehicles[i % vehicles.length].plate_no,
    })
  }
  return [...base, ...generated]
}

/** 列表：主案例在最前（created_at 最新 + risk_score 4） */
export const exceptionList: ExceptionListItem[] = [
  { ...exceptionMain },
  ...otherExceptions(),
]

/* ---------------------------------------------------------- AI 分析步骤 */

/** 实测 8 步（后端上限 14 步）：6 个只读工具 + 1 次 LLM 输出 + 1 次 schema 校验 */
export const aiSteps: AiAnalysisStep[] = [
  { step_no: 1, step_type: 'TOOL', tool_name: 'get_order', args: { order_id: 21 }, status: 'OK', duration_ms: 0, error: null, result_summary: 'SO20260930021 天津→上海 IN_TRANSIT', created_at: DEMO_NOW_UTC },
  { step_no: 2, step_type: 'TOOL', tool_name: 'get_tracking_events', args: { order_id: 21, limit: 10 }, status: 'OK', duration_ms: 0, error: null, result_summary: '6 条轨迹，最后位置 济南 09-30 06:15', created_at: DEMO_NOW_UTC },
  { step_no: 3, step_type: 'TOOL', tool_name: 'get_customer_sla', args: { customer_id: 1, order_id: 21 }, status: 'OK', duration_ms: 0, error: null, result_summary: `CUSTOMER:VIP-01 发车后 24h，允许延迟 0min，延误 ${CASE_A.delayMinutes}min`, created_at: DEMO_NOW_UTC },
  { step_no: 4, step_type: 'TOOL', tool_name: 'get_vehicle', args: { vehicle_id: 1 }, status: 'OK', duration_ms: 0, error: null, result_summary: '津A·12345 REPAIRING 当前 济南', created_at: DEMO_NOW_UTC },
  { step_no: 5, step_type: 'TOOL', tool_name: 'get_exception_history', args: { customer_id: 1, days: 90 }, status: 'OK', duration_ms: 0, error: null, result_summary: '近 90 天 6 次，未结 6 次', created_at: DEMO_NOW_UTC },
  { step_no: 6, step_type: 'TOOL', tool_name: 'search_knowledge', args: { query: '车辆故障', top_k: 5 }, status: 'OK', duration_ms: 0, error: null, result_summary: '命中 5 条（车辆故障处理规范#2.1）', created_at: DEMO_NOW_UTC },
  { step_no: 7, step_type: 'LLM', tool_name: null, args: null, status: 'OK', duration_ms: 0, error: null, result_summary: `输出 JSON（template）：车辆故障导致停滞，预计延误 ${CASE_A.delayMinutes} 分钟`, created_at: DEMO_NOW_UTC },
  { step_no: 8, step_type: 'VALIDATE', tool_name: null, args: null, status: 'OK', duration_ms: 0, error: null, result_summary: 'schema 与事实一致性校验通过', created_at: DEMO_NOW_UTC },
]

export const aiOutput: AiAnalysisOutput = {
  summary: exceptionMain.impact_summary ?? '',
  root_cause: { code: 'VEHICLE_BREAKDOWN', note: '承运商反馈右后轮爆胎，正在联系修理厂' },
  impact: { delay_minutes: CASE_A.delayMinutes, sla_breached: true, affected_customer_level: 'VIP' },
  suggestions: [
    { code: 'UPDATE_ETA', title: '将 ETA 更新为 2026-10-01 06:00', rationale: '依据承运商反馈与系统规则重算的预计到达时间', assignee_role: null },
    { code: 'CREATE_FOLLOWUP', title: '09:30 回访承运商确认恢复情况', rationale: '车辆处于故障/延误状态，需在承诺时间前确认进展', assignee_role: 'OPERATOR' },
    { code: 'SAVE_NOTICE', title: '生成并发送延误通知给客户', rationale: '客户等级与违约状态触达通知规范要求', assignee_role: null },
  ],
  open_questions: ['修理厂是否已确认配件到位？'],
  evidence_refs: [
    { type: 'ORDER', id: 21, note: '订单 SO20260930021', doc: null, section: null },
    { type: 'TRACKING_EVENT', id: 115, note: '09-30 06:15 位于 济南', doc: null, section: null },
    { type: 'VEHICLE', id: 1, note: '津A·12345 REPAIRING', doc: null, section: null },
    { type: 'CARRIER_MESSAGE', id: 1, note: '“车在济南爆胎了…预计晚上 8 点恢复”', doc: null, section: null },
    { type: 'KNOWLEDGE_CHUNK', id: 7, note: '故障期间承运商须每 30 分钟更新恢复时间', doc: '车辆故障处理规范', section: '2.1 车辆故障' },
  ],
}

export function makeAiAnalysis(analysisId = 2): AiAnalysis {
  return {
    id: analysisId,
    workspace_id: 1,
    exception_id: 1,
    analysis_no: `AI2026093000000${analysisId}`,
    task_type: 'ANALYZE_EXCEPTION',
    status: 'READY',
    input_hash: '3190b20ddfe05de8a5bd8daa6f038379461c390f4af6baf73a1cebf3409e715d',
    model: 'template',
    prompt_version: 'v1',
    output: aiOutput,
    risk_level_calculated: 'CRITICAL',
    error_code: null,
    error_message: null,
    tokens_in: 0,
    tokens_out: 0,
    latency_ms: 46,
    reused_from_id: null,
    is_replay: true,
    started_at: DEMO_NOW_UTC,
    finished_at: DEMO_NOW_UTC,
    created_at: DEMO_NOW_UTC,
    steps: aiSteps,
  }
}

/* ---------------------------------------------------------------- 审批 */

/**
 * 3 张待决策审批单（实测字段：ai_payload / final_payload / diff / execution_result；
 * diff 里的 fields 是 map: {field: {ai, final}}）
 */
export const approvals: Approval[] = [
  {
    id: 2,
    workspace_id: 1,
    exception_id: 1,
    analysis_id: 2,
    action_type: 'UPDATE_ETA',
    target_type: 'order',
    target_id: 21,
    ai_payload: { eta_at: CASE_A.expectedAt, reason: '依据承运商反馈与系统规则重算的预计到达时间' },
    final_payload: null,
    diff: null,
    status: 'PENDING',
    decided_by: null,
    decided_at: null,
    reject_reason: null,
    executed_at: null,
    execution_result: null,
    error_message: null,
    retry_count: 0,
    expires_at: '2026-10-01T01:00:00Z',
    version: 1,
    created_at: DEMO_NOW_UTC,
    updated_at: DEMO_NOW_UTC,
  },
  {
    id: 3,
    workspace_id: 1,
    exception_id: 1,
    analysis_id: 2,
    action_type: 'CREATE_FOLLOWUP',
    target_type: 'followup_task',
    target_id: null,
    ai_payload: { title: '09:30 回访承运商确认恢复情况', content: '车辆处于故障/延误状态，需在承诺时间前确认进展', assignee_role: 'OPERATOR', due_at: '2026-09-30T02:00:00Z', priority: 'NORMAL' },
    final_payload: null,
    diff: null,
    status: 'PENDING',
    decided_by: null,
    decided_at: null,
    reject_reason: null,
    executed_at: null,
    execution_result: null,
    error_message: null,
    retry_count: 0,
    expires_at: '2026-10-01T01:00:00Z',
    version: 1,
    created_at: DEMO_NOW_UTC,
    updated_at: DEMO_NOW_UTC,
  },
  {
    id: 4,
    workspace_id: 1,
    exception_id: 1,
    analysis_id: 2,
    action_type: 'SAVE_NOTICE',
    target_type: 'notification',
    target_id: null,
    ai_payload: {
      subject: '【延误告知】SO20260930021 预计 2026-10-01 06:00 送达',
      content: '尊敬的客户：您的订单 SO20260930021（天津→上海）因车辆故障，预计到达时间调整为 2026-10-01 06:00，我方已安排专人跟进。',
    },
    final_payload: null,
    diff: null,
    status: 'PENDING',
    decided_by: null,
    decided_at: null,
    reject_reason: null,
    executed_at: null,
    execution_result: null,
    error_message: null,
    retry_count: 0,
    expires_at: '2026-10-01T01:00:00Z',
    version: 1,
    created_at: DEMO_NOW_UTC,
    updated_at: DEMO_NOW_UTC,
  },
]

/* ------------------------------------------------------------ 跟进任务 */

export const followups: FollowupTask[] = [
  {
    id: 1,
    workspace_id: 1,
    exception_id: 1,
    title: '09:30 回访承运商确认恢复情况',
    content: '确认修理厂是否已换好轮胎，并让司机补一条轨迹',
    assignee_user_id: 3,
    assignee_name: '张三',
    due_at: '2026-09-30T02:00:00Z',
    priority: 'NORMAL',
    status: 'OPEN',
    source: 'AI_SUGGESTED',
    source_approval_id: 3,
    done_at: null,
    done_by: null,
    version: 1,
    created_at: DEMO_NOW_UTC,
    updated_at: DEMO_NOW_UTC,
  },
]

/* -------------------------------------------------------------- 通知 */

export const notifications: Notification[] = [
  {
    id: 1,
    workspace_id: 1,
    exception_id: 1,
    customer_id: 1,
    customer_name: '远洋集团',
    channel: 'MANUAL_COPY',
    subject: '【延误告知】SO20260930021 预计 2026-10-01 06:00 送达',
    content: '尊敬的客户：您的订单 SO20260930021（天津→上海）因车辆故障，预计到达时间调整为 2026-10-01 06:00，我方已安排专人跟进。',
    ai_draft_content: '尊敬的客户：您的订单 SO20260930021（天津→上海）因车辆故障，预计到达时间调整为 2026-10-01 06:00，我方已安排专人跟进。',
    status: 'DRAFT',
    approved_by: null,
    approved_at: null,
    sent_at: null,
    source_approval_id: 4,
    version: 1,
    created_at: DEMO_NOW_UTC,
    updated_at: DEMO_NOW_UTC,
  },
]

/* ------------------------------------------------------- 异常时间线 */

export const exceptionEvents: ExceptionEvent[] = [
  { id: 1, workspace_id: 1, exception_id: 1, event_type: 'DETECTED', actor_type: 'SYSTEM', actor_id: null, from_status: null, to_status: 'DETECTED', note: '规则 STALL_OVER_THRESHOLD：济南停滞 180 分钟', detail: { detection_rule: 'STALL_OVER_THRESHOLD', stall_minutes: 180 }, occurred_at: '2026-09-30T01:00:00Z' },
  { id: 2, workspace_id: 1, exception_id: 1, event_type: 'MESSAGE_ADDED', actor_type: 'USER', actor_id: 3, from_status: 'DETECTED', to_status: 'DETECTED', note: '录入承运商消息并触发解析（4 状态模型：不自动推进状态）', detail: { carrier_message_id: 1, parse_status: 'PARSED' }, occurred_at: '2026-09-30T00:10:00Z' },
  { id: 3, workspace_id: 1, exception_id: 1, event_type: 'CONFIRMED', actor_type: 'USER', actor_id: 3, from_status: 'DETECTED', to_status: 'PROCESSING', note: '信息完整，确认异常，进入处理中', detail: null, occurred_at: '2026-09-30T00:20:00Z' },
  { id: 4, workspace_id: 1, exception_id: 1, event_type: 'ANALYSIS_REQUESTED', actor_type: 'USER', actor_id: 3, from_status: 'PROCESSING', to_status: 'PROCESSING', note: '触发 AI 分析（ANALYZE_EXCEPTION）', detail: { analysis_id: 2 }, occurred_at: '2026-09-30T01:00:00Z' },
  { id: 5, workspace_id: 1, exception_id: 1, event_type: 'ANALYSIS_READY', actor_type: 'AI', actor_id: null, from_status: 'PROCESSING', to_status: 'PROCESSING', note: '分析完成，规则等级 CRITICAL，生成 3 张审批单', detail: { analysis_id: 2, risk_level_calculated: 'CRITICAL', approvals: 3 }, occurred_at: '2026-09-30T01:00:00Z' },
]

/* -------------------------------------------------------------- 审计 */

export const auditLogs: AuditLog[] = [
  { id: 28, workspace_id: 1, actor_type: 'USER', actor_id: 3, action: 'auth.login', resource_type: 'user', resource_id: 3, before_json: null, after_json: { email: 'operator@logiops.dev' }, source: 'MANUAL', request_id: null, ip: null, user_agent: null, occurred_at: '2026-09-30T02:30:00Z' },
  { id: 27, workspace_id: 1, actor_type: 'AI', actor_id: null, action: 'ai.analysis_ready', resource_type: 'ai_analysis', resource_id: 2, before_json: null, after_json: { tokens_in: 0, tokens_out: 0, latency_ms: 46, is_replay: true }, source: 'SYSTEM', request_id: null, ip: null, user_agent: null, occurred_at: '2026-09-30T01:00:00Z' },
  { id: 26, workspace_id: 1, actor_type: 'USER', actor_id: 3, action: 'exception.message_added', resource_type: 'exception_case', resource_id: 1, before_json: null, after_json: { carrier_message_id: 1, parse_status: 'PARSED' }, source: 'MANUAL', request_id: null, ip: null, user_agent: null, occurred_at: '2026-09-30T00:10:00Z' },
  { id: 25, workspace_id: 1, actor_type: 'SYSTEM', actor_id: null, action: 'exception.detected', resource_type: 'exception_case', resource_id: 1, before_json: null, after_json: { detection_rule: 'STALL_OVER_THRESHOLD', level: 'CRITICAL', risk_score: 4 }, source: 'SYSTEM', request_id: null, ip: null, user_agent: null, occurred_at: '2026-09-30T01:00:00Z' },
  { id: 24, workspace_id: 1, actor_type: 'SYSTEM', actor_id: null, action: 'order.eta_updated', resource_type: 'order', resource_id: 21, before_json: { current_eta_at: CASE_A.promisedAt }, after_json: { current_eta_at: CASE_A.expectedAt, eta_method: 'REPAIR_WAIT' }, source: 'SYSTEM', request_id: null, ip: null, user_agent: null, occurred_at: '2026-09-30T00:05:00Z' },
  { id: 23, workspace_id: 1, actor_type: 'SYSTEM', actor_id: null, action: 'security.cross_tenant_denied', resource_type: 'order', resource_id: 404, before_json: null, after_json: { reason: 'workspace mismatch' }, source: 'SYSTEM', request_id: null, ip: null, user_agent: null, occurred_at: '2026-09-29T23:00:00Z' },
]

/* -------------------------------------------------------------- 知识库 */

/** 按 ## 小节切分（§11.6），前端用于展示可点击的原文小节 */
export const knowledgeChunks: KnowledgeChunk[] = [
  { id: 7, doc_id: 4, chunk_no: 2, section_path: '2.1 车辆故障', content: '车辆发生故障（爆胎、机械故障等）时，承运商须在 15 分钟内告知调度；调度须在 30 分钟内录入系统并评估对承诺到达时间的影响。若影响超过 SLA 允许延迟，必须同步通知客户。', token_estimate: 118, score: 3.42 },
  { id: 8, doc_id: 4, chunk_no: 3, section_path: '2.2 等待配件', content: '等待配件期间，承运商须每 30 分钟更新一次恢复时间；连续两次未按时更新时，运营须升级处理并考虑改派车辆。', token_estimate: 96, score: 2.87 },
  { id: 2, doc_id: 1, chunk_no: 1, section_path: '1.1 适用范围', content: '适用于公路整车运输在途阶段发生的全部异常，包括车辆故障、延误风险、位置停滞与客户投诉；不适用于货损货差理赔、单证与关务争议。', token_estimate: 102, score: 3.1 },
  { id: 9, doc_id: 3, chunk_no: 1, section_path: '1.1 VIP 客户告知义务', content: 'VIP/SVIP 客户发生时效偏差时，须在 30 分钟内以书面形式（邮件/短信）告知客户新的预计到达时间与处理进展。', token_estimate: 104, score: 2.61 },
  { id: 10, doc_id: 5, chunk_no: 4, section_path: '1.2 通知要素', content: '客户通知必须包含：订单号、新的预计到达时间、延误原因概述、下一步处理动作、联系人。禁止出现未经系统确认的时间。', token_estimate: 112, score: 2.33 },
]

export const knowledgeDocs: KnowledgeDoc[] = [
  { id: 1, doc_code: 'KD-EXC', title: '异常处理规范', category: 'EXCEPTION', version: 'v1', source_path: 'backend/app/knowledge/异常处理规范.md', status: 'ACTIVE', chunk_count: 12, updated_at: DEMO_NOW_UTC, created_at: DEMO_NOW_UTC },
  { id: 3, doc_code: 'KD-VIP', title: 'VIP客户服务规则', category: 'CUSTOMER', version: 'v1', source_path: 'backend/app/knowledge/VIP客户服务规则.md', status: 'ACTIVE', chunk_count: 10, updated_at: DEMO_NOW_UTC, created_at: DEMO_NOW_UTC },
  { id: 4, doc_code: 'KD-VEH', title: '车辆故障处理规范', category: 'VEHICLE', version: 'v1', source_path: 'backend/app/knowledge/车辆故障处理规范.md', status: 'ACTIVE', chunk_count: 14, updated_at: DEMO_NOW_UTC, created_at: DEMO_NOW_UTC },
  { id: 5, doc_code: 'KD-NOTICE', title: '客户通知规范', category: 'NOTIFICATION', version: 'v1', source_path: 'backend/app/knowledge/客户通知规范.md', status: 'ACTIVE', chunk_count: 10, updated_at: DEMO_NOW_UTC, created_at: DEMO_NOW_UTC },
]

export const knowledgeDocChunks: Record<number, KnowledgeChunk[]> = {
  4: [knowledgeChunks[0], knowledgeChunks[1]],
  1: [knowledgeChunks[2]],
  3: [knowledgeChunks[3]],
  5: [knowledgeChunks[4]],
}

export const knowledgeDocDetail: KnowledgeDocDetail = {
  ...knowledgeDocs[2],
  chunks: knowledgeDocChunks[4],
}

/* -------------------------------------------------------------- Dashboard */

/** 实测字段：pending / sla_breached_open / delayed_orders … */
export const dashboardSummary: DashboardSummary = {
  generated_at: DEMO_NOW_UTC,
  now_utc: DEMO_NOW_UTC,
  business_date: DEMO_BASE_DATE,
  today_orders: 128,
  in_transit: 300,
  delayed_orders: 26,
  exceptions_total: 50,
  open_exceptions: 39,
  high_risk: 14,
  pending: 19,
  processing: 9,
  resolving: 7,
  resolved: 7,
  closed: 11,
  sla_breached: 29,
  sla_breached_open: 18,
  by_level: { CRITICAL: 14, HIGH: 14, MEDIUM: 18, LOW: 4 },
  by_status: { DETECTED: 9, PROCESSING: 23, RESOLVED: 7, CLOSED: 11 },
}

/** 实测趋势点字段：detected / breached / resolved / closed */
export const dashboardTrend: DashboardTrend = {
  days: 7,
  start_date: '2026-09-24',
  end_date: '2026-09-30',
  items: [
    { date: '2026-09-24', detected: 5, breached: 1, resolved: 3, closed: 2 },
    { date: '2026-09-25', detected: 7, breached: 2, resolved: 4, closed: 3 },
    { date: '2026-09-26', detected: 4, breached: 0, resolved: 6, closed: 4 },
    { date: '2026-09-27', detected: 9, breached: 3, resolved: 5, closed: 3 },
    { date: '2026-09-28', detected: 6, breached: 1, resolved: 7, closed: 5 },
    { date: '2026-09-29', detected: 11, breached: 4, resolved: 6, closed: 4 },
    { date: '2026-09-30', detected: 8, breached: 2, resolved: 4, closed: 2 },
  ],
}
