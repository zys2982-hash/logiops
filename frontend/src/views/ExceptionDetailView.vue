<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'

import AiPanel from '@/components/AiPanel.vue'
import ApiHint from '@/components/ApiHint.vue'
import ApprovalCard from '@/components/ApprovalCard.vue'
import CarrierMessagePanel from '@/components/CarrierMessagePanel.vue'
import ExceptionTimeline from '@/components/ExceptionTimeline.vue'
import FollowupPanel from '@/components/FollowupPanel.vue'
import NotificationPanel from '@/components/NotificationPanel.vue'
import PanelCard from '@/components/PanelCard.vue'
import RiskTag from '@/components/RiskTag.vue'
import SlaImpactCard from '@/components/SlaImpactCard.vue'
import TrackingTimeline from '@/components/TrackingTimeline.vue'
import { exceptionApi, orderApi } from '@/api'
import { Perm } from '@/types'
import type {
  Approval,
  CarrierMessage,
  ExceptionDetail,
  ExceptionEvent,
  FollowupTask,
  Notification,
  TimelineIncident,
  TrackingEvent,
} from '@/types'
import { useAuthStore } from '@/stores/auth'
import { useDemoStore } from '@/stores/demo'
import { useWorkspaceStore } from '@/stores/workspace'
import { formatDateTime, formatDelay } from '@/utils/datetime'
import { customerLevelLabel, exceptionStatusLabel, exceptionStatusType, exceptionTypeLabel, formatNumber } from '@/utils/format'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const demo = useDemoStore()
const workspace = useWorkspaceStore()

const exceptionId = computed(() => Number(route.params.id))

const exception = ref<ExceptionDetail | null>(null)
const trackingEvents = ref<TrackingEvent[]>([])
const messages = ref<CarrierMessage[]>([])
const followups = ref<FollowupTask[]>([])
const notifications = ref<Notification[]>([])
const approvals = ref<Approval[]>([])
const events = ref<ExceptionEvent[]>([])
const loading = ref(false)
const actionLoading = ref(false)

const aiPanel = ref<InstanceType<typeof AiPanel> | null>(null)

const canHandle = computed(() => auth.can(Perm.EXCEPTION_HANDLE))
const canDecide = computed(() => auth.can(Perm.APPROVAL_DECIDE))
const canNotify = computed(() => auth.can(Perm.NOTIFICATION_APPROVE))
const canFollowup = computed(() => auth.can(Perm.FOLLOWUP_WRITE))
const canForceClose = computed(() => auth.can(Perm.EXCEPTION_FORCE_CLOSE))

const pendingApprovals = computed(() => approvals.value.filter((a) => a.status === 'PENDING'))
const decidedApprovals = computed(() => approvals.value.filter((a) => a.status !== 'PENDING'))

/** 本页异常自身作为时间线上的"异常条目"（开始 + 已结束时追加结束那条） */
const timelineIncidents = computed<TimelineIncident[]>(() => {
  const current = exception.value
  if (!current) return []
  const endedAt = current.resolved_at ?? current.closed_at ?? null
  return [
    {
      id: current.id,
      case_no: current.case_no,
      type: current.type,
      level: current.level,
      status: current.status,
      startedAt: current.occurred_at,
      endedAt,
      endedLabel: String(current.status) === 'CLOSED' ? '已关闭' : '已解决',
    },
  ]
})

/** 右侧流程标签页当前页（默认 AI 分析） */
const activeTab = ref('ai')
/** 未完成的跟进任务数（用于标签角标） */
const openFollowupCount = computed(() => followups.value.filter((t) => t.status === 'OPEN').length)
/** 审批接口说明：原来铺在卡片底部的"契约：…"，现在收进右上角 ⓘ 悬浮提示 */
const approvalApiHint =
  'POST /approvals/{id}/approve body {expected_version, final_payload}；' +
  '批准后后端在同一事务内执行 UPDATE_ETA / CREATE_FOLLOWUP / SAVE_NOTICE / SEND_NOTICE / CLOSE_EXCEPTION'

