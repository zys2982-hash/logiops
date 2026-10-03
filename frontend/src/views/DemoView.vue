<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import PanelCard from '@/components/PanelCard.vue'
import { resetMockState } from '@/mocks/registry'
import { Perm } from '@/types'
import type { ExceptionStatus, OrderStatus } from '@/types'
import { exceptionApi, orderApi } from '@/api'
import { useAuthStore } from '@/stores/auth'
import { useDemoStore } from '@/stores/demo'
import { displayIsoToUtc, formatDateTime } from '@/utils/datetime'
import {
  EXCEPTION_STATUS_OPTIONS,
  ORDER_STATUS_OPTIONS,
  exceptionStatusLabel,
  orderStatusLabel,
} from '@/utils/format'

const auth = useAuthStore()
const demo = useDemoStore()

const tickMinutes = ref(60)
const targetTime = ref('')
const busy = ref(false)
/** 操作记录：界面只显示大白话（action），接口路径放进悬浮提示（api）供核对 */
const timeline = ref<Array<{ time: string; action: string; api?: string }>>([])

const canControl = computed(() => auth.can(Perm.DEMO_CONTROL))
/** 真实时间模式（CLOCK_MODE=system）下没有"虚拟时钟"可推：控件停用并说明原因，与后端 409 口径一致 */
const isRealClock = computed(() => demo.clockMode !== 'replay')

/** 运行时切换时钟模式：真实时间 ↔ 虚拟时钟（立即生效、不用重启，与顶部 AI 模式开关同一机制） */
async function switchClockMode(mode: 'system' | 'replay'): Promise<void> {
  if (mode === demo.clockMode) return
  busy.value = true
  try {
    await demo.setClockMode(mode)
    if (demo.lastError) {
      logAction(`时钟模式切换失败：${demo.lastError}`)
      ElMessage.error(`时钟模式切换失败：${demo.lastError}`)
      return
    }
    const label = mode === 'replay' ? '虚拟时钟（可快进 / 跳转）' : '真实时间'
    logAction(`时钟模式切到${label}`, `POST /demo/actions/set-clock-mode {clock_mode: "${mode}"}`)
    if (demo.lastWarning) ElMessage.warning(demo.lastWarning)
    else ElMessage.success(`时钟模式已切到${label}`)
    fillTargetFromNow()
  } finally {
    busy.value = false
  }
}

function logAction(action: string, api?: string): void {
  timeline.value.unshift({ time: new Date().toLocaleTimeString('zh-CN'), action, api })
  timeline.value = timeline.value.slice(0, 20)
}

async function refresh(): Promise<void> {
  await demo.refresh()
  // 默认把选择器填成当前业务时间，方便"微调式"跳转；已选过就不覆盖
  if (!targetTime.value) fillTargetFromNow()
  logAction('刷新了当前状态', 'GET /demo/state')
}

function fillTargetFromNow(): void {
  targetTime.value = formatDateTime(demo.state.now_utc, 'YYYY-MM-DD HH:mm:ss')
}

/** 时间跳转：选定年月日时分秒 → 直接把虚拟时钟设到那一刻（秒级精确，不触发业务链路） */
async function jumpToTime(): Promise<void> {
  if (!targetTime.value) {
    ElMessage.warning('请先选择目标时间（年月日时分秒）')
    return
  }
  const targetUtc = displayIsoToUtc(targetTime.value)
  if (!targetUtc) {
    ElMessage.error('时间格式不正确，请重新选择')
    return
  }
  busy.value = true
  try {
    await demo.setClock(targetUtc)
    logAction(
      `把虚拟时钟跳到 ${formatDateTime(targetUtc, 'YYYY-MM-DD HH:mm:ss')}（北京时间）`,
      `POST /demo/actions/set-clock {target_utc: "${targetUtc}"}`,
    )
    if (demo.lastError) {
      logAction(`时间跳转失败：${demo.lastError}`)
      ElMessage.error(`时间跳转失败：${demo.lastError}`)
      return
    }
    targetTime.value = formatDateTime(targetUtc, 'YYYY-MM-DD HH:mm:ss')
    ElMessage.success(`虚拟时钟已跳到 ${targetTime.value}（本地时间）`)
  } finally {
    busy.value = false
  }
}

