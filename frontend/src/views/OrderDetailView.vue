<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
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
  exceptionTypeLabel,
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
const canHandleException = computed(() => auth.can(Perm.EXCEPTION_HANDLE))

/* ------------------------------------------------ 在途异常（录入 / 结束） */
const demo = useDemoStore()
/**
 * 两个口径必须分开（踩过坑）：
 * - **后端"未关闭"**含 RESOLVED：同一订单只允许一个未关闭异常（DETECTED/PROCESSING/RESOLVED，
 *   旧数据的 CONFIRMING/ANALYZING 也算未关闭）
 *   → 决定"能不能录入新异常"，UI 必须与后端一致，否则点了必然 409
 * - **展示口径**（用户要求）：只有 RESOLVED/CLOSED 才算"已结束"→ 时间线灰点
 */
const UNCLOSED_EXCEPTION_STATUSES = new Set(['DETECTED', 'CONFIRMING', 'ANALYZING', 'PROCESSING', 'RESOLVED'])
const unclosedExceptions = computed(() =>
  relatedExceptions.value.filter((item) => UNCLOSED_EXCEPTION_STATUSES.has(String(item.status))),
)
/** 还能"结束（解决）"的：非 RESOLVED/CLOSED */
const openExceptions = computed(() =>
  relatedExceptions.value.filter(
    (item) => !['RESOLVED', 'CLOSED'].includes(String(item.status)),
  ),
)
/** 已解决但尚未归档的：只能"归档（关闭）"，关掉后该订单才能再录入新异常 */
const resolvedExceptions = computed(() =>
  relatedExceptions.value.filter((item) => String(item.status) === 'RESOLVED'),
)
/** 可做"车辆已修复"（信号级闭环）的：未结束的车辆故障异常 —— 只去掉车辆故障那 1 分，异常继续 */
const breakdownExceptions = computed(() =>
  openExceptions.value.filter((item) => String(item.type) === 'VEHICLE_BREAKDOWN'),
)

const exceptionForm = reactive({
  occurred_at: '',
  note: '',
})
const resolveForm = reactive({
  exception_id: null as number | null,
  note: '',
})
const archiveForm = reactive({
  exception_id: null as number | null,
  note: '',
})
const clearIssueForm = reactive({
  exception_id: null as number | null,
  note: '',
})
const exceptionSaving = ref(false)

/** 时间线条目：把每个异常折算成"开始（+ 结束）"，交给时间线按时间混排 */
const timelineIncidents = computed<TimelineIncident[]>(() =>
  relatedExceptions.value.map((item) => ({
    id: item.id,
    case_no: item.case_no,
    type: item.type,
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
      type: 'VEHICLE_BREAKDOWN', // 订单页只手工录「车辆故障」；延误风险由系统检测 / 延误录入产生
      occurred_at: occurredAt,
      note: exceptionForm.note.trim(),
    })
    ElMessage.success('异常已录入（待确认）；可在异常详情里继续「确认 → AI 分析」')
    exceptionForm.note = ''
    await load()
  } finally {
    exceptionSaving.value = false
  }
}

async function endIncident(): Promise<void> {
  if (!resolveForm.exception_id) {
    ElMessage.warning('请选择要结束的异常')
    return
  }
  if (!resolveForm.note.trim()) {
    ElMessage.warning('请填写结束说明')
    return
  }
  const target = openExceptions.value.find((item) => item.id === resolveForm.exception_id)
  if (!target || typeof target.version !== 'number') {
    ElMessage.warning('该异常的版本信息缺失，请刷新页面后重试')
    return
  }
  exceptionSaving.value = true
  try {
    // 状态机决定合法出口：只有「处理中」能 → 已解决；其余状态（待确认/确认中/分析中）
    // 只能 → 已关闭。两者在时间线上都算"已结束"（灰点），所以按钮文案统一叫"结束异常"。
    const canResolve = String(target.status) === 'PROCESSING'
    if (canResolve) {
      await exceptionApi.resolveException(resolveForm.exception_id, {
        note: resolveForm.note.trim(),
        expected_version: target.version,
      })
      ElMessage.success('异常已结束（已解决）')
    } else {
      await exceptionApi.closeException(resolveForm.exception_id, {
        reason_code: 'MANUAL',
        note: resolveForm.note.trim(),
        expected_version: target.version,
      })
      ElMessage.success(`异常已结束（由${exceptionStatusLabel(target.status)}直接归档为已关闭）`)
    }
    resolveForm.exception_id = null
    resolveForm.note = ''
  } catch {
    // 409（状态机/版本冲突）/422 已由响应拦截器提示；这里刷新拿最新状态
  } finally {
    exceptionSaving.value = false
    await load()
  }
}

