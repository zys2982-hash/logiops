import { get, patch, post } from './request'
import { normalizePage } from './master'
import type {
  ExceptionListItem,
  Order,
  OrderBrief,
  OrderCreatePayload,
  OrderQuery,
  OrderUpdatePayload,
  Page as PageType,
  SlaRule,
  TrackingEvent,
  TrackingEventCreatePayload,
} from '@/types'

/* ---------------------------------------------------------------- 订单 */

/** GET /orders ?status&customer_id&order_no&created_from&created_to&sort&page */
export async function listOrders(query: OrderQuery = {}): Promise<PageType<OrderBrief>> {
  const raw = await get<PageType<OrderBrief> | OrderBrief[]>('/orders', { ...query })
  return normalizePage(raw, query.page ?? 1, query.page_size ?? 20)
}

/** GET /orders/{id} → 含 customer/carrier/vehicle/driver 摘要 + sla 快照 + 当前异常摘要 */
export function getOrder(id: number): Promise<Order> {
  return get<Order>(`/orders/${id}`)
}

/** POST /orders（CREATED） */
export function createOrder(payload: OrderCreatePayload): Promise<Order> {
  return post<Order>('/orders', payload)
}

/** PATCH /orders/{id}（填 vehicle/carrier 时按状态机派车） */
export function updateOrder(id: number, payload: OrderUpdatePayload): Promise<Order> {
  return patch<Order>(`/orders/${id}`, payload)
}

/**
 * PATCH /orders/{id}/delivered-at —— 修正**实际送达时间**（送达时间录错时用，需要 order.manage）。
 * 延误单只在送达后按"实际送达 − 承诺送达"判定，所以纠错入口是这里；改完后端立刻重算延误单。
 */
export function correctDeliveredAt(
  id: number,
  payload: { delivered_at: string; note?: string },
): Promise<Order> {
  return patch<Order>(`/orders/${id}/delivered-at`, payload)
}

/** GET /orders/{id}/tracking-events → 实测返回 {items,total,page,page_size}（兼容平数组） */
export async function listTrackingEvents(orderId: number): Promise<TrackingEvent[]> {
  const raw = await get<PageType<TrackingEvent> | TrackingEvent[]>(
    `/orders/${orderId}/tracking-events`,
  )
  return Array.isArray(raw) ? raw : (raw?.items ?? [])
}

/** POST /orders/{id}/tracking-events（写入后同步触发 ETA 重算与异常检测） */
export function createTrackingEvent(
  orderId: number,
  payload: TrackingEventCreatePayload,
): Promise<TrackingEvent> {
  return post<TrackingEvent>(`/orders/${orderId}/tracking-events`, payload)
}

/**
 * GET /orders/{id}/exceptions
 * 实测：返回**平数组**（与 lead 给的 {items:[...]} 不同），这里两种都兼容。
 */
export async function listOrderExceptions(orderId: number): Promise<ExceptionListItem[]> {
  const raw = await get<PageType<ExceptionListItem> | ExceptionListItem[]>(
    `/orders/${orderId}/exceptions`,
  )
  return Array.isArray(raw) ? raw : (raw?.items ?? [])
}

/* ------------------------------------------------------------ SLA 规则 */

export async function listSlaRules(
  query: { page?: number; page_size?: number } = {},
): Promise<PageType<SlaRule>> {
  const raw = await get<PageType<SlaRule> | SlaRule[]>('/sla-rules', { ...query })
  return normalizePage(raw, query.page ?? 1, query.page_size ?? 50)
}

export function createSlaRule(payload: Partial<SlaRule>): Promise<SlaRule> {
  return post<SlaRule>('/sla-rules', payload)
}

export function updateSlaRule(id: number, payload: Partial<SlaRule>): Promise<SlaRule> {
  return patch<SlaRule>(`/sla-rules/${id}`, payload)
}
