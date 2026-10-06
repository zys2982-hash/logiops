<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'

import PanelCard from '@/components/PanelCard.vue'
import RiskTag from '@/components/RiskTag.vue'
import TrackingTimeline from '@/components/TrackingTimeline.vue'
import { exceptionApi, masterApi, orderApi } from '@/api'
import { Perm } from '@/types'
import type {
  Carrier,
  Customer,
  Driver,
  ExceptionListItem,
  Order,
  SlaRule,
  TimelineIncident,
  TrackingEvent,
  Vehicle,
} from '@/types'
import { useAuthStore } from '@/stores/auth'
import { useDemoStore } from '@/stores/demo'
import { businessNowText, displayIsoToUtc, formatDateTime, formatDelay } from '@/utils/datetime'
import {
  customerLevelLabel,
  exceptionStatusLabel,
  orderStatusLabel,
  orderStatusType,
  TRACKING_EVENT_OPTIONS,
} from '@/utils/format'

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
const carriers = ref<Carrier[]>([])
const slaRules = ref<SlaRule[]>([])
const loading = ref(false)
const saving = ref(false)

const canManage = computed(() => auth.can(Perm.ORDER_MANAGE))
const canTrack = computed(() => auth.can(Perm.TRACKING_WRITE))
const canCreateException = computed(() => auth.can(Perm.EXCEPTION_CREATE))

/* ------------------------------------------------ 在途异常（订单页只负责"录入车辆故障"） */
const demo = useDemoStore()
/**
 * 订单页的定位（用户口径 2026-10-06）：**这里只能加异常，不能结束异常**。
 * - 只能录「车辆故障」：延误＝实际送达 − 承诺送达，按 SLA 规则在**送达时自动生成**，不能手工录；
 * - 结束 / 归档 / 车辆已修复 一律去「异常中心」那张单里做（本页不再提供）；
 * - 一个订单可以同时有「车辆故障 + 延误」两张独立异常单，但**同一类型只能有一张未关闭单**：
 *   后端 `find_open_by_order_and_type` 的"未关闭"含 RESOLVED（已解决未归档也占名额，CLOSED 才释放），
 *   所以这里的判断必须同口径，否则点了必然 409。
 */
const UNCLOSED_EXCEPTION_STATUSES = new Set(['DETECTED', 'CONFIRMING', 'ANALYZING', 'PROCESSING', 'RESOLVED'])
const unclosedExceptions = computed(() =>
  relatedExceptions.value.filter((item) => UNCLOSED_EXCEPTION_STATUSES.has(String(item.status))),
)
/** 占着"车辆故障"名额的未关闭单（含已解决未归档） */
const unclosedVehicleException = computed(
  () => unclosedExceptions.value.find((item) => String(item.type) === 'VEHICLE_BREAKDOWN') ?? null,
)
/** 该订单上未关闭的延误单：与车辆故障是两张独立单，可以并存，只是提示一下 */
const unclosedDelayException = computed(
  () => unclosedExceptions.value.find((item) => String(item.type) === 'DELAY_RISK') ?? null,
)
/** 能不能录车辆故障：有权限 + 已派车（车故障要挂在车上）+ 该类型没有未关闭单 */
const canCreateVehicleException = computed(
  () =>
    canCreateException.value &&
    Boolean(order.value?.vehicle_id) &&
    unclosedVehicleException.value === null,
)
const createBlockedReason = computed(() => {
  if (!order.value?.vehicle_id) {
    return '该订单还没派车（未绑定车辆）：车辆故障要挂在车上，请先派车再来录。'
  }
  if (unclosedVehicleException.value) {
    return `该订单已有未结束的车辆故障异常（${unclosedVehicleException.value.case_no} · ${exceptionStatusLabel(unclosedVehicleException.value.status)}）：同一类型只能有一张，请先去「异常中心」处理它。`
  }
  return ''
})

const exceptionForm = reactive({
  occurred_at: '',
  note: '',
})

/** 修正实际送达时间（订单已送达后；延误单只在送达后按实际时间判定） */
const deliverFixForm = reactive({
  delivered_at: '',
  note: '',
})
watch(
  () => order.value?.delivered_at,
  (value: string | null | undefined) => {
    deliverFixForm.delivered_at = value ? formatDateTime(value, 'YYYY-MM-DD HH:mm') : ''
  },
  { immediate: true },
)

async function correctDeliveredAt(): Promise<void> {
  if (!order.value) return
  const deliveredAt = displayIsoToUtc(deliverFixForm.delivered_at)
  if (!deliveredAt) {
    ElMessage.warning('请选择实际送达时间')
    return
  }
  saving.value = true
  try {
    await orderApi.correctDeliveredAt(order.value.id, {
      delivered_at: deliveredAt,
      note: deliverFixForm.note.trim() || '修正实际送达时间（订单页）',
    })
    ElMessage.success('实际送达时间已修正；若该订单有延误异常，延误与风险分已按新时间重算')
    deliverFixForm.note = ''
    await load()
  } catch {
    // 409（未送达）/403/422 已由响应拦截器提示
  } finally {
    saving.value = false
  }
}
const exceptionSaving = ref(false)