/** 实测详情不返回 assigned_to_name，用工作区成员列表映射 user_id → 姓名 */
const assigneeName = computed(() => {
  const userId = exception.value?.assigned_to
  if (!userId) return '未指派'
  return workspace.members.find((m) => m.user_id === userId)?.name ?? `用户 #${userId}`
})

/** 详情里内嵌 order/customer 都是扁平/嵌套对象；列表字段缺失时逐级兜底 */
const customerLevel = computed(
  () => exception.value?.customer?.level ?? exception.value?.customer_level ?? null,
)

/**
 * 实测 GET /exceptions/{id} 的顶层 order_no / customer_name / vehicle_plate 是**空值**，
 * 真实数据只在嵌套的 order / customer 里 → 展示时优先取嵌套值。
 */
const displayOrderNo = computed(
  () => exception.value?.order?.order_no ?? exception.value?.order_no ?? exception.value?.case_no ?? '—',
)
const displayCustomerName = computed(
  () => exception.value?.customer?.name ?? exception.value?.customer_name ?? '—',
)
const displayVehiclePlate = computed(
  () => exception.value?.vehicle?.plate_no ?? exception.value?.vehicle_plate ?? '—',
)

/** 可发起 AI 分析：确认中（首次）、处理中（重新分析）、以及遗留的 ANALYZING（后端自动回退）。
 *  真有任务在跑时 AiPanel 会用 isRunning 自己隐藏按钮，所以这里放开是安全的。 */
const canAnalyze = computed(
  () =>
    canHandle.value && ['CONFIRMING', 'PROCESSING', 'ANALYZING'].includes(exception.value?.status ?? ''),
)

/** 不可分析时告诉用户"为什么、该怎么办"，避免点了才报 409 */
const analyzeBlockReason = computed(() => {
  const status = exception.value?.status
  if (!canHandle.value) return '没有 exception.handle 权限，无法触发分析'
  if (status === 'DETECTED') return '异常还没确认：请先点上方「确认异常」，再发起 AI 分析'
  if (status === 'RESOLVED') return '异常已「已解决」，无需再分析'
  if (status === 'CLOSED') return '异常已关闭，不能再分析'
  return ''
})

async function loadAll(): Promise<void> {
  loading.value = true
  try {
    const detail = await exceptionApi.getException(exceptionId.value)
    exception.value = detail
    // 详情已内嵌轨迹时先用内嵌数据渲染，减少首屏空白
    if (detail.tracking_events?.length) trackingEvents.value = detail.tracking_events

    const [trackingResult, messagesResult, followupResult, notificationResult, approvalResult, eventResult] =
      await Promise.allSettled([
        orderApi.listTrackingEvents(detail.order_id),
        exceptionApi.listCarrierMessages(exceptionId.value),
        exceptionApi.listFollowups(exceptionId.value),
        exceptionApi.listNotifications(exceptionId.value),
        exceptionApi.listApprovals(exceptionId.value),
        exceptionApi.listExceptionEvents(exceptionId.value),
      ])

    if (trackingResult.status === 'fulfilled') trackingEvents.value = trackingResult.value
    if (messagesResult.status === 'fulfilled') messages.value = messagesResult.value
    if (followupResult.status === 'fulfilled') followups.value = followupResult.value
    if (notificationResult.status === 'fulfilled') notifications.value = notificationResult.value
    if (approvalResult.status === 'fulfilled') approvals.value = approvalResult.value.items
    if (eventResult.status === 'fulfilled') events.value = eventResult.value.items

    // 已有分析结果时回填 AI 面板（不在轮询中时）。
    // 注意：AiPanel 在 <template v-if="exception"> 内，上面的赋值刚触发挂载，
    // 必须等一次 nextTick 才能拿到模板 ref，否则 panel 为 null 会静默跳过回填。
    const latest = detail.latest_analysis
    if (latest) {
      await nextTick()
      const panel = aiPanel.value
      if (panel && !panel.isPolling()) {
        panel.loadExisting(latest)
      }
    }
  } finally {
    loading.value = false
  }
}

