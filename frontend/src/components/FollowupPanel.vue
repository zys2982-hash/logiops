<script setup lang="ts">
import { ref } from 'vue'

import PanelCard from './PanelCard.vue'
import { formatDateTime, formatFromNow } from '@/utils/datetime'
import { followupStatusLabel, followupStatusType } from '@/utils/format'
import type { FollowupTask } from '@/types'

defineProps<{
  tasks: FollowupTask[]
  canWrite?: boolean
}>()

const emit = defineEmits<{
  (e: 'done', id: number): void
  (e: 'create', payload: { title: string; content: string; due_at: string | null }): void
}>()

const showCreate = ref(false)
const title = ref('')
const content = ref('')
const dueAt = ref<string | null>(null)

function submit(): void {
  if (!title.value.trim()) return
  emit('create', { title: title.value.trim(), content: content.value.trim(), due_at: dueAt.value })
  title.value = ''
  content.value = ''
  dueAt.value = null
  showCreate.value = false
}

function overdue(task: FollowupTask): boolean {
  return task.status === 'OPEN' && !!task.due_at && Date.parse(task.due_at) < Date.now()
}
</script>

<template>
  <PanelCard title="跟进任务" :subtitle="`${tasks.filter((t) => t.status === 'OPEN').length} 个待办`" icon="List">
    <template #actions>
      <el-button v-if="canWrite" size="small" @click="showCreate = !showCreate">
        {{ showCreate ? '收起' : '新建' }}
      </el-button>
    </template>

    <div v-if="showCreate" class="create-block">
      <el-input v-model="title" size="small" placeholder="任务标题（必填）" class="u-mb-8" />
      <el-input v-model="content" type="textarea" :rows="2" placeholder="说明（可选）" class="u-mb-8" />
      <div class="create-row">
        <el-date-picker v-model="dueAt" type="datetime" size="small" placeholder="截止时间" style="width: 190px" />
        <el-button size="small" type="primary" @click="submit">创建</el-button>
      </div>
    </div>

    <el-empty v-if="tasks.length === 0" description="暂无跟进任务" :image-size="50" />

    <div v-for="task in tasks" :key="task.id" class="task-item" :class="{ 'task-done': task.status === 'DONE' }">
      <el-checkbox
        :model-value="task.status === 'DONE'"
        :disabled="!canWrite || task.status === 'DONE'"
        @change="emit('done', task.id)"
      >
        <span :class="{ 'task-title-done': task.status === 'DONE' }">{{ task.title }}</span>
      </el-checkbox>
      <el-tag size="small" :type="followupStatusType(task.status)" effect="plain">
        {{ followupStatusLabel(task.status) }}
      </el-tag>
      <el-tag size="small" effect="plain">{{ task.source }}</el-tag>
      <span class="u-text-muted">
        {{ task.assignee_name ?? '未指派' }}
        <template v-if="task.due_at">
          · 截止 {{ formatDateTime(task.due_at) }}
          <span v-if="overdue(task)" class="log-level-CRITICAL">（已逾期）</span>
        </template>
      </span>
      <span v-if="task.content" class="u-text-muted task-content">{{ task.content }}</span>
      <span v-if="task.done_at" class="u-text-muted">完成于 {{ formatFromNow(task.done_at) }}</span>
    </div>
  </PanelCard>
</template>

<style scoped>
.create-block {
  border: 1px dashed #dcdfe6;
  border-radius: 6px;
  padding: 8px;
  margin-bottom: 10px;
}

.create-row {
  display: flex;
  gap: 8px;
  align-items: center;
}

.task-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 0;
  border-bottom: 1px dashed #ebeef5;
  flex-wrap: wrap;
}

.task-content {
  flex-basis: 100%;
}

.task-done {
  opacity: 0.65;
}

.task-title-done {
  text-decoration: line-through;
}
</style>
