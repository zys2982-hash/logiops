import { get, patch, post } from './request'
import type { Carrier, CarrierQuery, Customer, CustomerQuery, Page, Page as PageType } from '@/types'

/** 后端可能直接返回数组（未实现分页）也可能返回分页对象，这里做兼容归一 */
export function normalizePage<T>(
  raw: PageType<T> | T[],
  page = 1,
  pageSize = 20,
): Page<T> {
  if (Array.isArray(raw)) {
    return { items: raw, total: raw.length, page, page_size: pageSize }
  }
  return {
    items: raw?.items ?? [],
    total: raw?.total ?? raw?.items?.length ?? 0,
    page: raw?.page ?? page,
    page_size: raw?.page_size ?? pageSize,
  }
}

/* ---------------------------------------------------------------- 客户 */

export async function listCustomers(query: CustomerQuery = {}): Promise<PageType<Customer>> {
  const raw = await get<PageType<Customer> | Customer[]>('/customers', { ...query })
  return normalizePage(raw, query.page ?? 1, query.page_size ?? 20)
}

export function getCustomer(id: number): Promise<Customer> {
  return get<Customer>(`/customers/${id}`)
}

export function createCustomer(payload: Partial<Customer>): Promise<Customer> {
  return post<Customer>('/customers', payload)
}

export function updateCustomer(id: number, payload: Partial<Customer>): Promise<Customer> {
  return patch<Customer>(`/customers/${id}`, payload)
}

/* -------------------------------------------------------------- 承运商 */

export async function listCarriers(query: CarrierQuery = {}): Promise<PageType<Carrier>> {
  const raw = await get<PageType<Carrier> | Carrier[]>('/carriers', { ...query })
  return normalizePage(raw, query.page ?? 1, query.page_size ?? 20)
}

export function getCarrier(id: number): Promise<Carrier> {
  return get<Carrier>(`/carriers/${id}`)
}

export function createCarrier(payload: Partial<Carrier>): Promise<Carrier> {
  return post<Carrier>('/carriers', payload)
}

export function updateCarrier(id: number, payload: Partial<Carrier>): Promise<Carrier> {
  return patch<Carrier>(`/carriers/${id}`, payload)
}

/* ---------------------------------------------------------------- 车辆 */

export async function listVehicles(
  query: import('@/types').VehicleQuery = {},
): Promise<PageType<import('@/types').Vehicle>> {
  type V = import('@/types').Vehicle
  const raw = await get<PageType<V> | V[]>('/vehicles', { ...query })
  return normalizePage(raw, query.page ?? 1, query.page_size ?? 20)
}

export function getVehicle(id: number): Promise<import('@/types').Vehicle> {
  return get(`/vehicles/${id}`)
}

export function createVehicle(
  payload: Partial<import('@/types').Vehicle>,
): Promise<import('@/types').Vehicle> {
  return post('/vehicles', payload)
}

export function updateVehicle(
  id: number,
  payload: Partial<import('@/types').Vehicle>,
): Promise<import('@/types').Vehicle> {
  return patch(`/vehicles/${id}`, payload)
}

/* ---------------------------------------------------------------- 司机 */

export async function listDrivers(
  query: import('@/types').DriverQuery = {},
): Promise<PageType<import('@/types').Driver>> {
  type D = import('@/types').Driver
  const raw = await get<PageType<D> | D[]>('/drivers', { ...query })
  return normalizePage(raw, query.page ?? 1, query.page_size ?? 20)
}

export function getDriver(id: number): Promise<import('@/types').Driver> {
  return get(`/drivers/${id}`)
}

export function createDriver(
  payload: Partial<import('@/types').Driver>,
): Promise<import('@/types').Driver> {
  return post('/drivers', payload)
}

export function updateDriver(
  id: number,
  payload: Partial<import('@/types').Driver>,
): Promise<import('@/types').Driver> {
  return patch(`/drivers/${id}`, payload)
}
