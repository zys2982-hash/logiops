/**
 * AI 分析轮询（基线 §12.2-1）：
 * POST /exceptions/{id}/analyze → 每 1.5s 轮询 GET /ai-analyses/{id}
 * READY / FAILED 停止，最多 90s 超时提示。
 */
import { computed, ref, onUnmounted } from 'vue'

import { exceptionApi } from '@/api'
import { ApiError } from '@/api/request'
import type { AiAnalysis, AnalysisSummary } from '@/types'

/** 详情页内嵌的摘要与完整对象都接受 */
type AnalysisSummaryLike = AiAnalysis | AnalysisSummary

const POLL_INTERVAL_MS = 1500
const TIMEOUT_MS = 90_000

export function useAiAnalysis() {
  const analysis = ref<AiAnalysis | null>(null)
  const analysisId = ref<number | null>(null)
  const polling = ref(false)
  const timedOut = ref(false)
  const starting = ref(false)
  const errorMessage = ref<string | null>(null)
  let timer: number | null = null
  let startedAt = 0

  const status = computed(() => analysis.value?.status ?? null)
  const isRunning = computed(() => polling.value || status.value === 'RUNNING' || status.value === 'PENDING')
  const isReady = computed(() => status.value === 'READY')
  const isFailed = computed(() => status.value === 'FAILED')

  function stop(): void {
    polling.value = false
    if (timer !== null) {
      window.clearTimeout(timer)
      timer = null
    }
  }

  function scheduleNext(): void {
    timer = window.setTimeout(() => {
      void poll()
    }, POLL_INTERVAL_MS)
  }

  async function poll(): Promise<void> {
    if (!analysisId.value) return
    if (Date.now() - startedAt > TIMEOUT_MS) {
      stop()
      timedOut.value = true
      return
    }
    try {
      const result = await exceptionApi.getAiAnalysis(analysisId.value)
      analysis.value = result
      if (result.status === 'READY' || result.status === 'FAILED') {
        stop()
        return
      }
      if (polling.value) scheduleNext()
    } catch (error) {
      // 单次轮询失败不终止：继续轮询直到超时（后端可能正在重启）
      errorMessage.value = error instanceof Error ? error.message : '轮询失败'
      if (polling.value) scheduleNext()
    }
  }

  /** 返回 analysis_id（供审批单卡片拉取） */
  async function start(exceptionId: number, expectedVersion?: number): Promise<number | null> {
    starting.value = true
    timedOut.value = false
    errorMessage.value = null
    try {
      const result = await exceptionApi.analyzeException(exceptionId, { expected_version: expectedVersion })
      analysisId.value = result.analysis_id
      analysis.value = {
        id: result.analysis_id,
        exception_id: exceptionId,
        task_type: 'ANALYZE_EXCEPTION',
        status: result.status ?? 'PENDING',
        steps: [],
      }
      startedAt = Date.now()
      polling.value = true
      await poll()
      if (polling.value) scheduleNext()
      return result.analysis_id
    } catch (error) {
      if (error instanceof ApiError && error.code === 'AI_ANALYSIS_IN_PROGRESS') {
        const existing = (error.details as { analysis_id?: number }).analysis_id
        if (existing) {
          analysisId.value = existing
          startedAt = Date.now()
          polling.value = true
          await poll()
          if (polling.value) scheduleNext()
          return existing
        }
      }
      errorMessage.value = error instanceof Error ? error.message : '触发 AI 分析失败'
      return null
    } finally {
      starting.value = false
    }
  }

  async function retry(): Promise<void> {
    if (!analysisId.value) return
    timedOut.value = false
    try {
      analysis.value = await exceptionApi.retryAiAnalysis(analysisId.value)
      startedAt = Date.now()
      polling.value = true
      await poll()
      if (polling.value) scheduleNext()
    } catch (error) {
      errorMessage.value = error instanceof Error ? error.message : '重试失败'
    }
  }

  /** 直接载入已有分析（详情页返回 latest_analysis 时） */
  /**
   * 载入已有分析。异常详情里的 latest_analysis 只是**摘要**（没有 output/steps），
   * 这种情况会再拉一次 GET /ai-analyses/{id} 拿完整结果；RUNNING/PENDING 则启动轮询。
   */
  function loadExisting(value: AnalysisSummaryLike | null | undefined): void {
    if (!value) return
    analysisId.value = value.id
    analysis.value = value as AiAnalysis
    const needsFull = !('output' in value) || !('steps' in value)
    if (value.status === 'RUNNING' || value.status === 'PENDING') {
      startedAt = Date.now()
      polling.value = true
      scheduleNext()
      return
    }
    if (needsFull && value.status === 'READY') {
      void exceptionApi
        .getAiAnalysis(value.id)
        .then((full) => {
          analysis.value = full
        })
        .catch(() => {
          /* 摘要仍可展示，失败不阻塞页面 */
        })
    }
  }

  onUnmounted(() => stop())

  return {
    analysis,
    analysisId,
    polling,
    starting,
    timedOut,
    errorMessage,
    status,
    isRunning,
    isReady,
    isFailed,
    start,
    retry,
    poll,
    stop,
    loadExisting,
    POLL_INTERVAL_MS,
    TIMEOUT_MS,
  }
}
