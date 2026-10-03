<script setup lang="ts">
import { computed } from 'vue'

import EvidenceList from './EvidenceList.vue'
import PanelCard from './PanelCard.vue'
import { useAiAnalysis } from '@/composables/useAiAnalysis'
import { formatDateTime, formatDelay } from '@/utils/datetime'
import { analysisStatusLabel, analysisStatusType, formatDuration, toolNameLabel } from '@/utils/format'
import type { AiAnalysis, AiAnalysisStep, AnalysisSummary, ExceptionDetail } from '@/types'

const props = defineProps<{
  exception: ExceptionDetail
  canAnalyze?: boolean
  /** 不可分析时的原因（由详情页按状态给出：未确认 / 处理中 / 已关闭 …） */
  blockReason?: string
}>()

const emit = defineEmits<{ (e: 'started', analysisId: number): void }>()

const {
  analysis,
  analysisId,
  status,
  starting,
  timedOut,
  isRunning,
  isFailed,
  start,
  retry,
  loadExisting: loadExistingFn,
} = useAiAnalysis()

/** 后端有界循环上限 8 步（§11.3），步骤条数按实际返回显示 */
const STEP_LIMIT = 8

const steps = computed<AiAnalysisStep[]>(() => analysis.value?.steps ?? [])
const output = computed(() => analysis.value?.output ?? null)

const stepState = (index: number): 'ok' | 'running' | 'pending' | 'error' => {
  const step = steps.value[index]
  if (!step) return 'pending'
  if (step.status === 'OK') return 'ok'
  if (step.status === 'ERROR') return 'error'
  if (step.status === 'RUNNING') return 'running'
  return 'pending'
}

function stepIcon(index: number): string {
  switch (stepState(index)) {
    case 'ok':
      return 'CircleCheckFilled'
    case 'running':
      return 'Loading'
    case 'error':
      return 'CircleCloseFilled'
    default:
      return 'Clock'
  }
}

function stepColor(index: number): string {
  switch (stepState(index)) {
    case 'ok':
      return '#67c23a'
    case 'running':
      return '#e6a23c'
    case 'error':
      return '#f56c6c'
    default:
      return '#909399'
  }
}

function stepTitle(step: AiAnalysisStep): string {
  if (step.step_type === 'VALIDATE') return 'schema 校验'
  if (step.step_type === 'LLM') return 'LLM 输出'
  return toolNameLabel(step.tool_name)
}

const failedHint = computed(() => {
  if (!isFailed.value) return null
  return analysis.value?.error_message ?? 'AI 暂不可用，可重试或手工处理'
})

async function handleAnalyze(): Promise<void> {
  const id = await start(props.exception.id, props.exception.version)
  if (id) emit('started', id)
}

async function handleRetry(): Promise<void> {
  await retry()
  if (analysisId.value) emit('started', analysisId.value)
}

/** 详情页拿到 latest_analysis（可能是摘要或完整对象）时回填；仅在未轮询时调用 */
function loadExisting(value: AnalysisSummary | AiAnalysis | null | undefined): void {
  if (!value) return
  loadExistingFn(value)
}

function isPolling(): boolean {
  return isRunning.value
}

defineExpose({ loadExisting, isPolling, analysisId })
</script>

