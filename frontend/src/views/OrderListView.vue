<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, type FormInstance, type FormRules } from 'element-plus'

import PanelCard from '@/components/PanelCard.vue'
import { masterApi, orderApi } from '@/api'
import type { Customer, OrderBrief, OrderStatus, Page } from '@/types'
import { Perm } from '@/types'
import { useAuthStore } from '@/stores/auth'
import { formatDateTime } from '@/utils/datetime'
import { ORDER_STATUS_OPTIONS, formatNumber, orderStatusLabel, orderStatusType } from '@/utils/format'

const router = useRouter()
const auth = useAuthStore()

const query = reactive({
  order_no: '',
  status: '' as OrderStatus | '',
  customer_id: '' as number | '',
  page: 1,
  page_size: 20,
  sort: '-created_at',
})

const result = ref<Page<OrderBrief>>({ items: [], total: 0, page: 1, page_size: 20 })
const customers = ref<Customer[]>([])
const loading = ref(false)

const canManage = computed(() => auth.can(Perm.ORDER_MANAGE))

/* ------------------------------------------------------------- 新建订单 */
const createVisible = ref(false)
const creating = ref(false)
const formRef = ref<FormInstance>()
const form = reactive({
  customer_id: '' as number | '',
  origin_city: '',
  dest_city: '',
  order_no: '',
  cargo_desc: '',
  weight_ton: undefined as number | undefined,
  distance_km: undefined as number | undefined,
  remark: '',
})

/** 承诺到达时间**不让填**：派车后由 SLA 规则算（发车时间 + deadline_offset_hours），见 §8.3 */
const formRules: FormRules = {
  customer_id: [{ required: true, message: '请选择客户', trigger: 'change' }],
  origin_city: [{ required: true, message: '请填写起运地', trigger: 'blur' }],
  dest_city: [{ required: true, message: '请填写目的地', trigger: 'blur' }],
}

function openCreate(): void {
  form.customer_id = ''
  form.origin_city = ''
  form.dest_city = ''
  form.order_no = ''
  form.cargo_desc = ''
  form.weight_ton = undefined
  form.distance_km = undefined
  form.remark = ''
  createVisible.value = true
}

async function submitCreate(): Promise<void> {
  if (!formRef.value) return
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return
  creating.value = true
  try {
    const order = await orderApi.createOrder({
      customer_id: Number(form.customer_id),
      origin_city: form.origin_city.trim(),
      dest_city: form.dest_city.trim(),
      order_no: form.order_no.trim() || undefined,
      cargo_desc: form.cargo_desc.trim() || undefined,
      weight_ton: form.weight_ton ?? undefined,
      distance_km: form.distance_km ?? undefined,
      remark: form.remark.trim() || undefined,
    })
    ElMessage.success(`订单 ${order.order_no} 已创建（待发车）；在订单详情里派车后，系统按 SLA 规则算出承诺到达时间`)
    createVisible.value = false
    query.page = 1
    await load()
  } finally {
    creating.value = false
  }
}