/**
 * 车辆已修复（信号级闭环）：只解除这张单上的「车辆故障」问题（车辆状态恢复、该因子不再计分），
 * **异常单继续存在**。一键生效、说明可选（用户反馈："订单那里的异常修复了以后，直接把那 1 分移除就行"）。
 * 另：在订单时间线录「维修完成（REPAIR_END）」也会把车辆改回在途 —— 那样因子会在读取时**自动移除**。
 */
async function clearVehicleIssue(): Promise<void> {
  if (!clearIssueForm.exception_id) {
    ElMessage.warning('请选择要解除车辆故障的异常')
    return
  }
  const target = breakdownExceptions.value.find((item) => item.id === clearIssueForm.exception_id)
  if (!target || typeof target.version !== 'number') {
    ElMessage.warning('该异常的版本信息缺失，请刷新页面后重试')
    return
  }
  exceptionSaving.value = true
  try {
    await exceptionApi.clearVehicleIssue(clearIssueForm.exception_id, {
      note: clearIssueForm.note.trim() || '车辆已修复（订单页一键解除车辆故障问题）',
      expected_version: target.version,
    })
    ElMessage.success('已解除「车辆故障」问题，异常继续跟进（分数已按现状重算）')
    clearIssueForm.exception_id = null
    clearIssueForm.note = ''
  } catch {
    // 409（终态/非车辆故障/版本冲突）已由响应拦截器提示；这里刷新拿最新状态
  } finally {
    exceptionSaving.value = false
    await load()
  }
}

