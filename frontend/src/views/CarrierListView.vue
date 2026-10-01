<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'

import PanelCard from '@/components/PanelCard.vue'
import { masterApi } from '@/api'
import { Perm } from '@/types'
import type { Carrier, CarrierStatus, Page } from '@/types'
import { useAuthStore } from '@/stores/auth'
import { carrierStatusLabel, carrierStatusType, maskPhone } from '@/utils/format'

const auth = useAuthStore()
const canManage = computed(() => auth.can(Perm.CARRIER_MANAGE))

const query = reactive({ q: '', status: '' as CarrierStatus | '', page: 1, page_size: 20 })
const result = ref<Page<Carrier>>({ items: [], total: 0, page: 1, page_size: 20 })
const loading = ref(false)

const dialogVisible = ref(false)
const saving = ref(false)
const editingId = ref<number | null>(null)
const form = reactive({
  code: '',
  name: '',
  contact_name: '',
  contact_phone: '',
  service_level: 'NORMAL',
  status: 'ACTIVE' as CarrierStatus,
  remark: '',
})

async function load(): Promise<void> {
  loading.value = true
  try {
    result.value = await masterApi.listCarriers({
      q: query.q || undefined,
      status: query.status || undefined,
      page: query.page,
      page_size: query.page_size,
    })
  } finally {
    loading.value = false
  }
}

function openCreate(): void {
  editingId.value = null
  Object.assign(form, { code: '', name: '', contact_name: '', contact_phone: '', service_level: 'NORMAL', status: 'ACTIVE', remark: '' })
  dialogVisible.value = true
}

function openEdit(row: Carrier): void {
  editingId.value = row.id
  Object.assign(form, {
    code: row.code ?? '',
    name: row.name,
    contact_name: row.contact_name ?? '',
    contact_phone: row.contact_phone ?? '',
    service_level: row.service_level ?? 'NORMAL',
    status: row.status,
    remark: row.remark ?? '',
  })
  dialogVisible.value = true
}

async function submit(): Promise<void> {
  if (!form.name.trim()) {
    ElMessage.warning('承运商名称为必填')
    return
  }
  saving.value = true
  try {
    if (editingId.value) {
      await masterApi.updateCarrier(editingId.value, { ...form })
      ElMessage.success('承运商已更新')
    } else {
      await masterApi.createCarrier({ ...form })
      ElMessage.success('承运商已创建')
    }
    dialogVisible.value = false
    await load()
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="page">
    <PanelCard title="承运商主数据" :subtitle="`共 ${result.total} 家`" icon="Ship">
      <template #actions>
        <el-button v-if="canManage" size="small" type="primary" @click="openCreate">新增承运商</el-button>
        <el-tag v-else size="small" effect="plain">只读（carrier.manage 才可编辑）</el-tag>
      </template>

      <el-form inline class="u-mb-8" @submit.prevent="load">
        <el-form-item label="搜索">
          <el-input v-model="query.q" placeholder="编码 / 名称" clearable style="width: 200px" @keyup.enter="load" />
        </el-form-item>
        <el-form-item label="状态">
          <el-select v-model="query.status" clearable placeholder="全部" style="width: 130px" @change="load">
            <el-option label="合作中" value="ACTIVE" />
            <el-option label="已暂停" value="SUSPENDED" />
          </el-select>
        </el-form-item>
        <el-form-item><el-button type="primary" @click="load">查询</el-button></el-form-item>
      </el-form>

      <el-table :data="result.items" v-loading="loading" size="small" border stripe>
        <el-table-column prop="code" label="编码" width="110" />
        <el-table-column prop="name" label="承运商" min-width="160" />
        <el-table-column label="服务等级" width="110" align="center">
          <template #default="{ row }">{{ row.service_level ?? '—' }}</template>
        </el-table-column>
        <el-table-column label="联系人" min-width="140">
          <template #default="{ row }">{{ row.contact_name ?? '—' }}</template>
        </el-table-column>
        <el-table-column label="电话（脱敏）" width="140">
          <template #default="{ row }">{{ maskPhone(row.contact_phone) }}</template>
        </el-table-column>
        <el-table-column label="状态" width="110" align="center">
          <template #default="{ row }">
            <el-tag size="small" :type="carrierStatusType(row.status)">{{ carrierStatusLabel(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="remark" label="备注" min-width="160" />
        <el-table-column label="操作" width="90" fixed="right">
          <template #default="{ row }">
            <el-button v-if="canManage" size="small" text type="primary" @click="openEdit(row)">编辑</el-button>
            <span v-else class="u-text-muted">—</span>
          </template>
        </el-table-column>
      </el-table>

      <div class="u-text-muted u-mt-8">契约：GET|POST /carriers · GET|PATCH /carriers/{id}</div>
    </PanelCard>

    <el-dialog v-model="dialogVisible" :title="editingId ? '编辑承运商' : '新增承运商'" width="520px">
      <el-form label-width="90px">
        <el-form-item label="编码"><el-input v-model="form.code" placeholder="CA-05" /></el-form-item>
        <el-form-item label="名称"><el-input v-model="form.name" /></el-form-item>
        <el-form-item label="服务等级"><el-input v-model="form.service_level" placeholder="A / B / NORMAL" /></el-form-item>
        <el-form-item label="联系人"><el-input v-model="form.contact_name" /></el-form-item>
        <el-form-item label="电话"><el-input v-model="form.contact_phone" /></el-form-item>
        <el-form-item label="状态">
          <el-select v-model="form.status" style="width: 100%">
            <el-option label="合作中" value="ACTIVE" />
            <el-option label="已暂停" value="SUSPENDED" />
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
