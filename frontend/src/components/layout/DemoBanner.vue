<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import { Perm } from '@/types'
import { useAuthStore } from '@/stores/auth'
import { useDemoStore } from '@/stores/demo'

const auth = useAuthStore()
const demo = useDemoStore()

const canControl = computed(() => auth.can(Perm.DEMO_CONTROL))
const switching = ref(false)

/** 切换 AI 模式：有 demo.control 才可点；切到 live 先二次确认（会真实联网调用、产生费用） */
async function onAiModeChange(value: string | number | boolean | undefined): Promise<void> {
  const mode: 'replay' | 'live' = value === 'live' ? 'live' : 'replay'
  if (mode === demo.aiMode) return
  if (mode === 'live') {
    try {
      await ElMessageBox.confirm(
        '切到「真实大模型」后，每次 AI 分析都会真实调用 DeepSeek：需要联网、产生费用、结果每次可能不同。继续？',
        '切换到实时调用',
        { type: 'warning', confirmButtonText: '切到 live', cancelButtonText: '保持回放' },
      )
    } catch {
      return
    }
  }
  switching.value = true
  try {
    await demo.setAiMode(mode)
    if (demo.lastError) {
      ElMessage.error(`AI 模式切换失败：${demo.lastError}`)
      return
    }
    if (demo.lastWarning) {
      ElMessage.warning(demo.lastWarning)
      return
    }
    ElMessage.success(
      mode === 'live'
        ? '已切到真实大模型（live）：下一个分析就会真实调用模型'
        : '已切到回放样本（replay）：0 成本、结果可复现',
    )
  } finally {
    switching.value = false
  }
}

onMounted(() => {
  void demo.refresh()
})
</script>

<template>
  <div class="demo-banner">
    <el-icon><InfoFilled /></el-icon>
    <el-tooltip
      placement="bottom"
      raw-content
      content="回放样本 = 读取预录结果：0 成本、完全可复现（面试演示推荐）。<br/>真实大模型 = 现场调用 DeepSeek：需要联网、产生费用、结果每次可能不同。<br/>切换立即生效；重启后端后回到 .env 里的 AI_MODE。"
    >
      <span class="banner-source">AI 分析来源：</span>
    </el-tooltip>
    <el-radio-group
      v-if="canControl"
      :model-value="demo.aiMode"
      size="small"
      :disabled="switching"
      @change="onAiModeChange"
    >
      <el-radio-button value="replay">回放样本</el-radio-button>
      <el-radio-button value="live">真实大模型</el-radio-button>
    </el-radio-group>
    <b v-else>{{ demo.isReplay ? '回放样本（replay）' : '真实大模型（live）' }}</b>
    <el-divider direction="vertical" />
    <span>
      业务时间：
      <b>{{ demo.businessTimeText }}</b>
      <el-tag
        v-if="demo.clockMode === 'system'"
        size="small"
        type="success"
        effect="plain"
      >
        真实时间
      </el-tag>
      <el-tooltip
        v-else
        placement="bottom"
        content="演示时钟：可快进 / 跳转，用于演示 ETA 重算与异常检测（异常的解决/关闭仍需人工点）；它与真实时间不同，界面上所有业务时间都取自它。"
      >
        <el-tag size="small" type="warning" effect="plain">演示时钟（模拟）</el-tag>
      </el-tooltip>
    </span>
    <el-tag v-if="demo.mocked" size="small" type="warning" effect="plain">本地演示数据</el-tag>
    <span class="banner-actions">
      <router-link to="/demo">演示工具</router-link>
      <el-button size="small" text type="primary" :loading="demo.loading" @click="demo.refresh()">
        刷新
      </el-button>
    </span>
  </div>
</template>

<style scoped>
.demo-banner {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  padding: 6px 16px;
  font-size: 12px;
  background: #fdf6ec;
  border-bottom: 1px solid #f5dab1;
  color: #9a6a1f;
}

.banner-actions {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 8px;
}

/* 提示"来源说明"可悬浮看解释 */
.banner-source {
  cursor: help;
  text-decoration: underline dotted;
  text-underline-offset: 3px;
}
</style>
