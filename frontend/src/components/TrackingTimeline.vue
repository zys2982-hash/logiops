<script setup lang="ts">
/**
 * 运输轨迹时间线 = 轨迹事件 + 在途异常，按时间倒序混排。
 *
 * 配色规则（业务口径）：
 * - 轨迹：送达=绿、开始维修=红、停滞窗口内=橙、其余=灰
 * - 异常开始：**未结束=红点**、已结束=灰点
 * - 异常结束：灰点（只有已结束的异常才有这一条）
 */
import { computed } from 'vue'
import { useRouter } from 'vue-router'

import PanelCard from './PanelCard.vue'
import { formatDateTime } from '@/utils/datetime'
import {
  exceptionStatusLabel,
  exceptionTypeLabel,
  trackingEventLabel,
  trackingSourceLabel,
} from '@/utils/format'
import type { TimelineIncident, TrackingEvent } from '@/types'

/** 时间线上的异常条目定义在 `@/types`（TimelineIncident），此处只消费 */

const props = withDefaults(
  defineProps<{
    events: TrackingEvent[]
    /** 异常发生时间，用于给"异常窗口"内的事件加标记 */
    exceptionAt?: string | null
    incidents?: TimelineIncident[]
    loading?: boolean
  }>(),
  { exceptionAt: null, incidents: () => [], loading: false },
)

const router = useRouter()

type Entry =
  | { kind: 'tracking'; at: string; event: TrackingEvent }
  | { kind: 'incident-start'; at: string; incident: TimelineIncident }
  | { kind: 'incident-end'; at: string; incident: TimelineIncident }

const OPEN_STATUSES = new Set(['DETECTED', 'CONFIRMING', 'ANALYZING', 'PROCESSING'])

const entries = computed<Entry[]>(() => {
  const list: Entry[] = props.events.map((event) => ({ kind: 'tracking', at: event.occurred_at, event }))
  for (const incident of props.incidents) {
    list.push({ kind: 'incident-start', at: incident.startedAt, incident })
    if (incident.endedAt) list.push({ kind: 'incident-end', at: incident.endedAt, incident })
  }
  return list.sort((a, b) => Date.parse(b.at) - Date.parse(a.at))
})

const anomalyTypes = new Set(['STOP', 'REPAIR_START'])

function isAnomaly(event: TrackingEvent): boolean {
  if (!props.exceptionAt) return anomalyTypes.has(event.event_type)
  return Date.parse(event.occurred_at) >= Date.parse(props.exceptionAt) - 30 * 60 * 1000
}

function isOpenIncident(incident: TimelineIncident): boolean {
  return OPEN_STATUSES.has(String(incident.status ?? ''))
}

function dotColor(entry: Entry): string {
  if (entry.kind === 'incident-start') return isOpenIncident(entry.incident) ? '#f56c6c' : '#909399'
  if (entry.kind === 'incident-end') return '#909399'
  const event = entry.event
  if (event.event_type === 'REPAIR_START') return '#f56c6c'
  if (event.event_type === 'DELIVER') return '#67c23a'
  if (isAnomaly(event)) return '#e6a23c'
  return '#909399'
}

function openIncident(id: number): void {
  void router.push(`/exceptions/${id}`)
}

/** subtitle 同时体现两类条目数量，避免"共 N 条"和列表对不上 */
const subtitle = computed(() => {
  const trackingCount = props.events.length
  const incidentCount = props.incidents.length
  const parts = [`轨迹 ${trackingCount} 条`]
  // 措辞避开"在途异常"（那是操作面板的标题），避免同页两个卡片看起来同名
  if (incidentCount > 0) parts.push(`异常 ${incidentCount} 个`)
  return parts.join(' · ')
})
</script>

<template>
  <PanelCard title="运输轨迹时间线" :subtitle="subtitle" icon="Location">
    <el-empty v-if="!loading && entries.length === 0" description="暂无轨迹事件" :image-size="60" />
    <el-skeleton v-else-if="loading" :rows="5" animated />
    <el-timeline v-else>
      <el-timeline-item
        v-for="entry in entries"
        :key="entry.kind === 'tracking' ? `t-${entry.event.id}` : `${entry.kind}-${entry.incident.id}`"
        :timestamp="formatDateTime(entry.at)"
        :color="dotColor(entry)"
        placement="top"
      >
        <!-- 轨迹事件 -->
        <template v-if="entry.kind === 'tracking'">
          <div class="event-title">
            <b>{{ trackingEventLabel(entry.event.event_type) }}</b>
            <span>{{ entry.event.city ?? '—' }}</span>
            <el-tag v-if="isAnomaly(entry.event)" size="small" type="danger" effect="plain">异常标记</el-tag>
          </div>
          <div class="u-text-muted">
            {{ entry.event.address ?? '—' }} · 来源 {{ trackingSourceLabel(entry.event.source) }}
            <template v-if="entry.event.speed_kmh !== null && entry.event.speed_kmh !== undefined">
              · 速度 {{ entry.event.speed_kmh }} km/h
            </template>
          </div>
          <div v-if="entry.event.payload_json" class="u-text-muted u-mono">
            {{ JSON.stringify(entry.event.payload_json) }}
          </div>
        </template>

        <!-- 异常：开始 -->
        <template v-else-if="entry.kind === 'incident-start'">
          <div class="event-title">
            <b class="u-clickable" @click="openIncident(entry.incident.id)">
              异常 · {{ exceptionTypeLabel(entry.incident.type) }}
            </b>
            <el-tag size="small" :type="isOpenIncident(entry.incident) ? 'danger' : 'info'" effect="plain">
              {{ isOpenIncident(entry.incident) ? '未结束' : exceptionStatusLabel(entry.incident.status) }}
            </el-tag>
            <span class="u-text-muted u-mono">{{ entry.incident.case_no }}</span>
          </div>
          <div class="u-text-muted">
            录入异常（运营）· 当前状态 {{ exceptionStatusLabel(entry.incident.status) }}
            <span v-if="isOpenIncident(entry.incident)"> · 尚未结束</span>
          </div>
        </template>

        <!-- 异常：结束 -->
        <template v-else>
          <div class="event-title">
            <b class="u-clickable" @click="openIncident(entry.incident.id)">
              异常结束 · {{ exceptionTypeLabel(entry.incident.type) }}
            </b>
            <span class="u-text-muted u-mono">{{ entry.incident.case_no }}</span>
          </div>
          <div class="u-text-muted">{{ entry.incident.endedLabel ?? '已解决' }} · 运营确认问题已消除</div>
        </template>
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

.u-clickable {
  cursor: pointer;
}
</style>
