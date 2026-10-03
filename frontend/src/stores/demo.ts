/**
 * 演示态（基线 §12.2-6 / §13.4）：
 * 顶部横幅据此显示 "AI 分析来源：回放样本/真实大模型" 与 "业务时间：2026-09-30 19:05"。
 */
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { demoApi } from '@/api'
import { ApiError } from '@/api/request'
import type { DemoStatusResult } from '@/api/demo'
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
  const lastWarning = ref<string | null>(null)
  const mocked = ref(false)

  const aiMode = computed(() => state.value.ai_mode ?? 'replay')
  const clockMode = computed(() => state.value.clock_mode ?? 'replay')
  const isReplay = computed(() => aiMode.value === 'replay')
  /** AI 总开关（默认停用）：停用时面板显示「已停用」并隐藏分析按钮 */
  const aiEnabled = computed(
    () => (state.value as unknown as { ai_enabled?: boolean }).ai_enabled !== false,
  )
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

  async function setClock(targetUtc: string): Promise<void> {
    loading.value = true
    try {
      const result = await demoApi.demoSetClock(targetUtc)
      state.value = {
        ...state.value,
        base_date: result.base_date,
        offset_minutes: result.offset_minutes,
        now_utc: result.now_utc,
      }
      lastAction.value = `把虚拟时钟跳到 ${formatDateTime(result.now_utc, 'YYYY-MM-DD HH:mm:ss')}`
      mocked.value = false
      lastError.value = null
    } catch (error) {
      if (!isFixtureFallback(error)) {
        // 后端明确拒绝（时间非法/越界）：不改本地状态
        lastError.value = error instanceof Error ? error.message : '时间跳转失败'
        return
      }
      // 本地兜底：直接跳到目标时刻
      state.value = { ...state.value, base_date: targetUtc, offset_minutes: 0, now_utc: targetUtc }
      lastAction.value = `把虚拟时钟跳到 ${formatDateTime(targetUtc, 'YYYY-MM-DD HH:mm:ss')}（本地演示）`
      mocked.value = true
    } finally {
      loading.value = false
    }
  }

  /** 运行时切换 AI 模式（顶部横幅开关）：立即生效，后端重启后回到 .env 的 AI_MODE */
  async function setAiMode(mode: 'replay' | 'live'): Promise<void> {
    loading.value = true
    try {
      const result = await demoApi.demoSetAiMode(mode)
      state.value = { ...state.value, ai_mode: result.ai_mode }
      lastAction.value = mode === 'live' ? 'AI 切到真实大模型（live）' : 'AI 切到回放样本（replay）'
      lastWarning.value = result.warning ?? null
      lastError.value = null
      mocked.value = false
    } catch (error) {
      if (!isFixtureFallback(error)) {
        lastError.value = error instanceof Error ? error.message : 'AI 模式切换失败'
        return
      }
      state.value = { ...state.value, ai_mode: mode }
      lastAction.value = `AI 模式切换为 ${mode}（本地演示）`
      lastWarning.value = null
      mocked.value = true
    } finally {
      loading.value = false
    }
  }

  /** 运行时切换时钟模式（真实时间 / 虚拟时钟）：立即生效，后端重启后回到 .env 的 CLOCK_MODE */
  async function setClockMode(mode: 'system' | 'replay'): Promise<void> {
    loading.value = true
    try {
      const result = await demoApi.demoSetClockMode(mode)
      state.value = {
        ...state.value,
        clock_mode: result.clock_mode,
        now_utc: result.now_utc,
        base_date: result.base_date,
        offset_minutes: result.offset_minutes,
      }
      lastAction.value =
        mode === 'replay' ? '时钟切到虚拟时钟（可快进 / 跳转）' : '时钟切到真实时间'
      lastWarning.value = result.warning ?? null
      lastError.value = null
      mocked.value = false
    } catch (error) {
      if (!isFixtureFallback(error)) {
        lastError.value = error instanceof Error ? error.message : '时钟模式切换失败'
        return
      }
      state.value = { ...state.value, clock_mode: mode }
      lastAction.value = `时钟模式切为 ${mode}（本地演示）`
      lastWarning.value = null
      mocked.value = true
    } finally {
      loading.value = false
    }
  }

  /** 演示：直接设定订单状态（跳过状态机；后端写 order.status_forced 审计） */
  async function setOrderStatus(
    orderId: number,
    status: string,
    note?: string,
  ): Promise<DemoStatusResult | null> {
    loading.value = true
    try {
      const result = await demoApi.demoSetOrderStatus(orderId, status, note)
      lastAction.value = `订单 ${result.order_no ?? orderId}：${result.previous_status} → ${result.status}`
      lastError.value = null
      lastWarning.value = null
      mocked.value = false
      return result
    } catch (error) {
      lastError.value = error instanceof Error ? error.message : '订单状态直设失败'
      return null
    } finally {
      loading.value = false
    }
  }

  /** 演示：直接设定异常状态（跳过状态机；后端写事件 + exception.status_forced 审计） */
  async function setExceptionStatus(
    exceptionId: number,
    status: string,
    note?: string,
  ): Promise<DemoStatusResult | null> {
    loading.value = true
    try {
      const result = await demoApi.demoSetExceptionStatus(exceptionId, status, note)
      lastAction.value = `异常 ${result.case_no ?? exceptionId}：${result.previous_status} → ${result.status}`
      lastError.value = null
      lastWarning.value = null
      mocked.value = false
      return result
    } catch (error) {
      lastError.value = error instanceof Error ? error.message : '异常状态直设失败'
      return null
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
    lastWarning,
    mocked,
    aiMode,
    aiEnabled,
    clockMode,
    isReplay,
    businessNowUtc,
    businessTimeText,
    baseDateText,
    offsetMinutes,
    refresh,
    tick,
    advanceToLess,
    setClock,
    setAiMode,
    setClockMode,
    setOrderStatus,
    setExceptionStatus,
    reset,
  }
})
