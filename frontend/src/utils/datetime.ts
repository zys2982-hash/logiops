/**
 * 时间工具（基线 §8.7 / §12.2-5）：
 * 后端所有时间都是 ISO8601 UTC（…Z），前端统一转 Asia/Shanghai 展示。
 */
import dayjs from 'dayjs'
import 'dayjs/locale/zh-cn'
import relativeTime from 'dayjs/plugin/relativeTime'
import utc from 'dayjs/plugin/utc'
import timezone from 'dayjs/plugin/timezone'

dayjs.extend(utc)
dayjs.extend(timezone)
dayjs.extend(relativeTime)
dayjs.locale('zh-cn')

/** 展示时区（固定，与后端 §8.3 日历口径一致） */
export const DISPLAY_TZ = 'Asia/Shanghai'

export const DATETIME_FORMAT = 'YYYY-MM-DD HH:mm:ss'
export const DATETIME_MIN_FORMAT = 'YYYY-MM-DD HH:mm'
export const DATE_FORMAT = 'YYYY-MM-DD'

export type DateInput = string | number | Date | null | undefined

function parse(value: DateInput): dayjs.Dayjs | null {
  if (value === null || value === undefined || value === '') return null
  // 无时区后缀的字符串按 UTC 处理（后端契约：出参一律 …Z，兜底防止本地时区偏移）
  if (typeof value === 'string') {
    const hasZone = /(?:Z|[+-]\d{2}:?\d{2})$/.test(value)
    const base = hasZone ? dayjs(value) : dayjs.utc(value)
    return base.isValid() ? base.tz(DISPLAY_TZ) : null
  }
  const base = dayjs(value)
  return base.isValid() ? base.tz(DISPLAY_TZ) : null
}

/** UTC → Asia/Shanghai，格式化为 `2026-09-30 19:05` */
export function formatDateTime(value: DateInput, format: string = DATETIME_MIN_FORMAT): string {
  const d = parse(value)
  return d ? d.format(format) : '—'
}

export function formatDateTimeFull(value: DateInput): string {
  return formatDateTime(value, DATETIME_FORMAT)
}

export function formatDate(value: DateInput): string {
  return formatDateTime(value, DATE_FORMAT)
}

/** 相对时间（中文）：3 小时前 / 2 天后 */
export function formatFromNow(value: DateInput): string {
  const d = parse(value)
  return d ? d.fromNow() : '—'
}

/** 两个时间相差的分钟数（b - a），无效时返回 null */
export function diffMinutes(a: DateInput, b: DateInput): number | null {
  const da = parse(a)
  const db = parse(b)
  if (!da || !db) return null
  return db.diff(da, 'minute')
}

/** 延误分钟 → `延误 4 小时 30 分钟` / `提前 25 分钟` / `准点` */
export function formatDelay(minutes: number | null | undefined): string {
  if (minutes === null || minutes === undefined) return '—'
  if (minutes === 0) return '准点'
  const abs = Math.abs(minutes)
  const h = Math.floor(abs / 60)
  const m = abs % 60
  const body = h > 0 ? `${h} 小时${m > 0 ? ` ${m} 分钟` : ''}` : `${m} 分钟`
  return minutes > 0 ? `延误 ${body}` : `提前 ${body}`
}

/** 当前"业务时间"：优先用 /demo/state 的 now_utc，缺省用真实时间 */
export function businessNow(stateNowUtc?: string | null): dayjs.Dayjs {
  const d = parse(stateNowUtc)
  return d ?? dayjs().tz(DISPLAY_TZ)
}

export function businessNowText(stateNowUtc?: string | null): string {
  return businessNow(stateNowUtc).format(DATETIME_MIN_FORMAT)
}

/** 给 `<el-date-picker>` 用的本地化值（Asia/Shanghai 的 ISO 字符串） */
export function toDisplayIso(value: DateInput): string | null {
  const d = parse(value)
  return d ? d.format('YYYY-MM-DDTHH:mm:ss') : null
}

/** 由展示时间构造后端可用的 UTC ISO 字符串 */
export function displayIsoToUtc(value: string | null | undefined): string | null {
  if (!value) return null
  const d = dayjs.tz(value, DISPLAY_TZ)
  return d.isValid() ? d.utc().toISOString() : null
}

/** 判断时间是否落在"今天"（业务时间口径） */
export function isToday(value: DateInput, stateNowUtc?: string | null): boolean {
  const d = parse(value)
  return d ? d.isSame(businessNow(stateNowUtc), 'day') : false
}

export { dayjs }
