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
const scenario = ref('case-a')
const busy = ref(false)
const timeline = ref<Array<{ time: string; action: string }>>([])

const canControl = computed(() => auth.can(Perm.DEMO_CONTROL))

const scenarios = [
  { value: 'case-a', label: 'CASE-A 主案例（车辆故障全闭环，CRITICAL）' },
  { value: 'case-b', label: 'CASE-B 已完成（VIP 单：分析/审批/通知/跟进/关闭）' },
  { value: 'case-c', label: 'CASE-C 误报（装卸排队，close(reason=INVALID)）' },
  { value: 'case-d', label: 'CASE-D 边界（延误 25min < 允许 30min，MEDIUM，不违约）' },
  { value: 'case-e', label: 'CASE-E 高并发感（5 单不同等级/状态）' },
]

function logAction(action: string): void {
  timeline.value.unshift({ time: new Date().toLocaleTimeString('zh-CN'), action })
  timeline.value = timeline.value.slice(0, 20)
}

async function refresh(): Promise<void> {
  await demo.refresh()
  // 默认把选择器填成当前业务时间，方便"微调式"跳转；已选过就不覆盖
  if (!targetTime.value) fillTargetFromNow()
  logAction('GET /demo/state')
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
    logAction(`POST /demo/actions/set-clock {target_utc: "${targetUtc}"}`)
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
    logAction(`POST /demo/actions/tick {minutes: ${tickMinutes.value}}`)
    // 后端明确拒绝时（例如状态机 409）不谎报成功，错误提示已由拦截器给出
    if (demo.lastError) {
      logAction(`tick 失败：${demo.lastError}`)
      ElMessage.error(`推进失败：${demo.lastError}`)
      return
    }
    ElMessage.success(demo.lastAction ?? '时钟已推进')
  } finally {
    busy.value = false
  }
}

