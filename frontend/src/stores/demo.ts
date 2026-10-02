/**
 * 演示态（基线 §12.2-6 / §13.4）：
 * 顶部横幅据此显示 "AI 分析来源：回放样本/真实大模型" 与 "业务时间：2026-09-30 19:05"。
 */
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { demoApi } from '@/api'
import { ApiError } from '@/api/request'
import { demoState as fixtureDemoState } from '@/mocks/fixtures'
import { formatDateTime } from '@/utils/datetime'
import type { DemoState } from '@/types'

/**
 * 判断这次失败是否应当走"本地演示态兜底"：
 * 只有后端不可达/未实现（网络错误、404、5xx 且未被拦截器兜底）才兜底；
 * 401/403/409 这类后端**明确回答**的错误不篡改本地时钟与状态（否则会掩盖真实问题）。
 */
function isFixtureFallback(error: unknown): boolean {
  if (error instanceof ApiError) return error.status === 0 || error.status === 404 || error.status >= 500
  return true
}

export const useDemoStore = defineStore('demo', () => {
  const state = ref<DemoState>({ ...fixtureDemoState })
  const loading = ref(false)
  const lastAction = ref<string | null>(null)
  const lastError = ref<string | null>(null)
  const mocked = ref(false)

  const aiMode = computed(() => state.value.ai_mode ?? 'replay')
  const clockMode = computed(() => state.value.clock_mode ?? 'replay')
  const isReplay = computed(() => aiMode.value === 'replay')
  const businessNowUtc = computed(() => state.value.now_utc)
  const businessTimeText = computed(() => formatDateTime(state.value.now_utc, 'YYYY-MM-DD HH:mm'))
  const baseDateText = computed(() => formatDateTime(state.value.base_date, 'YYYY-MM-DD'))
  const offsetMinutes = computed(() => state.value.offset_minutes ?? 0)

  async function refresh(): Promise<void> {
    loading.value = true
    try {
      const result = await demoApi.getDemoState()
      state.value = { ...state.value, ...result }
      mocked.value = false
      lastError.value = null
    } catch (error) {
      lastError.value = error instanceof Error ? error.message : '演示态获取失败'
      // 后端未就绪时保持本地演示态（fixture 兜底）
      mocked.value = isFixtureFallback(error)
    } finally {
      loading.value = false
    }
  }

  async function tick(minutes: number): Promise<void> {
    loading.value = true
    try {
      const result = await demoApi.demoTick(minutes)
      state.value = { ...state.value, ...{ offset_minutes: result.offset_minutes, now_utc: result.now_utc } }
      lastAction.value = `推进 ${minutes} 分钟`
      mocked.value = false
      lastError.value = null
    } catch (error) {
      if (!isFixtureFallback(error)) {
        // 后端明确拒绝（例如状态机 409）：不改本地状态，错误提示已由拦截器给出
        lastError.value = error instanceof Error ? error.message : '推进失败'
        return
      }
      // 本地兜底：直接推进 fixture 时钟
      state.value = {
        ...state.value,
        offset_minutes: (state.value.offset_minutes ?? 0) + minutes,
        now_utc: new Date(Date.parse(state.value.now_utc) + minutes * 60000).toISOString(),
      }
      lastAction.value = `推进 ${minutes} 分钟（本地演示）`
      mocked.value = true
    } finally {
      loading.value = false
    }
  }

  async function advanceToLess(): Promise<void> {
    loading.value = true
    try {
      const result = await demoApi.demoAdvanceToLess()
      state.value = { ...state.value, offset_minutes: result.offset_minutes, now_utc: result.now_utc }
      lastAction.value = '推进到订单送达'
      mocked.value = false
      lastError.value = null
    } catch (error) {
      if (!isFixtureFallback(error)) {
        lastError.value = error instanceof Error ? error.message : '推进失败'
        return
      }
      state.value = {
        ...state.value,
        offset_minutes: (state.value.offset_minutes ?? 0) + 600,
        now_utc: new Date(Date.parse(state.value.now_utc) + 600 * 60000).toISOString(),
      }
      lastAction.value = '推进到订单送达（本地演示）'
      mocked.value = true
    } finally {
      loading.value = false
    }
  }

  async function reset(scenario = 'case-a'): Promise<void> {
    loading.value = true
    try {
      await demoApi.demoReset(scenario)
      state.value = { ...fixtureDemoState, scenario }
      lastAction.value = '已重置为 seed 初始态'
      mocked.value = false
      lastError.value = null
    } catch (error) {
      if (!isFixtureFallback(error)) {
        lastError.value = error instanceof Error ? error.message : '重置失败'
        return
      }
      state.value = { ...fixtureDemoState, scenario }
      lastAction.value = '已重置为 seed 初始态（本地演示）'
      mocked.value = true
    } finally {
      loading.value = false
    }
  }

  return {
    state,
    loading,
    lastAction,
    lastError,
    mocked,
    aiMode,
    clockMode,
    isReplay,
    businessNowUtc,
    businessTimeText,
    baseDateText,
    offsetMinutes,
    refresh,
    tick,
    advanceToLess,
    reset,
  }
})
