<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'

import PanelCard from '@/components/PanelCard.vue'
import RiskTag from '@/components/RiskTag.vue'
import TrackingTimeline from '@/components/TrackingTimeline.vue'
import { masterApi, orderApi } from '@/api'
import { Perm } from '@/types'
import type {
  Customer,
  Driver,
  ExceptionListItem,
  Order,
  SlaRule,
  TrackingEvent,
  Vehicle,
} from '@/types'
import { useAuthStore } from '@/stores/auth'
import { formatDateTime, formatDelay } from '@/utils/datetime'
import { customerLevelLabel, orderStatusLabel, orderStatusType, TRACKING_EVENT_OPTIONS } from '@/utils/format'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const orderId = computed(() => Number(route.params.id))
const order = ref<Order | null>(null)
const tracking = ref<TrackingEvent[]>([])
const relatedExceptions = ref<ExceptionListItem[]>([])
const vehicles = ref<Vehicle[]>([])
const customers = ref<Customer[]>([])
const drivers = ref<Driver[]>([])
const slaRules = ref<SlaRule[]>([])
const loading = ref(false)
const saving = ref(false)

const canManage = computed(() => auth.can(Perm.ORDER_MANAGE))
const canTrack = computed(() => auth.can(Perm.TRACKING_WRITE))

const dispatchForm = reactive({ vehicle_id: null as number | null, carrier_id: null as number | null })
/** 实测订单是扁平字段 + 内嵌 sla 快照，优先用后端返回的 sla.rule_name */
const slaRuleName = computed(
  () =>
    order.value?.sla?.rule_name ??
    slaRules.value.find((rule) => rule.id === order.value?.sla_rule_id)?.name ??
    '—',
)
const customerName = computed(
  () =>
    order.value?.customer_name ??
    customers.value.find((c) => c.id === order.value?.customer_id)?.name ??
    '—',
)

const trackForm = reactive({
  event_type: 'NOTE',
  city: '',
  address: '',
  occurred_at: new Date().toISOString().slice(0, 16),
  source: 'OPERATOR',
})

async function load(): Promise<void> {
  loading.value = true
  try {
    order.value = await orderApi.getOrder(orderId.value)
    const [trackingResult, exceptionResult] = await Promise.allSettled([
      orderApi.listTrackingEvents(orderId.value),
      orderApi.listOrderExceptions(orderId.value),
    ])
    if (trackingResult.status === 'fulfilled') tracking.value = trackingResult.value
    if (exceptionResult.status === 'fulfilled') relatedExceptions.value = exceptionResult.value
    dispatchForm.vehicle_id = order.value.vehicle_id ?? null
    dispatchForm.carrier_id = order.value.carrier_id ?? null
  } finally {
    loading.value = false
  }
}

async function loadOptions(): Promise<void> {
  const [vehicleResult, customerResult, driverResult, slaResult] = await Promise.allSettled([
    masterApi.listVehicles({ page: 1, page_size: 100 }),
    masterApi.listCustomers({ page: 1, page_size: 100 }),
    masterApi.listDrivers({ page: 1, page_size: 100 }),
    orderApi.listSlaRules({ page: 1, page_size: 50 }),
  ])
  if (vehicleResult.status === 'fulfilled') vehicles.value = vehicleResult.value.items
  if (customerResult.status === 'fulfilled') customers.value = customerResult.value.items
  if (driverResult.status === 'fulfilled') drivers.value = driverResult.value.items
  if (slaResult.status === 'fulfilled') slaRules.value = slaResult.value.items
}

async function dispatch(): Promise<void> {
  if (!order.value) return
  saving.value = true
  try {
    await orderApi.updateOrder(order.value.id, {
      expected_version: order.value.version,
      vehicle_id: dispatchForm.vehicle_id,
      carrier_id: dispatchForm.carrier_id,
    })
    ElMessage.success('派车信息已更新（按订单状态机流转，后端重算承诺到达）')
    await load()
  } catch {
    // 409 已由拦截器提示
    await load()
  } finally {
    saving.value = false
  }
}

async function createEvent(): Promise<void> {
  if (!trackForm.city.trim()) {
    ElMessage.warning('请输入城市')
    return
  }
  saving.value = true
  try {
    await orderApi.createTrackingEvent(orderId.value, {
      event_type: trackForm.event_type as TrackingEvent['event_type'],
      city: trackForm.city.trim(),
      address: trackForm.address.trim() || undefined,
      occurred_at: new Date(trackForm.occurred_at).toISOString(),
      source: trackForm.source as TrackingEvent['source'],
    })
    ElMessage.success('轨迹已写入：同步触发 ETA 重算 → 异常检测')
    trackForm.city = ''
    trackForm.address = ''
    await load()
  } finally {
    saving.value = false
  }
}

onMounted(async () => {
  await Promise.all([load(), loadOptions()])
})
</script>