async function refreshAfterWrite(): Promise<void> {
  await loadAll()
}

/* ---------------------------------------------------------- 状态机动作 */

async function confirmCase(): Promise<void> {
  if (!exception.value) return
  actionLoading.value = true
  try {
    await exceptionApi.confirmException(exception.value.id, { expected_version: exception.value.version })
    ElMessage.success('已确认，状态进入 CONFIRMING')
    await refreshAfterWrite()
  } finally {
    actionLoading.value = false
  }
}

async function resolveCase(): Promise<void> {
  if (!exception.value) return
  let note = ''
  try {
    const result = await ElMessageBox.prompt('resolve 需要填写处理说明（note）', '标记为已解决', {
      inputPlaceholder: '例如：车辆已修复恢复行驶，客户已确认新 ETA',
      inputValidator: (value) => (value && value.trim().length > 0 ? true : '请填写 note'),
    })
    note = result.value ?? ''
  } catch {
    return
  }
  actionLoading.value = true
  try {
    // resolve 的 expected_version 必填（乐观锁）；详情未加载完时不能发请求
    const version = exception.value.version
    if (typeof version !== 'number') {
      ElMessage.warning('异常版本信息缺失，请刷新后重试')
      return
    }
    await exceptionApi.resolveException(exception.value.id, {
      note,
      expected_version: version,
    })
    ElMessage.success('状态已更新为 RESOLVED')
    await refreshAfterWrite()
  } finally {
    actionLoading.value = false
  }
}

/** 头部「更多」下拉：把低频操作（关闭/强制关闭）收起来，减少按钮堆叠 */
function onHeadCommand(command: string | number | object): void {
  if (command === 'close') void closeCase(false)
  else if (command === 'force-close') void closeCase(true)
}

async function closeCase(forced = false): Promise<void> {
  if (!exception.value) return
  let note = ''
  let reasonCode = 'MANUAL'
  try {
    const result = await ElMessageBox.prompt(
      forced ? '强制关闭（FORCED_CLOSE）必须填写理由，将写入审计' : '关闭为终态不可逆（CLOSED），请填写说明',
      forced ? '强制关闭' : '关闭异常',
      {
        inputPlaceholder: forced ? '例如：承运商已改派，本单转人工线下处理' : '例如：订单已送达，异常解除',
        inputValidator: (value) => (value && value.trim().length > 0 ? true : '请填写说明'),
      },
    )
    note = result.value ?? ''
  } catch {
    return
  }
  if (!forced) {
    reasonCode = 'DELIVERED'
  }
  actionLoading.value = true
  try {
    await exceptionApi.closeException(exception.value.id, {
      reason_code: reasonCode,
      note,
      expected_version: exception.value.version,
    })
    ElMessage.success('异常已关闭（终态）')
    await refreshAfterWrite()
  } finally {
    actionLoading.value = false
  }
}

/* -------------------------------------------------------------- 指派处理 */

const assigneeVisible = ref(false)
const assigneeSaving = ref(false)
const assigneeId = ref<number | null>(null)

function openAssign(): void {
  assigneeId.value = exception.value?.assigned_to ?? null
  assigneeVisible.value = true
}

async function submitAssign(): Promise<void> {
  if (!exception.value) return
  assigneeSaving.value = true
  try {
    // PATCH /exceptions/{id} 的 expected_version 必填（缺失 422）
    const updated = await exceptionApi.updateException(exception.value.id, {
      assigned_to: assigneeId.value,
      expected_version: exception.value.version,
    })
    exception.value = updated
    ElMessage.success('处理人已更新')
    assigneeVisible.value = false
  } catch {
    // 409 已提示“数据已被他人更新”，拉最新数据
    await refreshAfterWrite()
  } finally {
    assigneeSaving.value = false
  }
}

/* -------------------------------------------------------------- AI 面板 */