/** 归档（关闭）已解决的异常：CLOSED 是终态，关掉后该订单才能再录入新异常 */
async function archiveIncident(): Promise<void> {
  if (!archiveForm.exception_id) {
    ElMessage.warning('请选择要归档的异常')
    return
  }
  const target = resolvedExceptions.value.find((item) => item.id === archiveForm.exception_id)
  if (!target || typeof target.version !== 'number') {
    ElMessage.warning('该异常的版本信息缺失，请刷新页面后重试')
    return
  }
  exceptionSaving.value = true
  try {
    await exceptionApi.closeException(archiveForm.exception_id, {
      reason_code: 'MANUAL',
      note: archiveForm.note.trim() || '问题已处理，归档该异常',
      expected_version: target.version,
    })
    ElMessage.success('异常已归档（已关闭）')
    archiveForm.exception_id = null
    archiveForm.note = ''
  } catch {
    // 409/422 已由拦截器提示
  } finally {
    exceptionSaving.value = false
    await load()
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
            v-if="canCreateException || canHandleException"
            title="在途异常"
            subtitle="路上出问题就记一笔；问题消除后点「结束异常」"
            icon="Warning"
            class="u-mb-12"
          >
            <template v-if="canCreateException">
              <div class="form-section-title u-mb-8">录入异常（车辆故障）</div>
              <el-alert
                v-if="unclosedExceptions.length > 0"
                type="warning"
                :closable="false"
                show-icon
                class="u-mb-8"
                :title="`该订单已有未关闭的异常（${unclosedExceptions[0]?.case_no} · ${exceptionStatusLabel(unclosedExceptions[0]?.status)}）`"
                description="同一订单同时只能有一个未关闭异常：请先在下方「结束异常」把它解决；已解决但未归档的，再点「归档异常」关闭它，之后才能录入新的。"
              />
              <el-form label-width="80px" size="small">
                <el-form-item label="类型">
                  <el-tag type="danger" size="small">车辆故障</el-tag>
                  <span class="u-text-muted" style="margin-left: 8px">
                    订单页只手工录「车辆故障」；延误风险不手工建单 —— 由系统按轨迹 / ETA 自动检测，
                    或在异常单的 SLA 卡里「录入延误」（人报事实，是否违约由规则判）
                  </span>
                </el-form-item>
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
                    :disabled="unclosedExceptions.length > 0"
                    @click="createIncident"
                  >
                    录入车辆故障异常
                  </el-button>
                  <span class="u-text-muted">等级由规则算，不用选</span>
                </el-form-item>
              </el-form>
            </template>

            <template v-if="canHandleException">
              <el-divider content-position="left">结束异常</el-divider>
              <el-empty v-if="openExceptions.length === 0" description="当前没有未结束的异常" :image-size="40" />
              <el-form v-else label-width="80px" size="small">
                <el-form-item label="选择异常">
                  <el-select v-model="resolveForm.exception_id" style="width: 100%" placeholder="选择未结束的异常">
                    <el-option
                      v-for="item in openExceptions"
                      :key="item.id"
                      :label="`${item.case_no}　${exceptionTypeLabel(item.type)}　${exceptionStatusLabel(item.status)}`"
                      :value="item.id"
                    />
                  </el-select>
                </el-form-item>
                <el-form-item label="结束说明">
                  <el-input
                    v-model="resolveForm.note"
                    type="textarea"
                    :autosize="{ minRows: 2, maxRows: 4 }"
                    placeholder="例如：轮胎已更换，恢复正常行驶"
                  />
                </el-form-item>
                <el-form-item>
                  <el-button type="primary" size="small" :loading="exceptionSaving" @click="endIncident">
                    结束异常
                  </el-button>
                  <span class="u-text-muted">处理中→已解决；其余状态→直接归档为已关闭（都算已结束，时间线变灰点）</span>
                </el-form-item>
              </el-form>
            </template>

            <!-- 车辆已修复：只解除车辆故障问题（信号级闭环），异常单继续 —— 与上面「结束异常」不同 -->
            <template v-if="canHandleException && breakdownExceptions.length > 0">
              <el-divider content-position="left">车辆已修复（只解除车辆故障，不结束异常）</el-divider>
              <el-form label-width="80px" size="small">
                <el-form-item label="选择异常">
                  <el-select v-model="clearIssueForm.exception_id" style="width: 100%" placeholder="选择未结束的车辆故障异常">
                    <el-option
                      v-for="item in breakdownExceptions"
                      :key="item.id"
                      :label="`${item.case_no}　${exceptionTypeLabel(item.type)}　${exceptionStatusLabel(item.status)}`"
                      :value="item.id"
                    />
                  </el-select>
                </el-form-item>
                <el-form-item label="说明">
                  <el-input
                    v-model="clearIssueForm.note"
                    type="textarea"
                    :autosize="{ minRows: 2, maxRows: 4 }"
                    placeholder="例如：轮胎已更换，车辆恢复在途"
                  />
                </el-form-item>
                <el-form-item>
                  <el-button size="small" :loading="exceptionSaving" @click="clearVehicleIssue">
                    车辆已修复（只去掉车辆故障风险）
                  </el-button>
                  <span class="u-text-muted">
                    车辆状态恢复 + 该因子不再计分（例如 4 分 → 3 分）；<b>异常单继续跟进</b>，
                    延误 / 违约等其它问题仍在。要结束整单请用上面的「结束异常」
                  </span>
                </el-form-item>
              </el-form>
            </template>

            <!-- 已解决但未归档：占着"同一订单只能一个未关闭异常"的名额，归档后订单才能再录入 -->            <template v-if="canHandleException && resolvedExceptions.length > 0">
              <el-divider content-position="left">归档异常（已解决 → 已关闭）</el-divider>
              <el-form label-width="80px" size="small">
                <el-form-item label="选择异常">
                  <el-select v-model="archiveForm.exception_id" style="width: 100%" placeholder="选择已解决、待归档的异常">
                    <el-option
                      v-for="item in resolvedExceptions"
                      :key="item.id"
                      :label="`${item.case_no}　${exceptionTypeLabel(item.type)}　已解决`"
                      :value="item.id"
                    />
                  </el-select>
                </el-form-item>
                <el-form-item label="归档说明">
                  <el-input
                    v-model="archiveForm.note"
                    type="textarea"
                    :autosize="{ minRows: 2, maxRows: 4 }"
                    placeholder="可选：例如已核对无遗留问题"
                  />
                </el-form-item>
                <el-form-item>
                  <el-button size="small" :loading="exceptionSaving" @click="archiveIncident">归档异常</el-button>
                  <span class="u-text-muted">关闭是终态；归档后该订单才能录入新异常</span>
                </el-form-item>
              </el-form>
            </template>
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
