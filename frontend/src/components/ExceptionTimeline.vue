<script setup lang="ts">
import PanelCard from './PanelCard.vue'
import { formatDateTime, formatFromNow } from '@/utils/datetime'
import { exceptionEventLabel } from '@/utils/format'
import type { ActorType, ExceptionEvent } from '@/types'

defineProps<{ events: ExceptionEvent[] }>()

function actorLabel(event: ExceptionEvent): string {
  const type: ActorType = event.actor_type
  const base = type === 'AI' ? 'AI' : type === 'SYSTEM' ? '系统' : '用户'
  return event.actor_name ? `${base} · ${event.actor_name}` : base
}

function actorColor(event: ExceptionEvent): string {
  if (event.actor_type === 'AI') return '#409eff'
  if (event.actor_type === 'SYSTEM') return '#909399'
  return '#67c23a'
}

function eventColor(event: ExceptionEvent): string {
  if (event.event_type === 'ANALYSIS_FAILED' || event.event_type === 'EXECUTE_FAILED') return '#f56c6c'
  if (event.event_type === 'CLOSED') return '#909399'
  if (event.event_type === 'ANALYSIS_READY' || event.event_type === 'EXECUTED') return '#67c23a'
  return '#409eff'
}
</script>

<template>
  <PanelCard id="evidence-timeline" title="操作记录" :subtitle="`${events.length} 条`" icon="Finished">
    <el-empty v-if="events.length === 0" description="暂无操作记录" :image-size="50" />
    <el-timeline v-else>
      <el-timeline-item
        v-for="event in events"
        :key="event.id"
        :timestamp="formatDateTime(event.occurred_at)"
        :color="eventColor(event)"
        placement="top"
      >
        <div class="event-head">
          <b>{{ exceptionEventLabel(event.event_type) }}</b>
          <el-tag size="small" effect="plain" :style="{ color: actorColor(event), borderColor: actorColor(event) }">
            {{ actorLabel(event) }}
          </el-tag>
          <template v-if="event.from_status && event.to_status">
            <span class="u-text-muted">{{ event.from_status }} → {{ event.to_status }}</span>
          </template>
        </div>
        <div v-if="event.note" class="timeline-note">{{ event.note }}</div>
        <div v-if="event.detail" class="u-text-muted u-mono">{{ JSON.stringify(event.detail) }}</div>
        <div class="u-text-muted">{{ formatFromNow(event.occurred_at) }}</div>
      </el-timeline-item>
    </el-timeline>
  </PanelCard>
</template>

<style scoped>
.event-head {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}
</style>
