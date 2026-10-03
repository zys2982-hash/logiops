import { get, post } from './request'
import type { DemoClockJumpResult, DemoState, DemoTickResult } from '@/types'

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

/** POST /demo/actions/set-clock 把虚拟时钟直接跳到指定时刻（年月日时分秒，秒级精确） */
export function demoSetClock(targetUtc: string): Promise<DemoClockJumpResult> {
  return post<DemoClockJumpResult>('/demo/actions/set-clock', { target_utc: targetUtc })
}

/** POST /demo/actions/set-ai-mode 运行时切换 AI 模式（replay 回放样本 / live 真实大模型） */
export interface DemoAiModeResult {
  ok: boolean
  ai_mode: string
  previous_ai_mode?: string
  runtime_only?: boolean
  warning?: string | null
}

export function demoSetAiMode(aiMode: 'replay' | 'live'): Promise<DemoAiModeResult> {
  return post<DemoAiModeResult>('/demo/actions/set-ai-mode', { ai_mode: aiMode })
}
