<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import PanelCard from '@/components/PanelCard.vue'
import { resetMockState } from '@/mocks/registry'
import { Perm } from '@/types'
import { useAuthStore } from '@/stores/auth'
import { useDemoStore } from '@/stores/demo'
import { displayIsoToUtc, formatDateTime } from '@/utils/datetime'

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
  }
}

onMounted(refresh)
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

          <el-alert
            v-if="isRealClock"
            type="success"
            :closable="false"
            show-icon
            class="u-mb-12"
            title="当前是真实时间模式（CLOCK_MODE=system）"
            description="系统时间就是现实时间，不需要也不允许推进或跳转，因此下方「快进 / 跳到该时间」已停用；「重置到初始态」仍可用。需要可复现的虚拟时钟演示时，把 CLOCK_MODE 改成 replay 并重启后端。"
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
