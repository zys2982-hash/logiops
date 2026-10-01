<script setup lang="ts">
import { computed } from 'vue'

import PanelCard from './PanelCard.vue'
import { formatDateTime, formatDelay } from '@/utils/datetime'
import type { ExceptionDetail } from '@/types'

const props = defineProps<{ exception: ExceptionDetail }>()

const rows = computed(() => {
  const e = props.exception
  return [
    { label: '承诺到达', value: formatDateTime(e.promised_delivery_at) },
    { label: '当前 ETA', value: formatDateTime(e.current_eta_at) },
    { label: '预计到达', value: formatDateTime(e.expected_eta_at) },
  ]
})

const delayText = computed(() => formatDelay(props.exception.sla_delay_minutes))
</script>

<template>
  <PanelCard title="SLA 影响" icon="Timer">
    <el-alert
      :type="exception.sla_breached ? 'error' : exception.sla_delay_minutes && exception.sla_delay_minutes > 0 ? 'warning' : 'success'"
      :closable="false"
      show-icon
      class="u-mb-12"
    >
      <template #title>
        {{ exception.sla_breached ? `SLA 已违约 · ${delayText}` : `未违约 · ${delayText}` }}
      </template>
      <div class="u-text-muted">
        {{
          exception.sla_breached
            ? '规则口径：Asia/Shanghai，24×7；超出允许延迟即违约（§8.3）'
            : '仍在允许延迟范围内'
        }}
      </div>
    </el-alert>

    <el-descriptions :column="1" size="small" border>
      <el-descriptions-item v-for="row in rows" :key="row.label" :label="row.label">
        {{ row.value }}
      </el-descriptions-item>
      <el-descriptions-item label="延误时长">
        <el-tag :type="exception.sla_breached ? 'danger' : 'info'" size="small">{{ delayText }}</el-tag>
      </el-descriptions-item>
    </el-descriptions>
  </PanelCard>
</template>
