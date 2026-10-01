<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'

import PanelCard from '@/components/PanelCard.vue'
import { systemApi } from '@/api'
import type { AuditLog, Page } from '@/types'
import { formatDateTime } from '@/utils/datetime'

const query = reactive({
  action: '',
  resource_type: '',
  resource_id: '' as number | '',
  actor_id: '' as number | '',
  occurred_from: '',
  occurred_to: '',
  page: 1,
  page_size: 20,
})

const result = ref<Page<AuditLog>>({ items: [], total: 0, page: 1, page_size: 20 })
const loading = ref(false)
const drawerVisible = ref(false)
const current = ref<AuditLog | null>(null)

const resourceTypes = ['order', 'exception_case', 'approval', 'ai_analysis', 'notification', 'followup_task', 'customer', 'carrier', 'vehicle', 'driver', 'workspace']

async function load(): Promise<void> {
  loading.value = true
  try {
    result.value = await systemApi.listAuditLogs({
      action: query.action || undefined,
      resource_type: query.resource_type || undefined,
      resource_id: query.resource_id === '' ? undefined : Number(query.resource_id),
      actor_id: query.actor_id === '' ? undefined : Number(query.actor_id),
      occurred_from: query.occurred_from || undefined,
      occurred_to: query.occurred_to || undefined,
      page: query.page,
      page_size: query.page_size,
    })
  } finally {
    loading.value = false
  }
}

function openDetail(row: AuditLog): void {
  current.value = row
  drawerVisible.value = true
}

function sourceTagType(source?: string | null): 'success' | 'warning' | 'info' {
  if (source === 'APPROVED_AI') return 'warning'
  if (source === 'SYSTEM') return 'info'
  return 'success'
}

onMounted(load)
</script>

<template>
  <div class="page">
    <PanelCard title="审计日志" :subtitle="`共 ${result.total} 条（追加写，不可修改）`" icon="Document">
      <el-form inline class="u-mb-8" @submit.prevent="load">
        <el-form-item label="action">
          <el-input v-model="query.action" placeholder="exception.resolve" clearable style="width: 180px" />
        </el-form-item>
        <el-form-item label="资源类型">
          <el-select v-model="query.resource_type" clearable placeholder="全部" style="width: 170px" @change="load">
            <el-option v-for="type in resourceTypes" :key="type" :label="type" :value="type" />
          </el-select>
        </el-form-item>
        <el-form-item label="资源 ID">
          <el-input v-model="query.resource_id" placeholder="12" clearable style="width: 100px" />
        </el-form-item>
        <el-form-item label="操作者 ID">
          <el-input v-model="query.actor_id" placeholder="3" clearable style="width: 100px" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="load">查询</el-button>
        </el-form-item>
      </el-form>

      <el-table :data="result.items" v-loading="loading" size="small" border stripe @row-click="openDetail">
        <el-table-column label="时间" width="160">
          <template #default="{ row }">{{ formatDateTime(row.occurred_at) }}</template>
        </el-table-column>
        <el-table-column prop="action" label="操作" min-width="180" />
        <el-table-column label="操作者" width="140">
          <template #default="{ row }">
            {{ row.actor_name ?? (row.actor_type === 'SYSTEM' ? '系统' : row.actor_type === 'AI' ? 'AI' : `#${row.actor_id}`) }}
          </template>
        </el-table-column>
        <el-table-column label="资源" min-width="160">
          <template #default="{ row }">{{ row.resource_type ?? '—' }} #{{ row.resource_id ?? '—' }}</template>
        </el-table-column>
        <el-table-column label="来源" width="130">
          <template #default="{ row }">
            <el-tag size="small" :type="sourceTagType(row.source)" effect="plain">{{ row.source ?? '—' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="request_id" width="180">
          <template #default="{ row }">
            <span class="u-mono">{{ row.request_id ? row.request_id.slice(0, 8) : '—' }}</span>
          </template>
        </el-table-column>
      </el-table>

      <div class="table-foot">
        <el-pagination
          :current-page="result.page"
          :page-size="result.page_size"
          :total="result.total"
          :page-sizes="[10, 20, 50]"
          layout="total, sizes, prev, pager, next"
          background
          @current-change="(page: number) => { query.page = page; load() }"
          @size-change="(size: number) => { query.page_size = size; query.page = 1; load() }"
        />
      </div>
      <div class="u-text-muted u-mt-8">
        契约：GET /audit-logs?resource_type&amp;resource_id&amp;actor_id&amp;action&amp;occurred_from&amp;occurred_to&amp;page ·
        响应头 X-Request-Id 同时写入审计
      </div>
    </PanelCard>

    <el-drawer v-model="drawerVisible" title="审计详情" size="46%">
      <template v-if="current">
        <el-descriptions :column="1" size="small" border>
          <el-descriptions-item label="时间">{{ formatDateTime(current.occurred_at) }}</el-descriptions-item>
          <el-descriptions-item label="action">{{ current.action }}</el-descriptions-item>
          <el-descriptions-item label="操作者">
            {{ current.actor_type }} #{{ current.actor_id ?? '—' }} {{ current.actor_name ?? '' }}
          </el-descriptions-item>
          <el-descriptions-item label="资源">
            {{ current.resource_type ?? '—' }} #{{ current.resource_id ?? '—' }}
          </el-descriptions-item>
          <el-descriptions-item label="来源">{{ current.source ?? '—' }}</el-descriptions-item>
          <el-descriptions-item label="ip">{{ current.ip ?? '—' }}</el-descriptions-item>
          <el-descriptions-item label="request_id">{{ current.request_id ?? '—' }}</el-descriptions-item>
          <el-descriptions-item label="user_agent">{{ current.user_agent ?? '—' }}</el-descriptions-item>
        </el-descriptions>

        <h4>before</h4>
        <pre class="json-block">{{ JSON.stringify(current.before_json ?? {}, null, 2) }}</pre>
        <h4>after</h4>
        <pre class="json-block">{{ JSON.stringify(current.after_json ?? {}, null, 2) }}</pre>
      </template>
    </el-drawer>
  </div>
</template>

<style scoped>
.table-foot {
  display: flex;
  justify-content: flex-end;
  margin-top: 12px;
}

.json-block {
  background: #f5f7fa;
  border-radius: 4px;
  padding: 8px;
  font-size: 12px;
  overflow: auto;
}
</style>
