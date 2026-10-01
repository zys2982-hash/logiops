<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'

import PanelCard from '@/components/PanelCard.vue'
import { masterApi } from '@/api'
import { Perm } from '@/types'
import type { Carrier, Driver, DriverStatus, Page } from '@/types'
import { useAuthStore } from '@/stores/auth'
import { driverStatusLabel, driverStatusType, maskPhone } from '@/utils/format'

const auth = useAuthStore()
const canManage = computed(() => auth.can(Perm.DRIVER_MANAGE))

const query = reactive({ q: '', status: '' as DriverStatus | '', carrier_id: '' as number | '', page: 1, page_size: 20 })
const result = ref<Page<Driver>>({ items: [], total: 0, page: 1, page_size: 20 })
const carriers = ref<Carrier[]>([])
const loading = ref(false)

const dialogVisible = ref(false)
const saving = ref(false)
const editingId = ref<number | null>(null)
const form = reactive({ name: '', phone: '', carrier_id: null as number | null, license_no: '', status: 'AVAILABLE' as DriverStatus })

const statusOptions = [
  { value: 'AVAILABLE', label: '可派' },
  { value: 'ON_TRIP', label: '在途' },
  { value: 'OFF_DUTY', label: '休息' },
]

function carrierName(id?: number | null): string {
  if (!id) return '—'
  return carriers.value.find((c) => c.id === id)?.name ?? `#${id}`
}

async function load(): Promise<void> {
  loading.value = true
  try {
    result.value = await masterApi.listDrivers({
      q: query.q || undefined,
      status: query.status || undefined,
      carrier_id: query.carrier_id === '' ? undefined : Number(query.carrier_id),
      page: query.page,
      page_size: query.page_size,
    })
  } finally {
    loading.value = false
  }
}

function openCreate(): void {
  editingId.value = null
  Object.assign(form, { name: '', phone: '', carrier_id: null, license_no: '', status: 'AVAILABLE' })
  dialogVisible.value = true
}

function openEdit(row: Driver): void {
  editingId.value = row.id
  Object.assign(form, {
    name: row.name,
    phone: row.phone ?? '',
    carrier_id: row.carrier_id ?? null,
    license_no: row.license_no ?? '',
    status: row.status,
  })
  dialogVisible.value = true
}

async function submit(): Promise<void> {
  if (!form.name.trim()) {
    ElMessage.warning('司机姓名为必填')
    return
  }
  saving.value = true
  try {
    if (editingId.value) {
      await masterApi.updateDriver(editingId.value, { ...form })
      ElMessage.success('司机已更新')
    } else {
      await masterApi.createDriver({ ...form })
      ElMessage.success('司机已创建')
    }
    dialogVisible.value = false
    await load()
  } finally {
    saving.value = false
  }
}

onMounted(async () => {
  await load()
  try {
    const page = await masterApi.listCarriers({ page: 1, page_size: 100 })
    carriers.value = page.items
  } catch {
    carriers.value = []
  }
})
</script>

<template>
  <div class="page">
    <PanelCard title="司机主数据" :subtitle="`共 ${result.total} 名`" icon="User">
      <template #actions>
        <el-button v-if="canManage" size="small" type="primary" @click="openCreate">新增司机</el-button>
        <el-tag v-else size="small" effect="plain">只读（driver.manage 才可编辑）</el-tag>
      </template>

      <el-form inline class="u-mb-8" @submit.prevent="load">
        <el-form-item label="姓名">
          <el-input v-model="query.q" clearable style="width: 160px" @keyup.enter="load" />
        </el-form-item>
        <el-form-item label="状态">
          <el-select v-model="query.status" clearable placeholder="全部" style="width: 130px" @change="load">
            <el-option v-for="option in statusOptions" :key="option.value" :label="option.label" :value="option.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="承运商">
          <el-select v-model="query.carrier_id" clearable placeholder="全部" style="width: 170px" @change="load">
            <el-option v-for="carrier in carriers" :key="carrier.id" :label="carrier.name" :value="carrier.id" />
          </el-select>
        </el-form-item>
        <el-form-item><el-button type="primary" @click="load">查询</el-button></el-form-item>
      </el-form>

      <el-table :data="result.items" v-loading="loading" size="small" border stripe>
        <el-table-column prop="name" label="姓名" width="120" />
        <el-table-column label="电话（脱敏）" width="150">
          <template #default="{ row }">{{ maskPhone(row.phone) }}</template>
        </el-table-column>
        <el-table-column label="承运商" min-width="150">
          <template #default="{ row }">{{ carrierName(row.carrier_id) }}</template>
        </el-table-column>
        <el-table-column prop="license_no" label="驾驶证号" width="140" />
        <el-table-column label="状态" width="110" align="center">
          <template #default="{ row }">
            <el-tag size="small" :type="driverStatusType(row.status)">{{ driverStatusLabel(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="90" fixed="right">
          <template #default="{ row }">
            <el-button v-if="canManage" size="small" text type="primary" @click="openEdit(row)">编辑</el-button>
            <span v-else class="u-text-muted">—</span>
          </template>
        </el-table-column>
      </el-table>

      <div class="u-text-muted u-mt-8">契约：GET|POST /drivers · GET|PATCH /drivers/{id}</div>
    </PanelCard>

    <el-dialog v-model="dialogVisible" :title="editingId ? '编辑司机' : '新增司机'" width="480px">
      <el-form label-width="90px">
        <el-form-item label="姓名"><el-input v-model="form.name" /></el-form-item>
        <el-form-item label="电话"><el-input v-model="form.phone" placeholder="13800000031" /></el-form-item>
        <el-form-item label="承运商">
          <el-select v-model="form.carrier_id" clearable style="width: 100%">
            <el-option v-for="carrier in carriers" :key="carrier.id" :label="carrier.name" :value="carrier.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="驾驶证号"><el-input v-model="form.license_no" /></el-form-item>
        <el-form-item label="状态">
          <el-select v-model="form.status" style="width: 100%">
            <el-option v-for="option in statusOptions" :key="option.value" :label="option.label" :value="option.value" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="submit">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>