<template>
  <PanelCard title="AI 分析" :subtitle="`异常 #${exception.id}`" icon="MagicStick">
    <template #actions>
      <el-tag v-if="status" size="small" :type="analysisStatusType(status)" effect="plain">
        {{ analysisStatusLabel(status) }}
      </el-tag>
      <el-button
        v-if="canAnalyze && !isRunning"
        size="small"
        type="primary"
        :loading="starting"
        @click="handleAnalyze"
      >
        {{ status ? '重新分析' : 'AI 分析此异常' }}
      </el-button>
      <el-button v-if="isRunning" size="small" plain loading>分析中…</el-button>
    </template>

    <el-alert
      v-if="timedOut"
      type="warning"
      :closable="false"
      show-icon
      class="u-mb-12"
      title="轮询超过 90 秒仍未完成，已停止等待"
      description="可稍后重试，或直接人工处理（异常仍可由人工 resolve/close）"
    />

    <el-alert v-if="failedHint" type="error" :closable="false" show-icon class="u-mb-12" :title="failedHint">
      <div class="u-text-muted">
        降级路径：写 ai_analysis.status=FAILED，异常状态回退 CONFIRMING，人工可继续处理。
      </div>
      <el-button size="small" class="u-mt-8" @click="handleRetry">重试</el-button>
    </el-alert>

    <div v-if="!status" class="analyze-hint">
      <el-empty description="尚未触发 AI 分析" :image-size="60">
        <el-button v-if="canAnalyze" type="primary" :loading="starting" @click="handleAnalyze">
          {{ props.exception.status === 'PROCESSING' ? '重新分析此异常' : 'AI 分析此异常' }}
        </el-button>
        <span v-else class="u-text-muted">{{ blockReason || '当前状态不可发起 AI 分析' }}</span>
      </el-empty>
      <div class="u-text-muted">
        契约：POST /exceptions/{id}/analyze → 202 {analysis_id}，随后每 1.5s 轮询 GET /ai-analyses/{id}
      </div>
    </div>

    <template v-else>
      <div class="progress-title">
        <span>工具调用步骤（{{ steps.length }}/{{ STEP_LIMIT }}）</span>
        <span v-if="analysis?.is_replay" class="u-text-muted">来源：预录样本（replay fixture）</span>
      </div>
      <div
        v-for="(step, index) in steps"
        :key="step.step_no"
        class="ai-step"
        :class="`ai-step--${stepState(index)}`"
      >
        <div class="ai-step-head">
          <el-icon :color="stepColor(index)" :class="{ 'is-loading': stepState(index) === 'running' }">
            <component :is="stepIcon(index)" />
          </el-icon>
          <b>{{ step.step_no }}.</b>
          <span>{{ stepTitle(step) }}</span>
          <span class="u-text-muted">{{ formatDuration(step.duration_ms) }}</span>
        </div>
        <div v-if="step.result_summary" class="u-text-muted">{{ step.result_summary }}</div>
        <div v-if="step.error" class="u-text-muted log-level-CRITICAL">{{ step.error }}</div>
      </div>
      <div v-if="steps.length === 0" class="u-text-muted">等待后端返回步骤…</div>

      <el-divider />

      <template v-if="output">
        <h4 class="block-title">结论摘要</h4>
        <p class="summary-text">{{ output.summary }}</p>

        <h4 class="block-title">根因</h4>
        <div>
          <el-tag size="small" type="warning" effect="plain">{{ output.root_cause.code }}</el-tag>
          <span>{{ output.root_cause.note }}</span>
        </div>

        <h4 class="block-title">影响</h4>
        <el-descriptions :column="1" size="small" border>
          <el-descriptions-item label="延误">{{ formatDelay(output.impact.delay_minutes) }}</el-descriptions-item>
          <el-descriptions-item label="是否违约">
            <el-tag size="small" :type="output.impact.sla_breached ? 'danger' : 'success'">
              {{ output.impact.sla_breached ? '已违约' : '未违约' }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="客户等级">
            {{ output.impact.affected_customer_level ?? '—' }}
          </el-descriptions-item>
          <el-descriptions-item label="规则等级（AI 无权改）">
            {{ analysis?.risk_level_calculated ?? '—' }}
          </el-descriptions-item>
        </el-descriptions>

        <h4 class="block-title">建议 → 审批单</h4>
        <ol class="suggestion-list">
          <li v-for="suggestion in output.suggestions" :key="suggestion.code">
            <b>{{ suggestion.title }}</b>
            <el-tag size="small" effect="plain">{{ suggestion.code }}</el-tag>
            <div v-if="suggestion.rationale" class="u-text-muted">依据：{{ suggestion.rationale }}</div>
          </li>
        </ol>

        <h4 class="block-title">待确认事项</h4>
        <div v-if="output.open_questions.length === 0" class="u-text-muted">无</div>
        <ul v-else class="question-list">
          <li v-for="(question, index) in output.open_questions" :key="index">{{ question }}</li>
        </ul>

        <h4 class="block-title">证据来源（点击可跳原文）</h4>
        <EvidenceList :refs="output.evidence_refs" :order-id="exception.order_id" :exception-id="exception.id" />

        <div class="u-text-muted u-mt-12 u-mono">
          model={{ analysis?.model ?? '—' }} · prompt={{ analysis?.prompt_version ?? '—' }} · tokens={{
            analysis?.tokens_in ?? 0
          }}/{{ analysis?.tokens_out ?? 0 }} · latency={{ formatDuration(analysis?.latency_ms) }} · finished={{
            formatDateTime(analysis?.finished_at)
          }}
        </div>
      </template>
      <div v-else-if="isRunning" class="u-text-muted u-mt-12">分析进行中，结论将在 READY 后展示…</div>
    </template>
  </PanelCard>
</template>

<style scoped>
.analyze-hint {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
}

.progress-title {
  display: flex;
  justify-content: space-between;
  font-weight: 600;
  margin-bottom: 8px;
}

.ai-step-head {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
}

.block-title {
  margin: 14px 0 6px;
  font-size: 13px;
  color: #303133;
}

.summary-text {
  margin: 0;
  line-height: 1.7;
  font-size: 13px;
  background: #f5f7fa;
  padding: 8px;
  border-radius: 4px;
}

.suggestion-list,
.question-list {
  margin: 0;
  padding-left: 18px;
  font-size: 13px;
}

.suggestion-list li {
  margin-bottom: 8px;
}
</style>