<template>
  <div class="page" v-loading="loading">
    <el-empty v-if="!order && !loading" description="订单不存在或无权访问">
      <el-button @click="router.push('/orders')">返回订单列表</el-button>
    </el-empty>

    <template v-if="order">
      <el-card shadow="never" class="page-card u-mb-12">
        <div class="detail-head">
          <div class="detail-head-main">
            <h3>{{ order.order_no }}</h3>
            <el-tag :type="orderStatusType(order.status)">{{ orderStatusLabel(order.status) }}</el-tag>
            <el-tag effect="plain">{{ order.origin_city }} → {{ order.dest_city }}</el-tag>
            <span class="u-text-muted">客户：{{ customerName }}</span>
            <span class="u-text-muted">承运商：{{ order.carrier_name ?? '—' }}</span>
            <span class="u-text-muted u-mono">v{{ order.version }}</span>
          </div>
          <el-button size="small" text @click="load">刷新</el-button>
        </div>
      </el-card>

      <el-row :gutter="12">
        <el-col :md="14">
          <PanelCard title="订单信息与 SLA 快照" icon="Document" class="u-mb-12">
            <el-descriptions :column="2" size="small" border>
              <el-descriptions-item label="客户等级">{{ customerLevelLabel(order.customer_level ?? null) }}</el-descriptions-item>
              <el-descriptions-item label="货描">{{ order.cargo_desc ?? '—' }}</el-descriptions-item>
              <el-descriptions-item label="重量">{{ order.weight_ton ?? '—' }} t</el-descriptions-item>
              <el-descriptions-item label="里程">{{ order.distance_km ?? '—' }} km</el-descriptions-item>
              <el-descriptions-item label="车辆">{{ order.vehicle_plate ?? '未派车' }}</el-descriptions-item>
              <el-descriptions-item label="司机">{{ order.driver_name ?? '未指派' }}</el-descriptions-item>
              <el-descriptions-item label="发车时间">{{ formatDateTime(order.dispatched_at) }}</el-descriptions-item>
              <el-descriptions-item label="送达时间">{{ formatDateTime(order.delivered_at) }}</el-descriptions-item>
              <el-descriptions-item label="承诺到达（SLA 快照）">{{ formatDateTime(order.promised_delivery_at) }}</el-descriptions-item>
              <el-descriptions-item label="SLA 规则">{{ slaRuleName }}</el-descriptions-item>
              <el-descriptions-item label="首次 ETA">{{ formatDateTime(order.original_eta_at) }}</el-descriptions-item>
              <el-descriptions-item label="当前 ETA">{{ formatDateTime(order.current_eta_at) }}</el-descriptions-item>
            </el-descriptions>
            <div v-if="order.remark" class="u-text-muted u-mt-8">备注：{{ order.remark }}</div>
          </PanelCard>

          <TrackingTimeline :events="tracking" :loading="loading" />
        </el-col>

        <el-col :md="10">
          <PanelCard v-if="canManage" title="派车操作" subtitle="PATCH /orders/{id}（填 vehicle 按状态机流转）" icon="SetUp" class="u-mb-12">
            <el-form label-width="80px" size="small">
              <el-form-item label="车辆">
                <el-select v-model="dispatchForm.vehicle_id" clearable filterable style="width: 100%">
                  <el-option
                    v-for="vehicle in vehicles"
                    :key="vehicle.id"
                    :label="`${vehicle.plate_no}（${vehicle.status}）`"
                    :value="vehicle.id"
                  />
                </el-select>
              </el-form-item>
              <el-form-item label="承运商">
                <el-select v-model="dispatchForm.carrier_id" clearable filterable style="width: 100%">
                  <el-option v-for="vehicle in vehicles.filter((v) => v.carrier_id)" :key="`c-${vehicle.carrier_id}`" :label="`承运商 #${vehicle.carrier_id}`" :value="vehicle.carrier_id as number" />
                </el-select>
              </el-form-item>
              <el-form-item>
                <el-button type="primary" size="small" :loading="saving" @click="dispatch">保存派车</el-button>
                <span class="u-text-muted">写操作带 expected_version，409 提示“已被他人更新”</span>
              </el-form-item>
            </el-form>
          </PanelCard>

          <PanelCard v-if="canTrack" title="录入轨迹" subtitle="POST /orders/{id}/tracking-events" icon="Position" class="u-mb-12">
            <el-form label-width="80px" size="small">
              <el-form-item label="事件">
                <el-select v-model="trackForm.event_type" style="width: 100%">
                  <el-option v-for="option in TRACKING_EVENT_OPTIONS" :key="option.value" :label="option.label" :value="option.value" />
                </el-select>
              </el-form-item>
              <el-form-item label="城市">
                <el-input v-model="trackForm.city" placeholder="济南" />
              </el-form-item>
              <el-form-item label="地点">
                <el-input v-model="trackForm.address" placeholder="京沪高速济南东服务区" />
              </el-form-item>
              <el-form-item label="发生时间">
                <el-date-picker v-model="trackForm.occurred_at" type="datetime" style="width: 100%" />
              </el-form-item>
              <el-form-item>
                <el-button type="primary" size="small" :loading="saving" @click="createEvent">写入轨迹</el-button>
              </el-form-item>
            </el-form>
            <el-alert
              type="info"
              :closable="false"
              title="写入后同步执行：ETA 重算 → 异常检测（§8.4），可能自动建单"
            />
          </PanelCard>

          <PanelCard v-if="!canManage && !canTrack" title="只读视图" icon="View" class="u-mb-12">
            <div class="u-text-muted">
              当前角色没有 order.manage / tracking.write 权限，派车与录入轨迹按钮不渲染（§9.2 权限矩阵）。
            </div>
          </PanelCard>

          <PanelCard title="关联异常" :subtitle="`${relatedExceptions.length} 条`" icon="Warning">
            <el-empty v-if="relatedExceptions.length === 0" description="该订单暂无异常" :image-size="50" />
            <div v-for="item in relatedExceptions" :key="item.id" class="related-item u-clickable" @click="router.push(`/exceptions/${item.id}`)">
              <b>{{ item.case_no }}</b>
              <RiskTag :level="item.level" :score="item.risk_score" />
              <span class="u-text-muted">{{ formatDelay(item.sla_delay_minutes) }}</span>
            </div>
          </PanelCard>
        </el-col>
      </el-row>
    </template>
  </div>
</template>

<style scoped>
.detail-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}

.detail-head-main {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.detail-head-main h3 {
  margin: 0;
}

.related-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px;
  border-bottom: 1px dashed #ebeef5;
}

.related-item:hover {
  background: #f5f7fa;
}
</style>