async function tick(): Promise<void> {
  busy.value = true
  try {
    await demo.tick(tickMinutes.value)
    logAction(
      `快进 ${tickMinutes.value} 分钟（顺带跑了一轮轨迹/重算/检测/自动关闭）`,
      `POST /demo/actions/tick {minutes: ${tickMinutes.value}}`,
    )
    // 后端明确拒绝时（例如状态机 409）不谎报成功，错误提示已由拦截器给出
    if (demo.lastError) {
      logAction(`快进失败：${demo.lastError}`)
      ElMessage.error(`快进失败：${demo.lastError}`)
      return
    }
    ElMessage.success(demo.lastAction ?? '时钟已推进')
  } finally {
    busy.value = false
  }
}

async function reset(): Promise<void> {
  try {
    await ElMessageBox.confirm('将重建 seed 并把时钟归零（演示翻车后 3 秒恢复），继续？', '重置演示数据', {
      type: 'warning',
    })
  } catch {
    return
  }
  busy.value = true
  try {
    await demo.reset('case-a')
    // 本地 fixture 状态一并重置，保证无后端时也能恢复初始演示数据
    resetMockState()
    logAction('重置为初始演示数据（数据重建、时钟归零）', 'POST /demo/actions/reset {scenario: "case-a"}')
    ElMessage.success('已重置为 seed 初始态')
  } finally {
    busy.value = false
    await loadTargets()
  }
}

/* ---------------------------------------------------------------------------
 * 状态直设（演示）：订单与异常的状态可以自己选
 * 后端 POST /demo/actions/set-order-status | set-exception-status（跳过状态机，写审计留痕）
 * ------------------------------------------------------------------------- */
interface OrderOption {
  id: number
  order_no: string
  status: OrderStatus
}
interface CaseOption {
  id: number
  case_no: string
  status: ExceptionStatus
}

const orders = ref<OrderOption[]>([])
const cases = ref<CaseOption[]>([])
const orderPick = ref<number | null>(null)
const orderStatusPick = ref<OrderStatus | null>(null)
const casePick = ref<number | null>(null)
const caseStatusPick = ref<ExceptionStatus | null>(null)

async function loadTargets(): Promise<void> {
  try {
    const [orderPage, casePage] = await Promise.all([
      orderApi.listOrders({ page: 1, page_size: 50, sort: '-id' }),
      exceptionApi.listExceptions({ page: 1, page_size: 50, sort: '-id' }),
    ])
    orders.value = orderPage.items.map((item) => ({
      id: item.id,
      order_no: item.order_no,
      status: item.status,
    }))
    cases.value = casePage.items.map((item) => ({
      id: item.id,
      case_no: item.case_no,
      status: item.status,
    }))
  } catch {
    // 未登录 / 后端不可达时保持空列表，不影响本页其它演示功能
  }
}

async function applyOrderStatus(): Promise<void> {
  if (!orderPick.value || !orderStatusPick.value) {
    ElMessage.warning('请先选择订单和目标状态')
    return
  }
  busy.value = true
  try {
    const result = await demo.setOrderStatus(orderPick.value, orderStatusPick.value, '演示工具直设订单状态')
    if (!result) {
      logAction(`订单状态直设失败：${demo.lastError}`)
      ElMessage.error(`订单状态直设失败：${demo.lastError}`)
      return
    }
    logAction(
      `订单 ${result.order_no}：${result.previous_status} → ${result.status}`,
      'POST /demo/actions/set-order-status',
    )
    ElMessage.success(`订单已直设为 ${orderStatusLabel(result.status as OrderStatus)}`)
    await loadTargets()
  } finally {
    busy.value = false
  }
}

async function applyCaseStatus(): Promise<void> {
  if (!casePick.value || !caseStatusPick.value) {
    ElMessage.warning('请先选择异常单和目标状态')
    return
  }
  busy.value = true
  try {
    const result = await demo.setExceptionStatus(casePick.value, caseStatusPick.value, '演示工具直设异常状态')
    if (!result) {
      logAction(`异常状态直设失败：${demo.lastError}`)
      ElMessage.error(`异常状态直设失败：${demo.lastError}`)
      return
    }
    logAction(
      `异常 ${result.case_no}：${result.previous_status} → ${result.status}`,
      'POST /demo/actions/set-exception-status',
    )
    ElMessage.success(`异常已直设为 ${exceptionStatusLabel(result.status as ExceptionStatus)}`)
    await loadTargets()
  } finally {
    busy.value = false
  }
}

onMounted(async () => {
  await refresh()
  await loadTargets()
})
</script>

