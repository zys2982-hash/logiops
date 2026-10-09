<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import {
  approvalActionLabel,
  approvalStatusLabel,
  approvalStatusType,
  formatNumber,
} from '@/utils/format'
import { displayIsoToUtc, formatDateTime, toDisplayIso } from '@/utils/datetime'
import type { Approval } from '@/types'

const props = defineProps<{
  approval: Approval
  canDecide?: boolean
}>()

const emit = defineEmits<{
  (e: 'decided', approvalId: number): void
  (e: 'approve', payload: { id: number; expected_version: number; final_payload: Record<string, unknown> }): void
  (e: 'reject', payload: { id: number; expected_version: number; reason: string }): void
  (e: 'execute', id: number): void
}>()

const editing = ref(false)
const submitting = ref(false)

/** 已知字段的中文标签（其余字段按 key 原样展示） */
const FIELD_LABELS: Record<string, string> = {
  eta_at: '新的预计到达时间',
  reason: '变更原因',
  title: '任务标题',
  content: '正文',
  subject: '通知标题',
  due_at: '截止时间',
  assignee_role: '建议处理角色',
  priority: '优先级',
  notification_id: '通知单号',
}

/** 枚举型 payload 值的中文说法（给人看的表格里不出现 OPERATOR / NORMAL 这类英文常量） */
const PAYLOAD_VALUE_LABELS: Record<string, Record<string, string>> = {
  assignee_role: { OPERATOR: '运营人员', ADMIN: '管理员', VIEWER: '只读用户' },
  priority: { LOW: '低', NORMAL: '普通', HIGH: '高', URGENT: '紧急' },
}

const DATE_FIELDS = new Set(['eta_at', 'due_at'])

const draft = ref<Record<string, unknown>>({})

watch(
  () => props.approval.id,
  () => {
    draft.value = editablePayload()
    editing.value = false
  },
  { immediate: true },
)

function editablePayload(): Record<string, unknown> {
  const base = props.approval.final_payload ?? props.approval.ai_payload ?? {}
  return JSON.parse(JSON.stringify(base)) as Record<string, unknown>
}

const aiPayload = computed<Record<string, unknown>>(() => props.approval.ai_payload ?? {})
const finalPayload = computed<Record<string, unknown> | null>(() => props.approval.final_payload ?? null)

/** 后端 ai_payload 里没有独立的 rationale 字段，这里按建议类型取最有信息量的一项作为"AI 依据" */
const aiRationale = computed<string | null>(() => {
  const payload = aiPayload.value
  const candidate = payload.reason ?? payload.rationale ?? payload.content ?? payload.title
  return typeof candidate === 'string' && candidate.trim() ? candidate : null
})

const fields = computed(() => {
  const keys = new Set([...Object.keys(aiPayload.value), ...Object.keys(draft.value)])
  return [...keys]
})

/** 后端 diff 形状：{changed:[], fields:{field:{ai,final}}, ai_value, final_value, ai_payload, final_payload} */
const diffChanged = computed<string[]>(() => {
  const explicit = props.approval.diff
  if (explicit?.changed && explicit.changed.length > 0) return explicit.changed
  if (explicit?.fields && Object.keys(explicit.fields).length > 0) return Object.keys(explicit.fields)
  const changed: string[] = []
  for (const key of fields.value) {
    const ai = JSON.stringify(aiPayload.value[key] ?? null)
    const human = JSON.stringify((props.approval.final_payload ?? {})[key] ?? null)
    if (ai !== human) changed.push(key)
  }
  return changed
})

/** 已决策单据的展示口径：优先用 diff 里记录的 ai/final，其次 final_payload，最后 ai_payload */
function aiValueOf(key: string): unknown {
  const explicit = props.approval.diff?.fields?.[key]
  if (explicit) return explicit.ai
  return aiPayload.value[key]
}

function finalValueOf(key: string): unknown {
  const explicit = props.approval.diff?.fields?.[key]
  if (explicit) return explicit.final
  if (finalPayload.value) return finalPayload.value[key]
  return aiPayload.value[key]
}

