<script setup lang="ts">
import type { AiEvidenceRef } from '@/types'

defineProps<{ refs: AiEvidenceRef[] }>()

function typeLabel(type: string): string {
  switch (type) {
    case 'TRACKING_EVENT':
      return '轨迹事件'
    case 'CARRIER_MESSAGE':
      return '承运商消息'
    case 'KNOWLEDGE_CHUNK':
      return '知识库分片'
    case 'ORDER':
      return '订单'
    case 'SLA_RULE':
      return 'SLA 规则'
    default:
      return type
  }
}

function targetId(ref: AiEvidenceRef): string | null {
  if (ref.type === 'CARRIER_MESSAGE') return 'evidence-carrier'
  if (ref.type === 'KNOWLEDGE_CHUNK') return 'evidence-knowledge'
  if (ref.type === 'TRACKING_EVENT') return 'evidence-timeline'
  return null
}

function jump(ref: AiEvidenceRef): void {
  const id = targetId(ref)
  if (!id) return
  document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
}
</script>

<template>
  <div>
    <el-empty v-if="refs.length === 0" description="本次分析没有引用来源" :image-size="50" />
    <div v-for="ref in refs" :key="`${ref.type}-${ref.id}`" class="evidence-item u-clickable" @click="jump(ref)">
      <el-tag size="small" effect="plain">{{ typeLabel(ref.type) }}</el-tag>
      <span class="u-mono">#{{ ref.id }}</span>
      <span v-if="ref.doc" class="u-text-muted">
        {{ ref.doc }}<template v-if="ref.section"> · {{ ref.section }}</template>
      </span>
      <span class="evidence-note">{{ ref.note ?? '—' }}</span>
      <el-icon v-if="targetId(ref)"><Top /></el-icon>
    </div>
  </div>
</template>

<style scoped>
.evidence-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 8px;
  border-radius: 6px;
}

.evidence-item:hover {
  background: #f5f7fa;
}

.evidence-note {
  margin-left: auto;
  color: #606266;
  font-size: 12px;
  text-align: right;
}
</style>
