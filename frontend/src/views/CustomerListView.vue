<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'

import PanelCard from '@/components/PanelCard.vue'
import { masterApi } from '@/api'
import { Perm } from '@/types'
import type { Customer, CustomerLevel, Page } from '@/types'
import { useAuthStore } from '@/stores/auth'
import { CUSTOMER_LEVEL_OPTIONS, customerLevelLabel, customerLevelType, maskPhone } from '@/utils/format'

const auth = useAuthStore()
const canManage = computed(() => auth.can(Perm.CUSTOMER_MANAGE))

const query = reactive({ q: '', level: '' as CustomerLevel | '', page: 1, page_size: 20 })
const result = ref<Page<Customer>>({ items: [], total: 0, page: 1, page_size: 20 })
const loading = ref(false)

const dialogVisible = ref(false)
const saving = ref(false)
const editingId = ref<number | null>(null)
const form = reactive({
  code: '',
  name: '',
  level: 'NORMAL' as CustomerLevel,
  contact_name: '',
  contact_phone: '',
  contact_email: '',
  notify_pref: 'MANUAL_COPY',
  remark: '',
})

async function load(): Promise<void> {
  loading.value = true
  try {
    result.value = await masterApi.listCustomers({
      q: query.q || undefined,
      level: query.level || undefined,
      page: query.page,
      page_size: query.page_size,
    })
  } finally {
    loading.value = false
  }
}

function search(): void {
  query.page = 1
  void load()
}

function openCreate(): void {
  editingId.value = null
  Object.assign(form, {
    code: '',
    name: '',
    level: 'NORMAL',
    contact_name: '',
    contact_phone: '',
    contact_email: '',
    notify_pref: 'MANUAL_COPY',
    remark: '',
  })
  dialogVisible.value = true
}

function openEdit(row: Customer): void {
  editingId.value = row.id
  Object.assign(form, {
    code: row.code,
    name: row.name,
    level: row.level,
    contact_name: row.contact_name ?? '',
    contact_phone: row.contact_phone ?? '',
    contact_email: row.contact_email ?? '',
    notify_pref: row.notify_pref ?? 'MANUAL_COPY',
    remark: row.remark ?? '',
  })
  dialogVisible.value = true
}

async function submit(): Promise<void> {
  if (!form.name.trim() || !form.code.trim()) {
    ElMessage.warning('编码与名称为必填')
    return
  }
  saving.value = true
  try {
    if (editingId.value) {
      await masterApi.updateCustomer(editingId.value, { ...form })
      ElMessage.success('客户已更新')
    } else {
      await masterApi.createCustomer({ ...form })
      ElMessage.success('客户已创建')
    }
    dialogVisible.value = false
    await load()
  } catch {
    // 409 DUPLICATE_ENTITY 已提示
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="page">
    <PanelCard title="客户主数据" :subtitle="`共 ${result.total} 个（NORMAL/VIP/SVIP 影响 SLA 规则匹配）`" icon="OfficeBuilding">
      <template #actions>
        <el-button v-if="canManage" size="small" type="primary" @click="openCreate">新增客户</el-button>
        <el-tag v-else size="small" effect="plain">只读（customer.manage 才可编辑）</el-tag>
      </template>

      <el-form inline class="u-mb-8" @submit.prevent="search">
        <el-form-item label="搜索">
          <el-input v-model="query.q" placeholder="编码 / 名称" clearable style="width: 200px" @keyup.enter="search" />
        </el-form-item>
        <el-form-item label="等级">
          <el-select v-model="query.level" clearable placeholder="全部" style="width: 130px" @change="search">
            <el-option v-for="option in CUSTOMER_LEVEL_OPTIONS" :key="option.value" :label="option.label" :value="option.value" />
          </el-select>
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="search">查询</el-button>
        </el-form-item>
      </el-form>

      <el-table :data="result.items" v-loading="loading" size="small" border stripe>
        <el-table-column prop="code" label="编码" width="110" />
        <el-table-column prop="name" label="客户名称" min-width="160" />
        <el-table-column label="等级" width="100" align="center">
          <template #default="{ row }">
            <el-tag size="small" :type="customerLevelType(row.level)">{{ customerLevelLabel(row.level) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="联系人" min-width="140">
          <template #default="{ row }">{{ row.contact_name ?? '—' }}</template>
        </el-table-column>
        <el-table-column label="电话（脱敏）" width="140">
          <template #default="{ row }">{{ maskPhone(row.contact_phone) }}</template>
        </el-table-column>
        <el-table-column label="通知偏好" width="140">
          <template #default="{ row }">{{ row.notify_pref ?? '—' }}</template>
        </el-table-column>
        <el-table-column prop="remark" label="备注" min-width="160" />
        <el-table-column label="操作" width="90" fixed="right">
          <template #default="{ row }">
            <el-button v-if="canManage" size="small" text type="primary" @click="openEdit(row)">编辑</el-button>
            <span v-else class="u-text-muted">—</span>
          </template>
        </el-table-column>
      </el-table>

      <div class="table-foot">
        <el-pagination
          :current-page="result.page"
          :page-size="result.page_size"
          :total="result.total"
          layout="total, prev, pager, next"
          background
          @current-change="(page: number) => { query.page = page; load() }"
        />
      </div>
      <div class="u-text-muted u-mt-8">
        API 响应中的 phone 一律脱敏（§8.7）：列表展示 138****0001。
      </div>
    </PanelCard>

    <el-dialog v-model="dialogVisible" :title="editingId ? '编辑客户' : '新增客户'" width="520px">
      <el-form label-width="90px">
        <el-form-item label="编码"><el-input v-model="form.code" placeholder="VIP-01" /></el-form-item>
        <el-form-item label="名称"><el-input v-model="form.name" /></el-form-item>
        <el-form-item label="等级">
          <el-select v-model="form.level" style="width: 100%">
            <el-option v-for="option in CUSTOMER_LEVEL_OPTIONS" :key="option.value" :label="option.label" :value="option.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="联系人"><el-input v-model="form.contact_name" /></el-form-item>
        <el-form-item label="电话"><el-input v-model="form.contact_phone" placeholder="13800000011" /></el-form-item>
        <el-form-item label="邮箱"><el-input v-model="form.contact_email" /></el-form-item>
        <el-form-item label="通知偏好">
          <el-select v-model="form.notify_pref" style="width: 100%">
            <el-option label="人工复制" value="MANUAL_COPY" />
            <el-option label="模拟邮件" value="MOCK_EMAIL" />
          </el-select>
        </el-form-item>
        <el-form-item label="备注"><el-input v-model="form.remark" type="textarea" :rows="2" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="submit">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.table-foot {
  display: flex;
  justify-content: flex-end;
  margin-top: 12px;
}
</style>
