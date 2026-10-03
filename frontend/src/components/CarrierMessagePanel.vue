<script setup lang="ts">
import PanelCard from './PanelCard.vue'
import { formatDateTime, formatFromNow } from '@/utils/datetime'
import { messageChannelLabel, parseStatusLabel, parseStatusType } from '@/utils/format'
import type { CarrierMessage } from '@/types'

defineProps<{
  messages: CarrierMessage[]
  canWrite?: boolean
  submitting?: boolean
}>()

const emit = defineEmits<{
  (e: 'submit', payload: { raw_text: string; channel: string; sender_name: string }): void
}>()

const draft = defineModel<string>('draft', { default: '' })
const channel = defineModel<string>('channel', { default: 'MANUAL_PASTE' })
const sender = defineModel<string>('sender', { default: '' })

const channelOptions = [
  { value: 'MANUAL_PASTE', label: '人工粘贴' },
  { value: 'MOCK_WECHAT', label: '模拟微信' },
  { value: 'MOCK_SMS', label: '模拟短信' },
]

function missingList(message: CarrierMessage): string[] {
  const result = message.parse_result
  if (!result) return []
  const value = result.missing_info ?? result.missing
  return Array.isArray(value) ? value : []
}

function lowConfidence(message: CarrierMessage): boolean {
  const confidence = message.parse_result?.confidence
  return typeof confidence === 'number' && confidence < 0.6
}

function parseEntries(message: CarrierMessage): Array<{ key: string; value: unknown }> {
  const result = message.parse_result
  if (!result) return []
  return Object.entries(result).map(([key, value]) => ({ key, value }))
}

/** 后端只回写 {status:"PARSED"} 这类占位结果时给出提示（缺 T1 结构化字段） */
function structuredMissing(message: CarrierMessage): boolean {
  const result = message.parse_result
  if (!result) return false
  return !result.exception_type && !result.location
}

/** 解析结果里的时间字段按 Asia/Shanghai 展示（后端给的是 +08 偏移的 ISO 串） */
function parseValueText(key: string, value: unknown): string {
  if (typeof value === 'string' && /_at$/.test(key) && /\d{4}-\d{2}-\d{2}T/.test(value)) {
    return formatDateTime(value)
  }
  return String(value)
}

function submit(): void {
  if (!draft.value.trim()) return
  emit('submit', { raw_text: draft.value.trim(), channel: channel.value, sender_name: sender.value })
}
</script>

<template>
  <PanelCard id="evidence-carrier" title="承运商消息" :subtitle="`${messages.length} 条 · 原文与解析对照`" icon="ChatDotRound">
    <template #actions>
      <el-tag v-if="canWrite" size="small" type="warning" effect="plain">可录入</el-tag>
      <el-tag v-else size="small" effect="plain">只读</el-tag>
    </template>

    <el-empty v-if="messages.length === 0" description="暂无承运商消息" :image-size="50" />

    <div v-for="message in messages" :key="message.id" class="message-block">
      <div class="message-head">
        <b>{{ message.sender_name ?? '未知发送人' }}</b>
        <el-tag size="small" effect="plain">{{ messageChannelLabel(message.channel) }}</el-tag>
        <el-tag size="small" :type="parseStatusType(message.parse_status)" effect="plain">
          {{ parseStatusLabel(message.parse_status) }}
        </el-tag>
        <span class="u-text-muted">{{ formatFromNow(message.received_at) }}</span>
      </div>

      <div class="raw-text">{{ message.raw_text }}</div>

      <el-table
        v-if="parseEntries(message).length"
        :data="parseEntries(message)"
        size="small"
        border
        class="parse-table"
      >
        <el-table-column prop="key" label="解析字段" width="160" />
        <el-table-column label="解析结果">
          <template #default="{ row }">
            <span v-if="Array.isArray(row.value)">
              {{ row.value.length ? row.value.join('、') : '（无）' }}
            </span>
            <span v-else-if="row.value === null || row.value === undefined">—</span>
            <span v-else>{{ parseValueText(row.key, row.value) }}</span>
          </template>
        </el-table-column>
      </el-table>
      <div v-else class="u-text-muted">尚无解析结果（parse_status={{ message.parse_status }}）</div>
      <div v-if="structuredMissing(message)" class="u-text-muted">
        提示：后端当前只回写 {{ JSON.stringify(message.parse_result) }}，未持久化 §11.2 表 2 的
        `exception_type/location/status/estimated_recovery_at/confidence/missing_info` 结构化字段。
      </div>

      <el-alert
        v-if="lowConfidence(message)"
        type="warning"
        :closable="false"
        show-icon
        class="u-mt-8"
        :title="`置信度偏低（${message.parse_result?.confidence}），需人工确认`"
      />
      <div v-if="missingList(message).length" class="u-text-muted u-mt-8">
        缺失信息：{{ missingList(message).join('、') }}
      </div>
      <div v-if="message.parse_error" class="u-text-muted">解析错误：{{ message.parse_error }}</div>
      <div v-if="message.parser_version" class="u-text-muted u-mono">parser {{ message.parser_version }}</div>
    </div>

    <el-divider />

    <div v-if="canWrite" class="compose">
      <el-input
        v-model="draft"
        type="textarea"
        :rows="3"
        placeholder="粘贴承运商原文，例如：车在济南爆胎了，预计晚上 8 点恢复"
      />
      <div class="compose-row">
        <el-select v-model="channel" size="small" style="width: 130px">
          <el-option v-for="option in channelOptions" :key="option.value" :label="option.label" :value="option.value" />
        </el-select>
        <el-input v-model="sender" size="small" placeholder="发送人（可选）" style="width: 160px" />
        <el-button type="primary" size="small" :loading="submitting" @click="submit">录入消息</el-button>
        <span class="u-text-muted">提交后自动解析原文（类型 / 位置 / 恢复时间）</span>
      </div>
    </div>
    <div v-else class="u-text-muted">当前角色没有 exception.handle 权限，仅可查看原文与解析结果。</div>
  </PanelCard>
</template>

<style scoped>
.message-block {
  border: 1px solid #ebeef5;
  border-radius: 6px;
  padding: 8px;
  margin-bottom: 10px;
}

.message-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}

.raw-text {
  background: #f5f7fa;
  border-radius: 4px;
  padding: 8px;
  font-size: 13px;
  white-space: pre-wrap;
}

.parse-table {
  margin-top: 8px;
}

.compose-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 8px;
  flex-wrap: wrap;
}
</style>