async function onAnalysisStarted(analysisId: number): Promise<void> {
  ElMessage.success(`AI 分析已启动（analysis_id=${analysisId}）`)
  // 分析完成后后端会生成审批单，这里延迟刷新审批单列表
  window.setTimeout(() => {
    void exceptionApi.listApprovals(exceptionId.value).then((page) => {
      approvals.value = page.items
    })
  }, 3000)
}

/* -------------------------------------------------------------- 审批单 */

async function onApprove(payload: {
  id: number
  expected_version: number
  final_payload: Record<string, unknown>
}): Promise<void> {
  try {
    const result = await exceptionApi.approveApproval(payload.id, {
      expected_version: payload.expected_version,
      final_payload: payload.final_payload,
    })
    ElMessage.success(`审批单 #${payload.id} 已执行（status=${result.status}）`)
    await refreshAfterWrite()
  } catch {
    // 409 已由拦截器提示“数据已被他人更新”，这里主动刷新
    await refreshAfterWrite()
  }
}

async function onReject(payload: { id: number; expected_version: number; reason: string }): Promise<void> {
  try {
    await exceptionApi.rejectApproval(payload.id, {
      expected_version: payload.expected_version,
      reason: payload.reason,
    })
    ElMessage.success(`审批单 #${payload.id} 已驳回`)
    await refreshAfterWrite()
  } catch {
    await refreshAfterWrite()
  }
}

async function onExecute(id: number): Promise<void> {
  try {
    await exceptionApi.executeApproval(id)
    ElMessage.success(`审批单 #${id} 已重新执行`)
    await refreshAfterWrite()
  } catch {
    await refreshAfterWrite()
  }
}

async function batchApprove(): Promise<void> {
  if (!exception.value || pendingApprovals.value.length === 0) return
  try {
    await ElMessageBox.confirm(
      `将逐条批准并执行 ${pendingApprovals.value.length} 张审批单，结果逐条展示。继续？`,
      '批量批准',
      { type: 'warning' },
    )
  } catch {
    return
  }
  try {
    const result = await exceptionApi.batchApprove({
      exception_id: exception.value.id,
      approval_ids: pendingApprovals.value.map((a) => a.id),
      auto_execute: true,
    })
    ElMessage.success(`批量批准完成：成功 ${result.succeeded} 条，失败 ${result.failed} 条`)
    await refreshAfterWrite()
  } catch {
    await refreshAfterWrite()
  }
}

/* -------------------------------------------------------- 承运商消息/通知 */

const messageDraft = ref('')
const messageChannel = ref('MANUAL_PASTE')
const messageSender = ref('')
const messageSubmitting = ref(false)

async function submitMessage(payload: { raw_text: string; channel: string; sender_name: string }): Promise<void> {
  messageSubmitting.value = true
  try {
    // 实测响应是 {message_id,parse_status,parse_result,eta,...}，不是完整消息对象
    const result = await exceptionApi.createCarrierMessage(exceptionId.value, {
      raw_text: payload.raw_text,
      channel: payload.channel as CarrierMessage['channel'],
      sender_name: payload.sender_name || undefined,
    })
    ElMessage.success(
      `消息已录入（message_id=${result.message_id}，parse_status=${result.parse_status}）`,
    )
    if (result.eta?.eta_at) {
      ElMessage.info(`解析后重算 ETA：${formatDateTime(String(result.eta.eta_at))}（${String(result.eta.method ?? '')}）`)
    }
    messageDraft.value = ''
    messageSender.value = ''
    await refreshAfterWrite()
  } finally {
    messageSubmitting.value = false
  }
}

async function updateNotification(payload: { id: number; content: string; subject?: string }): Promise<void> {
  await exceptionApi.updateNotification(payload.id, { content: payload.content, subject: payload.subject })
  ElMessage.success('通知草稿已保存（保留 ai_draft_content）')
  await refreshAfterWrite()
}

async function approveNotification(id: number): Promise<void> {
  const target = notifications.value.find((n) => n.id === id)
  if (!target) return
  await exceptionApi.updateNotification(id, { content: target.content, expected_version: target.version })
  ElMessage.success('通知内容已确认（后端 APPROVED 由审批链承担；此处沿用 PATCH 保存）')
  await refreshAfterWrite()
}

