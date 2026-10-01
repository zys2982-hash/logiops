<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'

import PanelCard from '@/components/PanelCard.vue'
import { orderApi } from '@/api'
import { Perm } from '@/types'
import type { Page, SlaRule, SlaScopeType } from '@/types'
import { useAuthStore } from '@/stores/auth'
import { slaScopeLabel } from '@/utils/format'

const auth = useAuthStore()
const canManage = computed(() => auth.can(Perm.SLA_MANAGE))

const result = ref<Page<SlaRule>>({ items: [], total: 0, page: 1, page_size: 50 })
const loading = ref(false)

const dialogVisible = ref(false)
const saving = ref(false)
const editingId = ref<number | null>(null)
const form = reactive({
  name: '',
  scope_type: 'DEFAULT' as SlaScopeType,
  scope_value: '',
  deadline_offset_hours: 30,
  max_delay_minutes: 30,
  priority: 100,
  description: '',
  is_active: true,
})

const scopeOptions = [
  { value: 'DEFAULT', label: '默认（兜底）' },
  { value: 'CUSTOMER_LEVEL', label: '按客户等级' },
  { value: 'CUSTOMER', label: '指定客户' },
]

const sorted = computed(() => [...result.value.items].sort((a, b) => a.priority - b.priority))

async function load(): Promise<void> {
  loading.value = true
  try {
    result.value = await orderApi.listSlaRules({ page: 1, page_size: 100 })
  } finally {
    loading.value = false
  }
}

function openCreate(): void {
  editingId.value = null
  Object.assign(form, {
    name: '',
    scope_type: 'DEFAULT',
    scope_value: '',
    deadline_offset_hours: 30,
    max_delay_minutes: 30,
    priority: 100,
    description: '',
    is_active: true,
  })
  dialogVisible.value = true
}

function openEdit(row: SlaRule): void {
  editingId.value = row.id
  Object.assign(form, {
    name: row.name,
    scope_type: row.scope_type,
    scope_value: row.scope_value ?? '',
    deadline_offset_hours: row.deadline_offset_hours,
    max_delay_minutes: row.max_delay_minutes,
    priority: row.priority,
    description: row.description ?? '',
    is_active: row.is_active,
  })
  dialogVisible.value = true
}

async function submit(): Promise<void> {
  if (!form.name.trim()) {
    ElMessage.warning('规则名称为必填')
    return
  }
  const payload = {
    ...form,
    scope_value: form.scope_type === 'DEFAULT' ? null : form.scope_value || null,
  }
  saving.value = true
  try {
    if (editingId.value) {
      await orderApi.updateSlaRule(editingId.value, payload)
      ElMessage.success('SLA 规则已更新')
    } else {
      await orderApi.createSlaRule(payload)
      ElMessage.success('SLA 规则已创建')
    }
    dialogVisible.value = false
    await load()
  } catch {
    // 唯一键冲突（workspace+scope_type+scope_value）由拦截器提示
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="page">
    <PanelCard
      title="SLA 规则"
      :subtitle="`共 ${result.total} 条 · 按 priority 升序匹配，越具体越优先`"
      icon="Timer"
    >
      <template #actions>
        <el-button v-if="canManage" size="small" type="primary" @click="openCreate">新增规则</el-button>
        <el-tag v-else size="small" effect="plain">只读（sla.manage 才可编辑）</el-tag>
      </template>

      <el-alert
        type="info"
        :closable="false"
        title="匹配顺序（§8.3）"
        description="1) scope_type=CUSTOMER 且 scope_value=order.customer.code → 2) CUSTOMER_LEVEL 且 scope_value=customer.level → 3) DEFAULT；承诺到达 = 发车时间 + deadline_offset_hours；违约 = sla_delay_minutes > max_delay_minutes。"
        class="u-mb-12"
      />

      <el-table :data="sorted" v-loading="loading" size="small" border stripe>
        <el-table-column prop="priority" label="优先级" width="80" align="center" sortable />
        <el-table-column prop="name" label="规则名称" min-width="220" />
        <el-table-column label="作用域" width="140">
          <template #default="{ row }">
            <el-tag size="small" effect="plain">{{ slaScopeLabel(row.scope_type) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="scope_value" width="120">
          <template #default="{ row }">{{ row.scope_value ?? '—' }}</template>
        </el-table-column>
        <el-table-column label="承诺时长" width="140" align="center">
          <template #default="{ row }">发车后 {{ row.deadline_offset_hours }}h</template>
        </el-table-column>
        <el-table-column label="允许延迟" width="120" align="center">
          <template #default="{ row }">{{ row.max_delay_minutes }} min</template>
        </el-table-column>
        <el-table-column label="启用" width="80" align="center">
          <template #default="{ row }">
            <el-tag size="small" :type="row.is_active ? 'success' : 'info'">{{ row.is_active ? '启用' : '停用' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="90" fixed="right">
          <template #default="{ row }">
            <el-button v-if="canManage" size="small" text type="primary" @click="openEdit(row)">编辑</el-button>
            <span v-else class="u-text-muted">—</span>
          </template>
        </el-table-column>
      </el-table>

      <div class="u-text-muted u-mt-8">
        Seed 规则：DEFAULT=30h/30min、CUSTOMER_LEVEL:VIP=24h/0min、CUSTOMER:VIP-01=24h/0min ·
        API：GET|POST /sla-rules · GET|PATCH /sla-rules/{id}
      </div>
    </PanelCard>

    <el-dialog v-model="dialogVisible" :title="editingId ? '编辑 SLA 规则' : '新增 SLA 规则'" width="520px">
      <el-form label-width="110px">
        <el-form-item label="名称"><el-input v-model="form.name" /></el-form-item>
        <el-form-item label="作用域">
          <el-select v-model="form.scope_type" style="width: 100%">
            <el-option v-for="option in scopeOptions" :key="option.value" :label="option.label" :value="option.value" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="form.scope_type !== 'DEFAULT'" label="scope_value">
          <el-input v-model="form.scope_value" :placeholder="form.scope_type === 'CUSTOMER_LEVEL' ? 'VIP / SVIP / NORMAL' : 'VIP-01'" />
        </el-form-item>
        <el-form-item label="承诺时长(h)">
          <el-input-number v-model="form.deadline_offset_hours" :min="1" :max="240" />
        </el-form-item>
        <el-form-item label="允许延迟(min)">
          <el-input-number v-model="form.max_delay_minutes" :min="0" :max="1440" />
        </el-form-item>
        <el-form-item label="优先级">
          <el-input-number v-model="form.priority" :min="1" :max="999" />
        </el-form-item>
        <el-form-item label="说明"><el-input v-model="form.description" type="textarea" :rows="2" /></el-form-item>
        <el-form-item label="启用"><el-switch v-model="form.is_active" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="submit">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>