async function advance(): Promise<void> {
  busy.value = true
  try {
    await demo.advanceToLess()
    logAction('POST /demo/actions/advance-to-less')
    if (demo.lastError) {
      logAction(`advance 失败：${demo.lastError}`)
      ElMessage.error(`推进到送达失败：${demo.lastError}`)
      return
    }
    ElMessage.success(demo.lastAction ?? '已推进到送达')
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
    await demo.reset(scenario.value)
    // 本地 fixture 状态一并重置，保证无后端时也能恢复初始演示数据
    resetMockState()
    logAction(`POST /demo/actions/reset {scenario: "${scenario.value}"}`)
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
      description="这一页只做两件事：① 快进/重置「虚拟时钟」——系统里的业务时间默认停在基准日不动，推一下它才往前流（推进即触发：轨迹生成 → ETA 重算 → 异常检测 → 审批过期检查 → 自动关闭检查）；② 一键重置回到 seed 初始态（演示翻车 3 秒恢复）。权限：APP_ENV=local 且 ADMIN+ 才有 demo.control。下方「AI 分析来源」是只读说明（回放样本 / 真实大模型），切换请改后端 AI_MODE。"
    />

    <el-row :gutter="12">
      <el-col :md="14">
        <PanelCard title="虚拟时间控制（快进 / 重置）" icon="MagicStick">
          <template #actions>
            <el-tag v-if="!canControl" size="small" effect="plain">当前角色无 demo.control</el-tag>
            <el-tag v-else size="small" type="success" effect="plain">可控制</el-tag>
          </template>

          <el-descriptions :column="2" size="small" border class="u-mb-12">
            <el-descriptions-item label="业务时间">{{ demo.businessTimeText }}</el-descriptions-item>
            <el-descriptions-item label="基准日">{{ demo.baseDateText }}</el-descriptions-item>
            <el-descriptions-item label="时钟偏移">{{ demo.offsetMinutes }} 分钟</el-descriptions-item>
            <el-descriptions-item label="clock_mode">{{ demo.clockMode }}</el-descriptions-item>
            <el-descriptions-item label="AI 分析来源">
              <el-tag size="small" :type="demo.isReplay ? 'warning' : 'danger'" effect="plain">
                {{ demo.isReplay ? '回放样本（replay）' : '真实大模型（live）' }}
              </el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="workspace_id">{{ demo.state.workspace_id ?? '—' }}</el-descriptions-item>
            <el-descriptions-item label="now_utc">{{ formatDateTime(demo.state.now_utc, 'YYYY-MM-DD HH:mm:ss') }}</el-descriptions-item>
            <el-descriptions-item label="seed_available">{{ demo.state.seed_available ?? '—' }}</el-descriptions-item>
          </el-descriptions>

          <el-form label-width="120px" :disabled="!canControl">
            <el-form-item label="推进分钟数">
              <el-input-number v-model="tickMinutes" :min="1" :max="43200" :step="30" />
              <span class="u-text-muted u-mt-8">（tick 会触发全链路自动执行）</span>
            </el-form-item>
            <el-form-item label="跳转到时间">
              <div class="jump-row">
                <el-date-picker
                  v-model="targetTime"
                  type="datetime"
                  placeholder="点这里选年月日时分秒"
                  format="YYYY-MM-DD HH:mm:ss"
                  value-format="YYYY-MM-DD HH:mm:ss"
                  :disabled="!canControl"
                  @keyup.enter="jumpToTime"
                />
                <el-button type="warning" :loading="busy" :disabled="!canControl" @click="jumpToTime">
                  跳到该时间 →
                </el-button>
              </div>
              <span class="u-text-muted">
                操作两步：① 在弹出面板里选好年月日时分秒并点面板右下角「确定」；② 点右侧「跳到该时间 →」执行。
                提交时按本地时区（Asia/Shanghai）转成 UTC。
              </span>
            </el-form-item>
            <el-form-item label="重置场景">
              <el-select v-model="scenario" style="width: 100%">
                <el-option v-for="option in scenarios" :key="option.value" :label="option.label" :value="option.value" />
              </el-select>
            </el-form-item>
            <el-form-item>
              <el-button type="primary" :loading="busy" @click="tick">快进 {{ tickMinutes }} 分钟</el-button>
              <el-button :loading="busy" @click="advance">快进到结案（推进到送达）</el-button>
              <el-button type="danger" plain :loading="busy" @click="reset">重置到初始态</el-button>
              <el-button text type="primary" @click="refresh">刷新状态</el-button>
            </el-form-item>
            <div class="u-text-muted" style="line-height: 1.7">
              1）<b>快进 N 分钟</b>：让虚拟时间走 N 分钟，顺便跑一遍自动链路（全库都会动）。<br />
              2）<b>跳到该时间</b>：选好年月日时分秒，直接把虚拟时钟设到那一刻（秒级精确；只改时钟，不跑业务链）。
              跳完想立刻触发一次检测/自动关闭，再点一次「快进 1 分钟」即可。<br />
              3）<b>快进到结案</b>：一直快进到主案例（带承运商消息那单）送达并自动关闭，中途遇到"等修车 / 等送达后 24h"会直接跳过去（只推这一单）。<br />
              4）<b>重置到初始态</b>：重建 seed、时钟归零，回到 2026-09-30 01:00 的标准演示起点。
            </div>
          </el-form>
        </PanelCard>
      </el-col>

      <el-col :md="10">
        <PanelCard title="本地操作记录" subtitle="最近 20 次操作" icon="Clock">
          <el-empty v-if="timeline.length === 0" description="暂无操作" :image-size="50" />
          <ul class="action-list">
            <li v-for="(item, index) in timeline" :key="index">
              <span class="u-mono u-text-muted">{{ item.time }}</span>
              <span>{{ item.action }}</span>
            </li>
          </ul>
        </PanelCard>

        <PanelCard title="脚本化案例（§13.3）" icon="Film" class="u-mt-12">
          <el-table :data="scenarios" size="small" border>
            <el-table-column prop="label" label="案例" min-width="200" />
          </el-table>
          <div class="u-text-muted u-mt-8">
            主案例：SO20260930021 天津→上海 VIP-01，19:00 济南停滞 ≥120min → STALL_OVER_THRESHOLD 建单；
            承运商消息“车在济南爆胎了…预计晚上 8 点恢复”；expected ETA 22:30，延误 270min，CRITICAL。
          </div>
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