<template>
  <div class="page">
    <el-alert
      type="warning"
      :closable="false"
      show-icon
      class="u-mb-12"
      title="演示工具（仅本地演示）"
      description="① 时间控制：真实时间模式（CLOCK_MODE=system，现行默认）下系统直接用现实时间，快进/跳转已停用；只有 CLOCK_MODE=replay 时才需要手动推虚拟时钟（推进即触发：轨迹生成 → ETA 重算 → 异常检测 → 审批过期检查 → 自动关闭检查）。② 一键重置回到 seed 初始态（演示翻车 3 秒恢复）。权限：APP_ENV=local 且 ADMIN+ 才有 demo.control。AI 模式（回放样本 / 真实大模型）在顶部横幅上直接切换。"
    />

    <el-row :gutter="12">
      <el-col :md="14">
        <PanelCard title="时间控制（真实时间 / 虚拟时钟）" icon="MagicStick">
          <template #actions>
            <el-tag v-if="!canControl" size="small" effect="plain">当前角色无 demo.control</el-tag>
            <el-tag v-else size="small" type="success" effect="plain">可控制</el-tag>
          </template>

          <div class="clock-mode-row u-mb-12">
            <span class="u-text-muted">时钟模式：</span>
            <el-radio-group
              :model-value="demo.clockMode"
              size="small"
              :disabled="!canControl || busy"
              @change="(value: string) => switchClockMode(value as 'system' | 'replay')"
            >
              <el-radio-button value="system">真实时间</el-radio-button>
              <el-radio-button value="replay">虚拟时钟</el-radio-button>
            </el-radio-group>
            <span class="u-text-muted" style="margin-left: 8px">
              运行时切换、不用重启；重启后端后回到 .env 的默认值（当前 {{ demo.clockMode }}）
            </span>
          </div>

          <el-alert
            v-if="isRealClock"
            type="success"
            :closable="false"
            show-icon
            class="u-mb-12"
            title="当前是真实时间模式：系统时间就是现实时间"
            description="没有可推的虚拟时钟，所以下方「快进 / 跳到该时间」已停用；要演示这些就切到上面的「虚拟时钟」（立即生效，数据时间戳随之按虚拟时间写）。「重置到初始态」两种模式都可用。"
          />

          <el-descriptions :column="2" size="small" border class="u-mb-12">
            <el-descriptions-item label="业务时间">{{ demo.businessTimeText }}</el-descriptions-item>
            <el-descriptions-item label="基准日">{{ demo.baseDateText }}</el-descriptions-item>
            <el-descriptions-item label="时钟偏移">{{ demo.offsetMinutes }} 分钟</el-descriptions-item>
            <el-descriptions-item label="clock_mode">{{ demo.clockMode }}</el-descriptions-item>
            <el-descriptions-item label="workspace_id">{{ demo.state.workspace_id ?? '—' }}</el-descriptions-item>
            <el-descriptions-item label="now_utc">{{ formatDateTime(demo.state.now_utc, 'YYYY-MM-DD HH:mm:ss') }}</el-descriptions-item>
            <el-descriptions-item label="seed_available">{{ demo.state.seed_available ?? '—' }}</el-descriptions-item>
          </el-descriptions>

          <el-form label-width="120px" :disabled="!canControl">
            <el-form-item label="推进分钟数">
              <el-input-number v-model="tickMinutes" :min="1" :max="43200" :step="30" :disabled="isRealClock" />
              <span class="u-text-muted u-mt-8">（tick 会触发全链路自动执行；仅虚拟时钟模式可用）</span>
            </el-form-item>
            <el-form-item label="跳转到时间">
              <div class="jump-row">
                <el-date-picker
                  v-model="targetTime"
                  type="datetime"
                  placeholder="点这里选年月日时分秒"
                  format="YYYY-MM-DD HH:mm:ss"
                  value-format="YYYY-MM-DD HH:mm:ss"
                  :disabled="!canControl || isRealClock"
                  @keyup.enter="jumpToTime"
                />
                <el-button
                  type="warning"
                  :loading="busy"
                  :disabled="!canControl || isRealClock"
                  @click="jumpToTime"
                >
                  跳到该时间 →
                </el-button>
              </div>
              <span class="u-text-muted">
                操作两步：① 在弹出面板里选好年月日时分秒并点面板右下角「确定」；② 点右侧「跳到该时间 →」执行。
                提交时按本地时区（Asia/Shanghai）转成 UTC。
              </span>
            </el-form-item>
            <el-form-item>
              <el-button type="primary" :loading="busy" :disabled="isRealClock" @click="tick">快进 {{ tickMinutes }} 分钟</el-button>
              <el-button text type="danger" :loading="busy" @click="reset">重置到初始态</el-button>
              <el-button text type="primary" @click="refresh">刷新状态</el-button>
            </el-form-item>
            <div class="u-text-muted" style="line-height: 1.7">
              1）<b>快进 N 分钟</b>：让虚拟时间走 N 分钟，顺便跑一遍自动链路（轨迹 → ETA 重算 → 检测 → 自动关闭）。<br />
              2）<b>跳到该时间</b>：选好年月日时分秒，直接把虚拟时钟设到那一刻（秒级精确，只改时钟）。
              跳完想立刻触发一次检测，再点一次「快进 1 分钟」。
            </div>
          </el-form>
        </PanelCard>
      </el-col>

      <el-col :md="10">
        <PanelCard title="操作记录" subtitle="最近 20 次；鼠标移上去可看对应接口" icon="Clock">
          <el-empty v-if="timeline.length === 0" description="还没有操作" :image-size="50" />
          <ul class="action-list">
            <li v-for="(item, index) in timeline" :key="index" :title="item.api ?? ''">
              <span class="u-mono u-text-muted">{{ item.time }}</span>
              <span>{{ item.action }}</span>
            </li>
          </ul>
        </PanelCard>
      </el-col>
    </el-row>

    <el-row :gutter="12" class="u-mt-12">
      <el-col :md="24">
        <PanelCard title="状态直设（订单 / 异常的状态都可以自己选）" icon="Switch">
          <template #actions>
            <el-tag v-if="!canControl" size="small" effect="plain">当前角色无 demo.control</el-tag>
            <el-tag v-else size="small" type="success" effect="plain">可控制</el-tag>
          </template>

          <el-alert
            type="info"
            :closable="false"
            show-icon
            class="u-mb-12"
            title="演示专用：直接设定状态（跳过状态机），但一切留痕"
            description="订单：设定 IN_TRANSIT / DELIVERED 会补 dispatched_at / delivered_at，回到未送达会清 delivered_at；异常：进入已解决 / 已关闭会释放车辆维修状态并补结束时间，回到进行中会清掉结束时间。每次都会写审计（order.status_forced / exception.status_forced）和状态事件，演示后可在审计页查到。"
          />

          <el-form label-width="130px" :disabled="!canControl">
            <el-form-item label="订单 → 目标状态">
              <div class="status-row">
                <el-select v-model="orderPick" filterable placeholder="选择订单" class="status-select">
                  <el-option
                    v-for="order in orders"
                    :key="order.id"
                    :label="`${order.order_no}（${orderStatusLabel(order.status)}）`"
                    :value="order.id"
                  />
                </el-select>
                <el-select v-model="orderStatusPick" placeholder="目标状态" style="width: 160px">
                  <el-option
                    v-for="option in ORDER_STATUS_OPTIONS"
                    :key="option.value"
                    :label="option.label"
                    :value="option.value"
                  />
                </el-select>
                <el-button type="primary" :loading="busy" :disabled="!canControl" @click="applyOrderStatus">
                  应用
                </el-button>
              </div>
            </el-form-item>

            <el-form-item label="异常 → 目标状态">
              <div class="status-row">
                <el-select v-model="casePick" filterable placeholder="选择异常单" class="status-select">
                  <el-option
                    v-for="item in cases"
                    :key="item.id"
                    :label="`${item.case_no}（${exceptionStatusLabel(item.status)}）`"
                    :value="item.id"
                  />
                </el-select>
                <el-select v-model="caseStatusPick" placeholder="目标状态" style="width: 160px">
                  <el-option
                    v-for="option in EXCEPTION_STATUS_OPTIONS"
                    :key="option.value"
                    :label="option.label"
                    :value="option.value"
                  />
                </el-select>
                <el-button type="primary" :loading="busy" :disabled="!canControl" @click="applyCaseStatus">
                  应用
                </el-button>
              </div>
            </el-form-item>

            <el-form-item>
              <el-button text type="primary" @click="loadTargets">刷新订单 / 异常列表</el-button>
              <span class="u-text-muted u-ml-8">
                列表取最新 50 条；演示中改完状态可回到订单页 / 异常中心确认
              </span>
            </el-form-item>
          </el-form>
        </PanelCard>
      </el-col>
    </el-row>
  </div>
</template>

<style scoped>
/* 时间选择器与"跳到该时间"按钮必须紧挨着，否则用户找不到执行入口 */
.jump-row {
  display: flex;
  gap: 8px;
  width: 100%;
}

.jump-row :deep(.el-date-editor) {
  flex: 1;
}

/* 状态直设：下拉与"应用"同一行，窄屏自动换行 */
.status-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.status-select {
  width: 300px;
}

.action-list {
  margin: 0;
  padding-left: 16px;
  font-size: 12px;
  max-height: 320px;
  overflow: auto;
}

.action-list li {
  display: flex;
  gap: 8px;
  margin-bottom: 4px;
}
</style>
