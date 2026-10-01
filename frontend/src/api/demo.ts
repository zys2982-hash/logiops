import { get, post } from './request'
import type { DemoState, DemoTickResult } from '@/types'

/** GET /demo/state → {base_date, offset_minutes, now_utc, clock_mode, ai_mode, ...} */
export function getDemoState(): Promise<DemoState> {
  return get<DemoState>('/demo/state')
}

/** POST /demo/actions/tick {minutes} 推进时钟（触发 ETA 重算/检测/自动关闭） */
export function demoTick(minutes: number): Promise<DemoTickResult> {
  return post<DemoTickResult>('/demo/actions/tick', { minutes })
}

/** POST /demo/actions/advance-to-less 一步推到“送达并自动关闭” */
export function demoAdvanceToLess(): Promise<DemoTickResult> {
  return post<DemoTickResult>('/demo/actions/advance-to-less')
}

/** POST /demo/actions/reset 重建 seed 并重置时钟 */
export function demoReset(scenario = 'case-a'): Promise<{ reset: boolean; scenario: string; summary?: unknown }> {
  return post('/demo/actions/reset', { scenario })
}