async function sendNotification(id: number): Promise<void> {
  const result = await exceptionApi.markNotificationSent(id)
  ElMessage.success(`模拟发送完成（status=${result.status}，已写审计）`)
  await refreshAfterWrite()
}

async function skipNotification(id: number): Promise<void> {
  let reason = ''
  try {
    const result = await ElMessageBox.prompt('SKIPPED 需要填写原因', '跳过通知', {
      inputValidator: (value) => (value && value.trim().length > 0 ? true : '请填写原因'),
    })
    reason = result.value ?? ''
  } catch {
    return
  }
  await exceptionApi.skipNotification(id, { reason })
  ElMessage.success('通知已跳过')
  await refreshAfterWrite()
}

/* -------------------------------------------------------------- 跟进任务 */

async function createFollowup(payload: { title: string; content: string; due_at: string | null }): Promise<void> {
  await exceptionApi.createFollowup({
    exception_id: exceptionId.value,
    title: payload.title,
    content: payload.content || undefined,
    due_at: payload.due_at ? new Date(payload.due_at).toISOString() : null,
  })
  ElMessage.success('跟进任务已创建（source=MANUAL）')
  await refreshAfterWrite()
}

async function doneFollowup(id: number): Promise<void> {
  await exceptionApi.updateFollowup(id, { status: 'DONE' })
  ElMessage.success('跟进任务已完成')
  await refreshAfterWrite()
}

watch(exceptionId, () => {
  void loadAll()
})

onMounted(async () => {
  await Promise.all([loadAll(), workspace.members.length ? Promise.resolve() : workspace.refreshMembers()])
})
</script>