function isChanged(key: string): boolean {
  return diffChanged.value.includes(key)
}

/** 长文本字段（变更原因、通知正文等）需要多行输入与限高展示，单行输入框没法编辑 */
const LONG_TEXT_KEYS = new Set(['reason', 'rationale', 'content', 'subject', 'note', 'remark', 'title'])

function isLongText(key: string, value: unknown): boolean {
  if (LONG_TEXT_KEYS.has(key)) return true
  return typeof value === 'string' && value.length > 40
}

function displayValue(key: string, value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  if (DATE_FIELDS.has(key) && typeof value === 'string') return formatDateTime(value)
  if (typeof value === 'object') return JSON.stringify(value)
  const mapped = PAYLOAD_VALUE_LABELS[key]?.[String(value)]
  return mapped ?? String(value)
}

function useFieldDate(key: string): boolean {
  return DATE_FIELDS.has(key)
}

function startEdit(): void {
  draft.value = editablePayload()
  editing.value = true
}

function resetEdit(): void {
  draft.value = editablePayload()
  editing.value = false
}

/** 把 datetime 选择器的本地值写回 UTC ISO */
function onDateChange(key: string, value: string | null): void {
  draft.value[key] = displayIsoToUtc(value)
}

function payloadToSend(): Record<string, unknown> {
  // 仅提交与 AI 原值不同的字段 + 时间字段的 UTC 化
  const result: Record<string, unknown> = {}
  for (const key of Object.keys(draft.value)) {
    const value = draft.value[key]
    if (value === undefined) continue
    result[key] = value
  }
  return result
}

async function approve(): Promise<void> {
  try {
    await ElMessageBox.confirm(
      `确认批准审批单 #${props.approval.id}（${approvalActionLabel(props.approval.action_type)}）？批准后后端将立即执行业务写操作。`,
      '二次确认',
      { type: 'warning', confirmButtonText: '批准并执行' },
    )
  } catch {
    return
  }
  submitting.value = true
  try {
    emit('approve', {
      id: props.approval.id,
      expected_version: props.approval.version,
      final_payload: payloadToSend(),
    })
  } finally {
    submitting.value = false
  }
}

async function reject(): Promise<void> {
  let reason = ''
  try {
    const result = await ElMessageBox.prompt('驳回必须填写原因（REJECTED 不产生任何业务副作用）', '驳回审批单', {
      inputPlaceholder: '例如：承运商恢复时间未确认，先别改 ETA',
      inputValidator: (value) => (value && value.trim().length > 0 ? true : '请填写驳回原因'),
    })
    reason = result.value ?? ''
  } catch {
    return
  }
  emit('reject', { id: props.approval.id, expected_version: props.approval.version, reason })
}

function retryExecute(): void {
  ElMessage.info(`重新执行审批单 #${props.approval.id}`)
  emit('execute', props.approval.id)
}
</script>

