<script setup lang="ts">
/** 审计日志（基线 §9.3）：后端存机器码，本页负责翻译成业务人员看得懂的中文。
 *  原则：中文为主、原始机器值以小字/折叠保留，绝不隐藏事实。
 */
import { computed, onMounted, reactive, ref } from 'vue'

import PanelCard from '@/components/PanelCard.vue'
import { systemApi } from '@/api'
import type { AuditLog, Page } from '@/types'
import { formatDateTime } from '@/utils/datetime'
import {
  AUDIT_ACTION_OPTIONS,
  AUDIT_RESOURCE_OPTIONS,
  auditActionLabel,
  auditActorTypeLabel,
  auditFieldChanges,
  auditResourceLabel,
  auditSourceLabel,
  auditSourceType,
} from '@/utils/format'

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

/** 抽屉里的"变化内容"（只列真正变了的字段） */
const changes = computed(() => auditFieldChanges(current.value?.before_json, current.value?.after_json))

/** 该条是否有可展示的原始数据 / 技术信息 */
const hasRawPayload = computed(
  () => !!current.value?.before_json || !!current.value?.after_json,
)
const hasTechInfo = computed(() => !!(current.value?.ip || current.value?.request_id || current.value?.user_agent))

function actorText(row: AuditLog): string {
  if (row.actor_name) return row.actor_name
  if (row.actor_type === 'USER') return row.actor_id ? `用户 #${row.actor_id}` : '用户'
  return auditActorTypeLabel(row.actor_type)
}

function openDetail(row: AuditLog): void {
  current.value = row
  drawerVisible.value = true
}

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

onMounted(load)
</script>

<template>
  <div class="page">
    <PanelCard
      title="审计日志"
      :subtitle="`共 ${result.total} 条 · 只追加、不可修改（谁在什么时候做了什么，全部留痕）`"
      icon="Document"
    >
      <el-form inline class="u-mb-8" @submit.prevent="load">
        <el-form-item label="操作">
          <el-select
            v-model="query.action"
            clearable
            filterable
            placeholder="全部操作"
            style="width: 220px"
            @change="load"
          >
            <el-option v-for="option in AUDIT_ACTION_OPTIONS" :key="option.value" :label="option.label" :value="option.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="对象类型">
          <el-select
            v-model="query.resource_type"
            clearable
            placeholder="全部类型"
            style="width: 150px"
            @change="load"
          >
            <el-option v-for="option in AUDIT_RESOURCE_OPTIONS" :key="option.value" :label="option.label" :value="option.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="对象编号">
          <el-input v-model="query.resource_id" placeholder="如 12" clearable style="width: 110px" />
        </el-form-item>
        <el-form-item label="操作者编号">
          <el-input v-model="query.actor_id" placeholder="如 3" clearable style="width: 110px" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="load">查询</el-button>
        </el-form-item>
      </el-form>

      <el-table :data="result.items" v-loading="loading" size="small" border stripe @row-click="openDetail">
        <el-table-column label="时间" width="160">
          <template #default="{ row }">{{ formatDateTime(row.occurred_at) }}</template>
        </el-table-column>
        <el-table-column label="做了什么" min-width="200">
          <template #default="{ row }">
            <div>{{ auditActionLabel(row.action) }}</div>
            <div class="raw-code">{{ row.action }}</div>
          </template>
        </el-table-column>
        <el-table-column label="谁做的" width="130">
          <template #default="{ row }">{{ actorText(row) }}</template>
        </el-table-column>
        <el-table-column label="对哪个对象" min-width="150">
          <template #default="{ row }">
            {{ auditResourceLabel(row.resource_type) }}<span v-if="row.resource_id"> #{{ row.resource_id }}</span>
          </template>
        </el-table-column>
        <el-table-column label="怎么发生的" width="190">
          <template #default="{ row }">
            <el-tag size="small" :type="auditSourceType(row.source)" effect="plain">
              {{ auditSourceLabel(row.source) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="请求号" width="110">
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
        点任意一行看详情。机器码（如 exception.close）与原始字段以小字保留，方便核对；
        同一次请求的多条写入共享一个请求号（响应头 X-Request-Id）。
      </div>
    </PanelCard>

    <el-drawer v-model="drawerVisible" title="这条审计记录" size="46%">
      <template v-if="current">
        <el-descriptions :column="1" size="small" border>
          <el-descriptions-item label="时间">{{ formatDateTime(current.occurred_at) }}</el-descriptions-item>
          <el-descriptions-item label="做了什么">
            {{ auditActionLabel(current.action) }}
            <span class="raw-code">{{ current.action }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="谁做的">{{ actorText(current) }}</el-descriptions-item>
          <el-descriptions-item label="对哪个对象">
            {{ auditResourceLabel(current.resource_type) }}
            <span v-if="current.resource_id">#{{ current.resource_id }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="怎么发生的">{{ auditSourceLabel(current.source) }}</el-descriptions-item>
        </el-descriptions>

        <h4>变化内容</h4>
        <el-table v-if="changes.length" :data="changes" size="small" border>
          <el-table-column prop="label" label="字段" width="150" />
          <el-table-column prop="before" label="变化前" min-width="140" />
          <el-table-column prop="after" label="变化后" min-width="140" />
        </el-table>
        <div v-else class="u-text-muted">这条记录没有字段变化（例如登录、查询类操作）。</div>

        <el-collapse v-if="hasRawPayload" class="u-mt-12">
          <el-collapse-item title="原始数据（技术核对用）" name="raw">
            <div class="u-text-muted">before</div>
            <pre class="json-block">{{ JSON.stringify(current.before_json ?? {}, null, 2) }}</pre>
            <div class="u-text-muted">after</div>
            <pre class="json-block">{{ JSON.stringify(current.after_json ?? {}, null, 2) }}</pre>
          </el-collapse-item>
        </el-collapse>

        <el-collapse v-if="hasTechInfo" class="u-mt-8">
          <el-collapse-item title="技术信息（IP / 请求号 / 浏览器）" name="tech">
            <el-descriptions :column="1" size="small" border>
              <el-descriptions-item label="IP">{{ current.ip ?? '—' }}</el-descriptions-item>
              <el-descriptions-item label="请求号">{{ current.request_id ?? '—' }}</el-descriptions-item>
              <el-descriptions-item label="浏览器">{{ current.user_agent ?? '—' }}</el-descriptions-item>
            </el-descriptions>
          </el-collapse-item>
        </el-collapse>
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

/* 机器码/原始值：小字弱化，保留但不干扰阅读 */
.raw-code {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 11px;
  color: #909399;
}

.json-block {
  background: #f5f7fa;
  border-radius: 4px;
  padding: 8px;
  font-size: 12px;
  overflow: auto;
}

h4 {
  margin: 14px 0 6px;
}
</style>