/** 时间线条目：把每个异常折算成"开始（+ 结束）"，交给时间线按时间混排 */
const timelineIncidents = computed<TimelineIncident[]>(() =>
  relatedExceptions.value.map((item) => ({
    id: item.id,
    case_no: item.case_no,
    type: item.type,
    currentType: item.current_type ?? item.type,
    level: item.level,
    status: item.status,
    startedAt: item.occurred_at,
    endedAt: item.resolved_at ?? item.closed_at ?? null,
    endedLabel: String(item.status) === 'CLOSED' ? '已关闭' : '已解决',
  })),
)

/** 派车三步级联：先选承运商（合同主体）→ 再选它名下的车与司机 */
const dispatchForm = reactive({
  carrier_id: null as number | null,
  vehicle_id: null as number | null,
  driver_id: null as number | null,
})

const dispatchVehicles = computed(() =>
  dispatchForm.carrier_id === null
    ? []
    : vehicles.value.filter((vehicle) => vehicle.carrier_id === dispatchForm.carrier_id),
)
const dispatchDrivers = computed(() =>
  dispatchForm.carrier_id === null
    ? []
    : drivers.value.filter((driver) => driver.carrier_id === dispatchForm.carrier_id),
)

function onCarrierChange(): void {
  // 换承运商必须清空下游，否则会出现"承运商 A + 承运商 B 的车"（后端也会 422 拦住）
  dispatchForm.vehicle_id = null
  dispatchForm.driver_id = null
}

/** 该司机固定绑定的车牌（用于下拉选项提示） */
function boundPlateOf(driverId: number | null): string {
  if (driverId === null) return ''
  return vehicles.value.find((vehicle) => vehicle.current_driver_id === driverId)?.plate_no ?? ''
}

/**
 * 强绑定（ADR-A18）：车与司机 1:1 固定绑定，所以"选司机 → 车辆自动导入"。
 * 反方向不需要再让用户选车辆：车辆下拉是只读的，避免出现"车 A + 司机 B"这种矛盾组合。
 */
function onDriverChange(): void {
  const bound = vehicles.value.find((vehicle) => vehicle.current_driver_id === dispatchForm.driver_id)
  dispatchForm.vehicle_id = bound?.id ?? null
}
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
  // 录入只开放 4 种事件（发车/到达/停靠/送达），默认发车
  event_type: 'DEPART',
  city: '',
  address: '',
  // 默认值在 onMounted 里按**业务时间**填充（syncFormsToBusinessTime），这里不放真实时间
  occurred_at: '',
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
    dispatchForm.carrier_id = order.value.carrier_id ?? null
    dispatchForm.vehicle_id = order.value.vehicle_id ?? null
    dispatchForm.driver_id = order.value.driver_id ?? null
  } finally {
    loading.value = false
  }
}

async function loadOptions(): Promise<void> {
  const [vehicleResult, customerResult, driverResult, carrierResult, slaResult] = await Promise.allSettled([
    masterApi.listVehicles({ page: 1, page_size: 100 }),
    masterApi.listCustomers({ page: 1, page_size: 100 }),
    masterApi.listDrivers({ page: 1, page_size: 100 }),
    masterApi.listCarriers({ page: 1, page_size: 100 }),
    orderApi.listSlaRules({ page: 1, page_size: 50 }),
  ])
  if (vehicleResult.status === 'fulfilled') vehicles.value = vehicleResult.value.items
  if (customerResult.status === 'fulfilled') customers.value = customerResult.value.items
  if (driverResult.status === 'fulfilled') drivers.value = driverResult.value.items
  if (carrierResult.status === 'fulfilled') carriers.value = carrierResult.value.items
  if (slaResult.status === 'fulfilled') slaRules.value = slaResult.value.items
}