<template>
  <el-card shadow="never" class="page-card approval-card">
    <div class="approval-head">
      <div class="approval-title">
        <el-tag size="small" type="primary" effect="dark">{{ approvalActionLabel(approval.action_type) }}</el-tag>
        <b>#{{ approval.id }}</b>
        <el-tag size="small" :type="approvalStatusType(approval.status)" effect="plain">
          {{ approvalStatusLabel(approval.status) }}
        </el-tag>
        <span class="u-text-muted u-mono">v{{ approval.version }} · {{ approval.action_type }}</span>
      </div>
      <div class="approval-actions">
        <template v-if="canDecide && approval.status === 'PENDING'">
          <el-button size="small" @click="editing ? resetEdit() : startEdit()">
            {{ editing ? '取消编辑' : '编辑' }}
          </el-button>
          <el-button size="small" type="primary" :loading="submitting" @click="approve">批准</el-button>
          <el-button size="small" type="danger" plain @click="reject">驳回</el-button>
        </template>
        <el-button v-else-if="approval.status === 'FAILED'" size="small" type="warning" @click="retryExecute">
          重新执行
        </el-button>
        <el-tag v-else-if="!canDecide" size="small" effect="plain">只读</el-tag>
      </div>
    </div>

    <div v-if="aiRationale" class="u-text-muted u-mb-8">AI 依据：{{ aiRationale }}</div>

    <el-table :data="fields.map((key) => ({ key }))" size="small" border class="diff-table">
      <el-table-column label="字段" width="120">
        <template #default="{ row }">{{ FIELD_LABELS[row.key] ?? row.key }}</template>
      </el-table-column>
      <el-table-column label="AI 原始值（ai_payload）" min-width="220">
        <template #default="{ row }">
          <div
            v-if="isLongText(row.key, aiValueOf(row.key))"
            class="diff-longtext"
            :class="{ 'diff-ai': isChanged(row.key) }"
          >
            {{ displayValue(row.key, aiValueOf(row.key)) }}
          </div>
          <span v-else :class="{ 'diff-ai': isChanged(row.key) }">{{ displayValue(row.key, aiValueOf(row.key)) }}</span>
        </template>
      </el-table-column>
      <el-table-column label="人工值（final_payload）" min-width="240">
        <template #default="{ row }">
          <template v-if="editing">
            <el-date-picker
              v-if="useFieldDate(row.key)"
              :model-value="toDisplayIso(draft[row.key] as string)"
              type="datetime"
              size="small"
              style="width: 190px"
              @update:model-value="(value: string | null) => onDateChange(row.key, value)"
            />
            <!-- 长文本（变更原因等）用多行文本域，单行输入框没法编辑 -->
            <el-input
              v-else-if="isLongText(row.key, draft[row.key])"
              v-model="draft[row.key] as string"
              type="textarea"
              :autosize="{ minRows: 3, maxRows: 12 }"
              size="small"
            />
            <el-input v-else v-model="draft[row.key] as string" size="small" />
          </template>
          <template v-else>
            <div
              v-if="isLongText(row.key, finalValueOf(row.key))"
              class="diff-longtext"
              :class="{ 'diff-final': isChanged(row.key) }"
            >
              {{ displayValue(row.key, finalValueOf(row.key)) }}
            </div>
            <span v-else :class="{ 'diff-final': isChanged(row.key) }">
              {{ displayValue(row.key, finalValueOf(row.key)) }}
            </span>
          </template>
        </template>
      </el-table-column>
    </el-table>

    <div class="approval-foot u-text-muted">
      <span v-if="diffChanged.length">
        人工修改了：{{ diffChanged.map((key) => FIELD_LABELS[key] ?? key).join('、') }}
      </span>
      <span v-else>人工未修改 AI 建议</span>
      <span v-if="approval.executed_at">· 执行于 {{ formatDateTime(approval.executed_at) }}</span>
      <span v-if="approval.reject_reason">· 驳回原因：{{ approval.reject_reason }}</span>
      <span v-if="approval.error_message" class="log-level-CRITICAL">· 错误：{{ approval.error_message }}</span>
      <span v-if="approval.execution_result">
        · 执行结果 {{ JSON.stringify(approval.execution_result) }}
      </span>
      <span v-if="approval.retry_count">· 重试 {{ formatNumber(approval.retry_count) }} 次</span>
    </div>
  </el-card>
</template>

<style scoped>
.approval-card {
  margin-bottom: 12px;
}

.approval-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 8px;
  flex-wrap: wrap;
}

.approval-title {
  display: flex;
  align-items: center;
  gap: 6px;
}

.approval-actions {
  display: flex;
  align-items: center;
  gap: 6px;
}

.diff-table {
  margin-bottom: 6px;
}

/* 长文本：保留换行、限高可滚动，避免把卡片撑成"一条竖线" */
.diff-longtext {
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 160px;
  overflow: auto;
  line-height: 1.6;
}

.approval-foot {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
</style>
