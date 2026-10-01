<script setup lang="ts">
import { ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import PanelCard from './PanelCard.vue'
import { formatDateTime } from '@/utils/datetime'
import { notificationStatusLabel, notificationStatusType } from '@/utils/format'
import type { Notification } from '@/types'

defineProps<{
  notifications: Notification[]
  canApprove?: boolean
}>()

const emit = defineEmits<{
  (e: 'update', payload: { id: number; content: string; subject?: string }): void
  (e: 'approve', id: number): void
  (e: 'send', id: number): void
  (e: 'skip', id: number): void
}>()

const editingId = ref<number | null>(null)
const draftContent = ref('')
const draftSubject = ref('')

function startEdit(item: Notification): void {
  editingId.value = item.id
  draftContent.value = item.content
  draftSubject.value = item.subject ?? ''
}

function submitEdit(item: Notification): void {
  emit('update', { id: item.id, content: draftContent.value, subject: draftSubject.value })
  editingId.value = null
}

async function copy(item: Notification): Promise<void> {
  const text = `${item.subject ?? ''}\n${item.content}`.trim()
  try {
    await navigator.clipboard.writeText(text)
    ElMessage.success('已复制到剪贴板')
  } catch {
    // 非 HTTPS / 无权限时退化为手动提示
    ElMessage.warning('浏览器拒绝剪贴板写入，请手动选择文本复制')
  }
}

async function send(item: Notification): Promise<void> {
  try {
    await ElMessageBox.confirm('模拟发送不会真的发信，只把状态置为 SENT_MOCK 并写审计。继续？', '模拟发送', {
      type: 'warning',
    })
  } catch {
    return
  }
  emit('send', item.id)
}
</script>

<template>
  <PanelCard title="客户通知" :subtitle="`${notifications.length} 条（草稿/编辑/批准/复制/模拟发送）`" icon="Message">
    <el-empty v-if="notifications.length === 0" description="暂无客户通知" :image-size="50" />

    <div v-for="item in notifications" :key="item.id" class="notice-block">
      <div class="notice-head">
        <b>{{ item.subject ?? '（无标题）' }}</b>
        <el-tag size="small" :type="notificationStatusType(item.status)" effect="plain">
          {{ notificationStatusLabel(item.status) }}
        </el-tag>
        <span class="u-text-muted u-mono">{{ item.channel }}{{ item.ai_draft_content ? ' · AI 草稿留痕' : '' }}</span>
      </div>

      <template v-if="editingId === item.id">
        <el-input v-model="draftSubject" size="small" placeholder="标题" class="u-mb-8" />
        <el-input v-model="draftContent" type="textarea" :rows="4" />
        <div class="notice-actions u-mt-8">
          <el-button size="small" type="primary" @click="submitEdit(item)">保存草稿</el-button>
          <el-button size="small" @click="editingId = null">取消</el-button>
        </div>
      </template>
      <template v-else>
        <div class="notice-body">{{ item.content }}</div>
        <div v-if="item.ai_draft_content && item.ai_draft_content !== item.content" class="ai-draft">
          <div class="u-text-muted">AI 原稿（未修改前）：</div>
          <div class="diff-ai">{{ item.ai_draft_content }}</div>
        </div>
      </template>

      <div class="notice-foot">
        <span class="u-text-muted">
          创建 {{ formatDateTime(item.created_at) }}
          <template v-if="item.approved_at"> · 批准 {{ formatDateTime(item.approved_at) }}</template>
          <template v-if="item.sent_at"> · 发送 {{ formatDateTime(item.sent_at) }}</template>
        </span>
        <span class="notice-actions">
          <el-button size="small" text type="primary" @click="copy(item)">复制</el-button>
          <template v-if="canApprove">
            <el-button v-if="item.status === 'DRAFT'" size="small" text type="primary" @click="emit('approve', item.id)">
              批准
            </el-button>
            <el-button size="small" text type="success" @click="send(item)">模拟发送</el-button>
            <el-button size="small" text @click="startEdit(item)">编辑</el-button>
            <el-button size="small" text type="info" @click="emit('skip', item.id)">跳过</el-button>
          </template>
        </span>
      </div>
    </div>
  </PanelCard>
</template>

<style scoped>
.notice-block {
  border: 1px solid #ebeef5;
  border-radius: 6px;
  padding: 8px;
  margin-bottom: 10px;
}

.notice-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
  flex-wrap: wrap;
}

.notice-body {
  white-space: pre-wrap;
  font-size: 13px;
  background: #f5f7fa;
  padding: 8px;
  border-radius: 4px;
}

.ai-draft {
  margin-top: 6px;
  font-size: 12px;
}

.notice-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-top: 6px;
  flex-wrap: wrap;
}

.notice-actions {
  display: flex;
  gap: 4px;
}
</style>