async function dispatch(): Promise<void> {
  if (!order.value) return
  saving.value = true
  try {
    await orderApi.updateOrder(order.value.id, {
      expected_version: order.value.version,
      carrier_id: dispatchForm.carrier_id,
      vehicle_id: dispatchForm.vehicle_id,
      driver_id: dispatchForm.driver_id,
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
    const occurredAt = displayIsoToUtc(trackForm.occurred_at)
    if (!occurredAt) {
      ElMessage.warning('请选择发生时间')
      return
    }
    await orderApi.createTrackingEvent(orderId.value, {
      event_type: trackForm.event_type as TrackingEvent['event_type'],
      city: trackForm.city.trim(),
      address: trackForm.address.trim() || undefined,
      occurred_at: occurredAt,
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

async function createIncident(): Promise<void> {
  if (!order.value) return
  if (!order.value.vehicle_id) {
    ElMessage.warning('该订单还没派车（未绑定车辆），车辆故障要挂在车上')
    return
  }
  if (unclosedVehicleException.value) {
    ElMessage.warning(
      `该订单已有未结束的车辆故障异常（${unclosedVehicleException.value.case_no}），请去「异常中心」处理`,
    )
    return
  }
  if (!exceptionForm.occurred_at) {
    ElMessage.warning('请选择异常发生时间')
    return
  }
  if (!exceptionForm.note.trim()) {
    ElMessage.warning('请填写异常说明')
    return
  }
  exceptionSaving.value = true
  try {
    const occurredAt = displayIsoToUtc(exceptionForm.occurred_at)
    if (!occurredAt) {
      ElMessage.warning('请选择异常发生时间')
      return
    }
    await exceptionApi.createException({
      order_id: order.value.id,
      // 订单页只允许录车辆故障（2026-10-06 口径）：延误只能由"实际送达 − 承诺送达"按 SLA 规则
      // 在订单送达时自动生成，不能手工录。
      type: 'VEHICLE_BREAKDOWN',
      occurred_at: occurredAt,
      note: exceptionForm.note.trim(),
    })
    ElMessage.success('车辆故障异常已录入（待确认）；结束请去「异常中心」那张单里处理')
    exceptionForm.note = ''
    await load()
  } finally {
    exceptionSaving.value = false
  }
}

/** 两个表单的时间默认取**业务时间**（真实时间或演示时钟），不要用电脑真实时间 */
function syncFormsToBusinessTime(): void {
  const business = businessNowText(demo.businessNowUtc)
  if (!business) return
  trackForm.occurred_at = business
  exceptionForm.occurred_at = business
}

onMounted(async () => {
  if (!demo.businessTimeText) await demo.refresh()
  syncFormsToBusinessTime()
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
            </el-descriptions>
            <div v-if="order.remark" class="u-text-muted u-mt-8">备注：{{ order.remark }}</div>
          </PanelCard>

          <TrackingTimeline :events="tracking" :incidents="timelineIncidents" :loading="loading" />
        </el-col>

        <el-col :md="10">
          <PanelCard
            v-if="canManage && order.status === 'DELIVERED'"
            title="修正实际送达时间"
            subtitle="送达时间录错时用这里改正；延误异常只在送达后按实际时间判定，改完立即重算"
            icon="Timer"
            class="u-mb-12"
          >
            <el-form label-width="90px" size="small">
              <el-form-item label="实际送达">
                <el-date-picker
                  v-model="deliverFixForm.delivered_at"
                  type="datetime"
                  value-format="YYYY-MM-DD HH:mm"
                  style="width: 100%"
                />
              </el-form-item>
              <el-form-item label="修正说明">
                <el-input
                  v-model="deliverFixForm.note"
                  type="textarea"
                  :autosize="{ minRows: 2, maxRows: 4 }"
                  placeholder="例如：实际为 18:20 送达，之前按 20:05 录入"
                />
              </el-form-item>
              <el-form-item>
                <el-button type="primary" size="small" :loading="saving" @click="correctDeliveredAt">
                  保存并重算
                </el-button>
                <span class="u-text-muted">仍违约 → 重算延误与分数；不再违约 → 只重算，单子仍挂着等你点「关闭」</span>
              </el-form-item>
            </el-form>
          </PanelCard>

          <PanelCard
            v-if="canManage"
            title="派车操作"
            subtitle="先选承运商 → 再选司机；车辆由司机自动导入（车与司机是固定绑定）"
            icon="SetUp"
            class="u-mb-12"
          >
            <el-form label-width="80px" size="small">
              <el-form-item label="承运商">
                <el-select
                  v-model="dispatchForm.carrier_id"
                  clearable
                  filterable
                  placeholder="① 先选承运商"
                  style="width: 100%"
                  @change="onCarrierChange"
                >
                  <el-option
                    v-for="carrier in carriers"
                    :key="carrier.id"
                    :label="`${carrier.name}（${carrier.code}）`"
                    :value="carrier.id"
                  />
                </el-select>
              </el-form-item>
              <el-form-item label="司机">
                <el-select
                  v-model="dispatchForm.driver_id"
                  clearable
                  filterable
                  :disabled="dispatchForm.carrier_id === null"
                  :placeholder="dispatchForm.carrier_id === null ? '请先选择承运商' : '② 选司机（括注为其固定车辆）'"
                  style="width: 100%"
                  @change="onDriverChange"
                >
                  <el-option
                    v-for="driver in dispatchDrivers"
                    :key="driver.id"
                    :label="`${driver.name}（${driver.status}${boundPlateOf(driver.id) ? ' · ' + boundPlateOf(driver.id) : ' · 未绑定车辆'}）`"
                    :value="driver.id"
                  />
                </el-select>
              </el-form-item>
              <el-form-item label="车辆">
                <el-select
                  v-model="dispatchForm.vehicle_id"
                  disabled
                  :placeholder="dispatchForm.driver_id === null ? '选择司机后自动带入' : '该司机未绑定车辆'"
                  style="width: 100%"
                >
                  <el-option
                    v-for="vehicle in dispatchVehicles"
                    :key="vehicle.id"
                    :label="`${vehicle.plate_no}（${vehicle.status}${vehicle.current_city ? ' · ' + vehicle.current_city : ''}）`"
                    :value="vehicle.id"
                  />
                </el-select>
                <span v-if="dispatchForm.driver_id !== null && dispatchForm.vehicle_id === null" class="u-text-warn">
                  该司机没有绑定车辆：请先到「车辆」页把车辆绑定给他，再进行派车
                </span>
              </el-form-item>
              <el-form-item>
                <el-button
                  type="primary"
                  size="small"
                  :loading="saving"
                  :disabled="dispatchForm.vehicle_id === null"
                  @click="dispatch"
                >
                  保存派车
                </el-button>
                <span class="u-text-muted">
                  车与司机是固定绑定（ADR-A18）：选司机 → 车辆自动导入；后端同样强制，换人需先改绑定
                </span>
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
                <el-date-picker
                  v-model="trackForm.occurred_at"
                  type="datetime"
                  value-format="YYYY-MM-DD HH:mm"
                  style="width: 100%"
                />
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

          <PanelCard
            v-if="canCreateException"
            title="在途异常（只录车辆故障）"
            subtitle="延误异常由订单送达时按 SLA 自动生成；结束 / 归档请去「异常中心」"
            icon="Warning"
            class="u-mb-12"
          >
            <div class="form-section-title u-mb-8">录入异常</div>
            <el-alert
              type="info"
              :closable="false"
              show-icon
              class="u-mb-8"
              title="订单页只能录「车辆故障」"
              description="延误＝实际送达 − 承诺送达，按 SLA 规则在订单点「送达」时自动判定并建单，所以不能手工录。同一订单可以同时有「车辆故障 + 延误」两张独立异常单，各管一个问题。"
            />
            <el-alert
              v-if="unclosedVehicleException"
              type="warning"
              :closable="false"
              show-icon
              class="u-mb-8"
              :title="`该订单已有未结束的车辆故障异常（${unclosedVehicleException.case_no} · ${exceptionStatusLabel(unclosedVehicleException.status)}）`"
              description="同一订单同一类型只能有一张：请去「异常中心」打开这张单，用里面的「解决 / 关闭」结束它，这里才能再录车辆故障。"
            />
            <el-alert
              v-else-if="unclosedDelayException"
              type="info"
              :closable="false"
              show-icon
              class="u-mb-8"
              :title="`该订单另有一张未结束的延误异常（${unclosedDelayException.case_no}）`"
              description="车辆故障与延误是两张独立异常单，可以并存：车辆故障照录；那张延误单请去「异常中心」处理。"
            />
            <el-alert
              v-else-if="order && !order.vehicle_id"
              type="warning"
              :closable="false"
              show-icon
              class="u-mb-8"
              title="该订单还没派车（未绑定车辆）"
              description="车辆故障要挂在车上：先在本页派车（选承运商 / 车辆 / 司机），再来录异常。"
            />
            <el-form label-width="80px" size="small">
              <el-form-item label="发生时间">
                <el-date-picker
                  v-model="exceptionForm.occurred_at"
                  type="datetime"
                  value-format="YYYY-MM-DD HH:mm"
                  style="width: 100%"
                />
              </el-form-item>
              <el-form-item label="说明">
                <el-input
                  v-model="exceptionForm.note"
                  type="textarea"
                  :autosize="{ minRows: 2, maxRows: 4 }"
                  placeholder="例如：右后轮胎压异常，已在服务区处理"
                />
              </el-form-item>
              <el-form-item>
                <el-button
                  type="danger"
                  plain
                  size="small"
                  :loading="exceptionSaving"
                  :disabled="!canCreateVehicleException"
                  @click="createIncident"
                >
                  录入车辆故障
                </el-button>
                <el-button link type="primary" size="small" @click="router.push('/exceptions')">
                  去异常中心处理 / 结束 →
                </el-button>
                <span v-if="createBlockedReason" class="u-text-muted">{{ createBlockedReason }}</span>
              </el-form-item>
            </el-form>
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
.form-section-title {
  font-weight: 600;
  font-size: 13px;
}
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
