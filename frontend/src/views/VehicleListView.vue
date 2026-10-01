<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'

import PanelCard from '@/components/PanelCard.vue'
import { masterApi } from '@/api'
import { Perm } from '@/types'
import type { Carrier, Driver, Page, Vehicle, VehicleStatus } from '@/types'
import { useAuthStore } from '@/stores/auth'
import { vehicleStatusLabel, vehicleStatusType } from '@/utils/format'

const auth = useAuthStore()
const canManage = computed(() => auth.can(Perm.VEHICLE_MANAGE))

const query = reactive({ plate_no: '', status: '' as VehicleStatus | '', carrier_id: '' as number | '', page: 1, page_size: 20 })
const result = ref<Page<Vehicle>>({ items: [], total: 0, page: 1, page_size: 20 })
const carriers = ref<Carrier[]>([])
const drivers = ref<Driver[]>([])
const loading = ref(false)

const dialogVisible = ref(false)
const saving = ref(false)
const editingId = ref<number | null>(null)
const form = reactive({
  plate_no: '',
  vehicle_type: '',
  capacity_ton: 0,
  carrier_id: null as number | null,
  current_driver_id: null as number | null,
  current_city: '',
  status: 'IDLE' as VehicleStatus,
  remark: '',
})

const statusOptions = [
  { value: 'IDLE', label: '空闲' },
  { value: 'IN_TRANSIT', label: '在途' },
  { value: 'REPAIRING', label: '维修中' },
  { value: 'OFFLINE', label: '离线' },
]

function carrierName(id?: number | null): string {
  if (!id) return '—'
  return carriers.value.find((c) => c.id === id)?.name ?? `#${id}`
}

function driverName(id?: number | null): string {
  if (!id) return '—'
  return drivers.value.find((d) => d.id === id)?.name ?? `#${id}`
}

async function load(): Promise<void> {
  loading.value = true
  try {
    result.value = await masterApi.listVehicles({
      plate_no: query.plate_no || undefined,
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
  Object.assign(form, { plate_no: '', vehicle_type: '', capacity_ton: 0, carrier_id: null, current_driver_id: null, current_city: '', status: 'IDLE', remark: '' })
  dialogVisible.value = true
}

function openEdit(row: Vehicle): void {
  editingId.value = row.id
  Object.assign(form, {
    plate_no: row.plate_no,
    vehicle_type: row.vehicle_type ?? '',
    capacity_ton: row.capacity_ton ?? 0,
    carrier_id: row.carrier_id ?? null,
    current_driver_id: row.current_driver_id ?? null,
    current_city: row.current_city ?? '',
    status: row.status,
    remark: row.remark ?? '',
  })
  dialogVisible.value = true
}

async function submit(): Promise<void> {
  if (!form.plate_no.trim()) {
    ElMessage.warning('车牌号为必填')
    return
  }
  saving.value = true
  try {
    if (editingId.value) {
      await masterApi.updateVehicle(editingId.value, { ...form })
      ElMessage.success('车辆已更新')
    } else {
      await masterApi.createVehicle({ ...form })
      ElMessage.success('车辆已创建')
    }
    dialogVisible.value = false
    await load()
  } finally {
    saving.value = false
  }
}

onMounted(async () => {
  await load()
  const [carrierResult, driverResult] = await Promise.allSettled([
    masterApi.listCarriers({ page: 1, page_size: 100 }),
    masterApi.listDrivers({ page: 1, page_size: 200 }),
  ])
  if (carrierResult.status === 'fulfilled') carriers.value = carrierResult.value.items
  if (driverResult.status === 'fulfilled') drivers.value = driverResult.value.items
})
</script>

<template>
  <div class="page">
    <PanelCard title="车辆主数据" :subtitle="`共 ${result.total} 台（状态影响 ETA 重算：REPAIRING 走 REPAIR_WAIT）`" icon="Truck">
      <template #actions>
        <el-button v-if="canManage" size="small" type="primary" @click="openCreate">新增车辆</el-button>
        <el-tag v-else size="small" effect="plain">只读（vehicle.manage 才可编辑）</el-tag>
      </template>

      <el-form inline class="u-mb-8" @submit.prevent="load">
        <el-form-item label="车牌">
          <el-input v-model="query.plate_no" placeholder="津A·12345" clearable style="width: 160px" @keyup.enter="load" />
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
        <el-table-column prop="plate_no" label="车牌" width="130" />
        <el-table-column prop="vehicle_type" label="车型" min-width="130" />
        <el-table-column label="载重(t)" width="90" align="center">
          <template #default="{ row }">{{ row.capacity_ton ?? '—' }}</template>
        </el-table-column>
        <el-table-column label="承运商" min-width="140">
          <template #default="{ row }">{{ carrierName(row.carrier_id) }}</template>
        </el-table-column>
        <el-table-column label="当前司机" min-width="120">
          <template #default="{ row }">{{ driverName(row.current_driver_id) }}</template>
        </el-table-column>
        <el-table-column label="当前城市" width="110">
          <template #default="{ row }">{{ row.current_city ?? '—' }}</template>
        </el-table-column>
        <el-table-column label="状态" width="110" align="center">
          <template #default="{ row }">
            <el-tag size="small" :type="vehicleStatusType(row.status)">{{ vehicleStatusLabel(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="90" fixed="right">
          <template #default="{ row }">
            <el-button v-if="canManage" size="small" text type="primary" @click="openEdit(row)">编辑</el-button>
            <span v-else class="u-text-muted">—</span>
          </template>
        </el-table-column>
      </el-table>

      <div class="u-text-muted u-mt-8">契约：GET|POST /vehicles?status&amp;carrier_id · GET|PATCH /vehicles/{id}</div>
    </PanelCard>

    <el-dialog v-model="dialogVisible" :title="editingId ? '编辑车辆' : '新增车辆'" width="520px">
      <el-form label-width="90px">
        <el-form-item label="车牌号"><el-input v-model="form.plate_no" /></el-form-item>
        <el-form-item label="车型"><el-input v-model="form.vehicle_type" /></el-form-item>
        <el-form-item label="载重(t)"><el-input-number v-model="form.capacity_ton" :min="0" :step="0.5" /></el-form-item>
        <el-form-item label="承运商">
          <el-select v-model="form.carrier_id" clearable style="width: 100%">
            <el-option v-for="carrier in carriers" :key="carrier.id" :label="carrier.name" :value="carrier.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="当前司机">
          <el-select v-model="form.current_driver_id" clearable style="width: 100%">
            <el-option v-for="driver in drivers" :key="driver.id" :label="driver.name" :value="driver.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="当前城市"><el-input v-model="form.current_city" /></el-form-item>
        <el-form-item label="状态">
          <el-select v-model="form.status" style="width: 100%">
            <el-option v-for="option in statusOptions" :key="option.value" :label="option.label" :value="option.value" />
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