<template>
  <div class="page" v-loading="loading">
    <el-empty v-if="!exception && !loading" description="异常不存在或无权访问（跨租户统一返回 404）">
      <el-button @click="router.push('/exceptions')">返回异常中心</el-button>
    </el-empty>

    <template v-if="exception">
      <!-- 顶部操作条 -->
      <el-card shadow="never" class="page-card u-mb-12">
        <div class="detail-head">
          <div class="detail-head-main">
            <h3>{{ displayOrderNo }}</h3>
            <el-tag effect="plain">{{ exceptionTypeLabel(exception.type) }}</el-tag>
            <RiskTag :level="exception.level" :score="exception.risk_score" show-score />
            <el-tag :type="exceptionStatusType(exception.status)">{{ exceptionStatusLabel(exception.status) }}</el-tag>
            <el-tag v-if="exception.sla_breached" type="danger" effect="dark">SLA 违约 {{ formatDelay(exception.sla_delay_minutes) }}</el-tag>
            <el-tag v-else type="info" effect="plain">{{ formatDelay(exception.sla_delay_minutes) }}</el-tag>
            <span class="u-text-muted">处理人：{{ assigneeName }}</span>
            <span class="u-text-muted">异常编号 {{ exception.case_no }}</span>
            <span v-if="exception.merged_count" class="u-text-muted">合并 {{ formatNumber(exception.merged_count) }} 次</span>
          </div>
          <div class="detail-head-actions">
            <el-button
              v-if="canHandle && exception.status === 'DETECTED'"
              size="small"
              type="primary"
              :loading="actionLoading"
              @click="confirmCase"
            >
              确认异常
            </el-button>
            <el-button v-if="canHandle" size="small" type="primary" plain :loading="actionLoading" @click="resolveCase">
              处理完成
            </el-button>
            <el-button v-if="canHandle" size="small" @click="openAssign">指派</el-button>
            <el-dropdown v-if="canHandle || canForceClose" trigger="click" @command="onHeadCommand">
              <el-button size="small" text>
                更多<el-icon><ArrowDown /></el-icon>
              </el-button>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item v-if="canHandle" command="close">关闭异常</el-dropdown-item>
                  <el-dropdown-item v-if="canForceClose" command="force-close" divided>强制关闭（ADMIN）</el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
            <el-button size="small" text @click="refreshAfterWrite">刷新</el-button>
          </div>
        </div>
        <div class="u-text-muted u-mt-8">
          业务时间 {{ demo.businessTimeText }} · 承诺到达 {{ formatDateTime(exception.promised_delivery_at) }} ·
          预计送达 {{ formatDateTime(exception.expected_eta_at) }}
        </div>
      </el-card>

      <!-- 两列布局：左=事实，右=流程（用标签页收纳，避免十几张卡片堆叠） -->
      <div class="detail-grid">
        <!-- 左列：事实 -->
        <div class="detail-col">
          <PanelCard title="订单信息" :subtitle="exception.case_no" icon="Van">
            <el-descriptions :column="2" size="small" border>
              <el-descriptions-item label="订单号">{{ displayOrderNo }}</el-descriptions-item>
              <el-descriptions-item label="状态">{{ exception.order?.status ?? '—' }}</el-descriptions-item>
              <el-descriptions-item label="客户">{{ displayCustomerName }}</el-descriptions-item>
              <el-descriptions-item label="客户等级">{{ customerLevelLabel(customerLevel) }}</el-descriptions-item>
              <el-descriptions-item label="起终地">
                {{ exception.order?.origin_city ?? '—' }} → {{ exception.order?.dest_city ?? '—' }}
              </el-descriptions-item>
              <el-descriptions-item label="里程">{{ exception.order?.distance_km ?? '—' }} km</el-descriptions-item>
              <el-descriptions-item label="车辆">{{ displayVehiclePlate }}</el-descriptions-item>
              <el-descriptions-item label="司机">{{ exception.order?.driver_name ?? '—' }}</el-descriptions-item>
              <el-descriptions-item label="承运商">{{ exception.order?.carrier_name ?? '—' }}</el-descriptions-item>
              <el-descriptions-item label="发车时间">{{ formatDateTime(exception.order?.dispatched_at) }}</el-descriptions-item>
            </el-descriptions>
            <div v-if="exception.impact_summary" class="impact-summary u-mt-8">{{ exception.impact_summary }}</div>
            <router-link :to="`/orders/${exception.order_id}`">
              <el-button size="small" text type="primary" class="u-mt-8">查看订单详情</el-button>
            </router-link>
          </PanelCard>

          <TrackingTimeline
            id="evidence-timeline"
            :events="trackingEvents"
            :incidents="timelineIncidents"
            :loading="loading"
          />

          <SlaImpactCard :exception="exception" @updated="refreshAfterWrite" />

          <PanelCard title="风险等级" subtitle="规则逐项加权，LLM 无权修改" icon="WarnTriangleFilled">
            <div class="risk-head u-mb-8">
              <RiskTag :level="exception.level" :score="exception.risk_score" show-score size="large" />
              <span class="u-text-muted">0→低 / 1-2→中 / 3→高 / 4→严重（封顶）</span>
            </div>
            <el-table
              :data="exception.risk_factors ?? []"
              size="small"
              border
              empty-text="无风险因子明细"
            >
              <el-table-column prop="label" label="因子" min-width="120" />
              <el-table-column prop="weight" label="权重" width="60" align="center" />
              <el-table-column prop="detail" label="说明" min-width="180" />
            </el-table>
          </PanelCard>
        </div>

        <!-- 右列：流程（AI / 审批 / 协同 / 记录） -->
        <div class="detail-col">
          <el-tabs v-model="activeTab" class="detail-tabs">
            <el-tab-pane name="ai">
              <template #label>
                <span>AI 分析</span>
              </template>
              <AiPanel
                ref="aiPanel"
                :exception="exception"
                :can-analyze="canAnalyze"
                :block-reason="analyzeBlockReason"
                @started="onAnalysisStarted"
              />
            </el-tab-pane>

            <el-tab-pane name="approvals">
              <template #label>
                <span>审批单<el-badge v-if="pendingApprovals.length" :value="pendingApprovals.length" class="tab-count" /></span>
              </template>
              <PanelCard
                title="审批单（HITL）"
                :subtitle="`${pendingApprovals.length} 张待决策 / 共 ${approvals.length} 张`"
                icon="Stamp"
              >
                <template #actions>
                  <ApiHint :text="approvalApiHint" />
                  <el-button
                    v-if="canDecide && pendingApprovals.length > 0"
                    size="small"
                    type="primary"
                    @click="batchApprove"
                  >
                    批量批准（{{ pendingApprovals.length }}）
                  </el-button>
                </template>

                <el-empty v-if="approvals.length === 0" description="暂无审批单（AI 建议会转成审批单）" :image-size="50" />

                <ApprovalCard
                  v-for="approval in pendingApprovals"
                  :key="approval.id"
                  :approval="approval"
                  :can-decide="canDecide"
                  @approve="onApprove"
                  @reject="onReject"
                  @execute="onExecute"
                />

                <template v-if="decidedApprovals.length > 0">
                  <el-divider content-position="left">已决策</el-divider>
                  <ApprovalCard
                    v-for="approval in decidedApprovals"
                    :key="approval.id"
                    :approval="approval"
                    :can-decide="canDecide"
                    @approve="onApprove"
                    @reject="onReject"
                    @execute="onExecute"
                  />
                </template>
              </PanelCard>
            </el-tab-pane>

            <el-tab-pane name="messages">
              <template #label>
                <span>承运商消息<el-badge v-if="messages.length" :value="messages.length" class="tab-count" /></span>
              </template>
              <CarrierMessagePanel
                v-model:draft="messageDraft"
                v-model:channel="messageChannel"
                v-model:sender="messageSender"
                :messages="messages"
                :can-write="canHandle"
                :submitting="messageSubmitting"
                @submit="submitMessage"
              />
            </el-tab-pane>

            <el-tab-pane name="notices">
              <template #label>
                <span>客户通知<el-badge v-if="notifications.length" :value="notifications.length" class="tab-count" /></span>
              </template>
              <NotificationPanel
                :notifications="notifications"
                :can-approve="canNotify"
                @update="updateNotification"
                @approve="approveNotification"
                @send="sendNotification"
                @skip="skipNotification"
              />
            </el-tab-pane>

            <el-tab-pane name="followups">
              <template #label>
                <span>跟进任务<el-badge v-if="openFollowupCount" :value="openFollowupCount" class="tab-count" /></span>
              </template>
              <FollowupPanel
                :tasks="followups"
                :can-write="canFollowup"
                @create="createFollowup"
                @done="doneFollowup"
              />
            </el-tab-pane>

            <el-tab-pane name="events">
              <template #label>
                <span>操作记录<el-badge v-if="events.length" :value="events.length" class="tab-count" /></span>
              </template>
              <ExceptionTimeline :events="events" />
            </el-tab-pane>
          </el-tabs>
        </div>
      </div>
    </template>

    <el-dialog v-model="assigneeVisible" title="指派处理人" width="420px">
      <el-form label-width="80px">
        <el-form-item label="处理人">
          <el-select v-model="assigneeId" clearable filterable placeholder="选择成员" style="width: 100%">
            <el-option
              v-for="member in workspace.members"
              :key="member.id"
              :label="`${member.name ?? member.email}（${member.role}）`"
              :value="member.user_id"
            />
          </el-select>
        </el-form-item>
      </el-form>
      <div class="u-text-muted">
        指派结果会写入审计日志（谁在什么时候把异常指派给了谁）。
      </div>
      <template #footer>
        <el-button @click="assigneeVisible = false">取消</el-button>
        <el-button type="primary" :loading="assigneeSaving" @click="submitAssign">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.detail-head {
  display: flex;
  align-items: flex-start;
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

.detail-head-actions {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.impact-summary {
  background: #f5f7fa;
  border-radius: 4px;
  padding: 8px;
  font-size: 13px;
}

.risk-head {
  display: flex;
  align-items: center;
  gap: 8px;
}
</style>
