<script setup lang="ts">
import { computed } from 'vue'

import PanelCard from './PanelCard.vue'
import { formatDateTime } from '@/utils/datetime'
import { trackingEventLabel, trackingSourceLabel } from '@/utils/format'
import type { TrackingEvent } from '@/types'

const props = withDefaults(
  defineProps<{
    events: TrackingEvent[]
    /** 异常发生时间，用于给"异常窗口"内的事件加标记 */
    exceptionAt?: string | null
    loading?: boolean
  }>(),
  { exceptionAt: null, loading: false },
)

const sorted = computed(() =>
  [...props.events].sort(
    (a, b) => Date.parse(b.occurred_at) - Date.parse(a.occurred_at),
  ),
)

const anomalyTypes = new Set(['STOP', 'REPAIR_START'])

function isAnomaly(event: TrackingEvent): boolean {
  if (!props.exceptionAt) return anomalyTypes.has(event.event_type)
  return Date.parse(event.occurred_at) >= Date.parse(props.exceptionAt) - 30 * 60 * 1000
}

function dotColor(event: TrackingEvent): string {
  if (event.event_type === 'REPAIR_START') return '#f56c6c'
  if (event.event_type === 'DELIVER') return '#67c23a'
  if (isAnomaly(event)) return '#e6a23c'
  return '#909399'
}
</script>

<template>
  <PanelCard title="运输轨迹时间线" :subtitle="`共 ${events.length} 条`" icon="Location">
    <el-empty v-if="!loading && sorted.length === 0" description="暂无轨迹事件" :image-size="60" />
    <el-skeleton v-else-if="loading" :rows="5" animated />
    <el-timeline v-else>
      <el-timeline-item
        v-for="event in sorted"
        :key="event.id"
        :timestamp="formatDateTime(event.occurred_at)"
        :color="dotColor(event)"
        placement="top"
      >
        <div class="event-title">
          <b>{{ trackingEventLabel(event.event_type) }}</b>
          <span>{{ event.city ?? '—' }}</span>
          <el-tag v-if="isAnomaly(event)" size="small" type="danger" effect="plain">异常标记</el-tag>
        </div>
        <div class="u-text-muted">
          {{ event.address ?? '—' }} · 来源 {{ trackingSourceLabel(event.source) }}
          <template v-if="event.speed_kmh !== null && event.speed_kmh !== undefined">
            · 速度 {{ event.speed_kmh }} km/h
          </template>
        </div>
        <div v-if="event.payload_json" class="u-text-muted u-mono">
          {{ JSON.stringify(event.payload_json) }}
        </div>
      </el-timeline-item>
    </el-timeline>
  </PanelCard>
</template>

<style scoped>
.event-title {
  display: flex;
  align-items: center;
  gap: 8px;
}
</style>