async function load(): Promise<void> {
  loading.value = true
  try {
    result.value = await orderApi.listOrders({
      order_no: query.order_no || undefined,
      status: query.status || undefined,
      customer_id: query.customer_id === '' ? undefined : Number(query.customer_id),
      sort: query.sort,
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

function onPageChange(page: number): void {
  query.page = page
  void load()
}

function onSizeChange(size: number): void {
  query.page_size = size
  query.page = 1
  void load()
}

onMounted(async () => {
  await load()
  try {
    const page = await masterApi.listCustomers({ page: 1, page_size: 100 })
    customers.value = page.items
  } catch {
    customers.value = []
  }
})
</script>

<template>
  <div class="page">
    <PanelCard title="运输订单" :subtitle="`共 ${result.total} 单`" icon="Van">
      <template #actions>
        <el-tag v-if="!canManage" size="small" effect="plain">只读（order.manage 才可建单/改派）</el-tag>
        <el-button v-if="canManage" size="small" type="primary" @click="openCreate">新建订单</el-button>
        <el-button size="small" :loading="loading" @click="load">刷新</el-button>
      </template>

      <el-form inline class="u-mb-8" @submit.prevent="search">
        <el-form-item label="订单号">
          <el-input v-model="query.order_no" placeholder="SO20260930021" clearable style="width: 190px" />
        </el-form-item>
        <el-form-item label="状态">
          <el-select v-model="query.status" clearable placeholder="全部" style="width: 140px" @change="search">
            <el-option v-for="option in ORDER_STATUS_OPTIONS" :key="option.value" :label="option.label" :value="option.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="客户">
          <el-select v-model="query.customer_id" clearable filterable placeholder="全部" style="width: 180px" @change="search">
            <el-option v-for="customer in customers" :key="customer.id" :label="customer.name" :value="customer.id" />
          </el-select>
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="search">查询</el-button>
        </el-form-item>
      </el-form>

      <el-table :data="result.items" v-loading="loading" size="small" border stripe
        @row-click="(row: OrderBrief) => router.push(`/orders/${row.id}`)">
        <el-table-column prop="order_no" label="订单号" min-width="150">
          <template #default="{ row }">
            <b>{{ row.order_no }}</b>
          </template>
        </el-table-column>
        <el-table-column label="客户" min-width="150">
          <template #default="{ row }">{{ row.customer_name ?? '—' }}</template>
        </el-table-column>
        <el-table-column label="线路" min-width="150">
          <template #default="{ row }">{{ row.origin_city ?? '—' }} → {{ row.dest_city ?? '—' }}</template>
        </el-table-column>
        <el-table-column label="状态" width="110" align="center">
          <template #default="{ row }">
            <el-tag size="small" :type="orderStatusType(row.status)">{{ orderStatusLabel(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="承诺到达" width="150">
          <template #default="{ row }">{{ formatDateTime(row.promised_delivery_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="90" fixed="right">
          <template #default="{ row }">
            <el-button size="small" text type="primary" @click.stop="router.push(`/orders/${row.id}`)">详情</el-button>
          </template>
        </el-table-column>
      </el-table>

      <div class="table-foot">
        <el-pagination
          :current-page="result.page"
          :page-size="result.page_size"
          :total="result.total"
          :page-sizes="[10, 20, 50, 100]"
          layout="total, sizes, prev, pager, next, jumper"
          background
          @current-change="onPageChange"
          @size-change="onSizeChange"
        />
      </div>
      <div class="u-text-muted u-mt-8">
        共 {{ formatNumber(result.total) }} 条 · 建单后为「待发车」，派车（填承运商/车辆）会把状态推进到「已发车」
      </div>
    </PanelCard>

    <el-dialog v-model="createVisible" title="新建运输订单" width="560px">
      <el-form ref="formRef" :model="form" :rules="formRules" label-width="96px">
        <el-form-item label="客户" prop="customer_id">
          <el-select v-model="form.customer_id" filterable placeholder="请选择客户（决定 SLA 规则）" style="width: 100%">
            <el-option
              v-for="customer in customers"
              :key="customer.id"
              :label="`${customer.name}（${customer.code}）`"
              :value="customer.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="起运地" prop="origin_city">
          <el-input v-model="form.origin_city" placeholder="例如：天津" maxlength="64" />
        </el-form-item>
        <el-form-item label="目的地" prop="dest_city">
          <el-input v-model="form.dest_city" placeholder="例如：上海" maxlength="64" />
        </el-form-item>
        <el-form-item label="订单号">
          <el-input v-model="form.order_no" placeholder="留空自动生成（SO20260930XXX）" maxlength="32" />
        </el-form-item>
        <el-form-item label="货物">
          <el-input v-model="form.cargo_desc" placeholder="例如：冷鲜食品" maxlength="128" />
        </el-form-item>
        <el-form-item label="重量/里程">
          <el-input-number v-model="form.weight_ton" :min="0" :precision="1" :controls="false" placeholder="重量(吨)" style="width: 120px" />
          <el-input-number v-model="form.distance_km" :min="1" :controls="false" placeholder="里程(km)" style="width: 120px; margin-left: 8px" />
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="form.remark" type="textarea" :autosize="{ minRows: 2, maxRows: 4 }" maxlength="255" />
        </el-form-item>
        <el-alert
          type="info"
          :closable="false"
          show-icon
          title="承诺到达时间不用填"
          description="订单创建后是「待发车」；派车时系统按匹配到的 SLA 规则（发车时间 + 规则偏移）算出承诺到达时间，并据此判定是否违约（§8.3）。"
        />
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" :loading="creating" @click="submitCreate">创建</el-button>
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
