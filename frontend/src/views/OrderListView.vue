<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'

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
        <el-tag v-if="!canManage" size="small" effect="plain">只读（order.manage 才可改派）</el-tag>
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
        <el-table-column label="当前 ETA" width="150">
          <template #default="{ row }">{{ formatDateTime(row.current_eta_at) }}</template>
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
        契约：GET /orders?status&amp;customer_id&amp;order_no&amp;created_from&amp;created_to&amp;sort&amp;page ·
        共 {{ formatNumber(result.total) }} 条
      </div>
    </PanelCard>
  </div>
</template>

<style scoped>
.table-foot {
  display: flex;
  justify-content: flex-end;
  margin-top: 12px;
}
</style>
