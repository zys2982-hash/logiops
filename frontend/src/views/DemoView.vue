<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import PanelCard from '@/components/PanelCard.vue'
import { resetMockState } from '@/mocks/registry'
import { Perm } from '@/types'
import { useAuthStore } from '@/stores/auth'
import { useDemoStore } from '@/stores/demo'
import { formatDateTime } from '@/utils/datetime'

const auth = useAuthStore()
const demo = useDemoStore()

const tickMinutes = ref(60)
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
  logAction('GET /demo/state')
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

function switchAiMode(mode: 'replay' | 'live'): void {
  demo.state = { ...demo.state, ai_mode: mode }
  logAction(`本地切换 AI 模式 → ${mode}（正式环境由 system_setting ai.mode 控制）`)
  ElMessage.info(`界面已切换为 ${mode === 'replay' ? '回放' : '实时'} 模式展示；后端模式由 /demo/state 返回`)
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
      title="Demo 控制台（仅本地演示）"
      description="权限：APP_ENV=local 且 ADMIN+ 才有 demo.control（后端 /demo 路由统一依赖 require(Perm.DEMO_CONTROL)）。推进时钟会触发轨迹生成 → ETA 重算 → 异常检测 → 审批过期检查 → 自动关闭检查。"
    />

    <el-row :gutter="12">
      <el-col :md="14">
        <PanelCard title="时钟与场景控制" icon="MagicStick">
          <template #actions>
            <el-tag v-if="!canControl" size="small" effect="plain">当前角色无 demo.control</el-tag>
            <el-tag v-else size="small" type="success" effect="plain">可控制</el-tag>
          </template>

          <el-descriptions :column="2" size="small" border class="u-mb-12">
            <el-descriptions-item label="业务时间">{{ demo.businessTimeText }}</el-descriptions-item>
            <el-descriptions-item label="基准日">{{ demo.baseDateText }}</el-descriptions-item>
            <el-descriptions-item label="时钟偏移">{{ demo.offsetMinutes }} 分钟</el-descriptions-item>
            <el-descriptions-item label="clock_mode">{{ demo.clockMode }}</el-descriptions-item>
            <el-descriptions-item label="AI 模式">
              <el-tag size="small" :type="demo.isReplay ? 'warning' : 'danger'" effect="plain">
                {{ demo.aiMode }}
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
            <el-form-item label="重置场景">
              <el-select v-model="scenario" style="width: 100%">
                <el-option v-for="option in scenarios" :key="option.value" :label="option.label" :value="option.value" />
              </el-select>
            </el-form-item>
            <el-form-item label="AI 模式">
              <el-radio-group :model-value="demo.aiMode" @change="(value: string | number | boolean | undefined) => switchAiMode(value === 'live' ? 'live' : 'replay')">
                <el-radio-button value="replay">回放 replay</el-radio-button>
                <el-radio-button value="live">实时 live</el-radio-button>
              </el-radio-group>
            </el-form-item>
            <el-form-item>
              <el-button type="primary" :loading="busy" @click="tick">推进 {{ tickMinutes }} 分钟</el-button>
              <el-button :loading="busy" @click="advance">推进到送达（advance-to-less）</el-button>
              <el-button type="danger" plain :loading="busy" @click="reset">重置 seed</el-button>
              <el-button text type="primary" @click="refresh">刷新状态</el-button>
            </el-form-item>
          </el-form>
        </PanelCard>
      </el-col>

      <el-col :md="10">
        <PanelCard title="本地操作记录" subtitle="最近 20 次控制台动作" icon="Clock">
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
