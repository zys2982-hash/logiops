<script setup lang="ts">
import { computed } from 'vue'

import { RISK_LEVEL_MAP, riskLevelLabel, riskLevelType } from '@/utils/format'
import type { ExceptionLevel } from '@/types'

const props = withDefaults(
  defineProps<{
    level?: ExceptionLevel | null
    score?: number | null
    size?: 'large' | 'default' | 'small'
    showScore?: boolean
    /** 异常已结束（已解决/已关闭）：没有当前风险 → 显示"无风险 · 0 分"（灰色） */
    ended?: boolean
  }>(),
  { size: 'default', showScore: false, level: null, score: null, ended: false },
)

const label = computed(() => {
  if (props.ended) return props.showScore ? '无风险 · 0 分' : '无风险'
  return props.showScore && props.score !== null && props.score !== undefined
    ? `${riskLevelLabel(props.level)} · ${props.score} 分`
    : riskLevelLabel(props.level)
})
const type = computed(() => (props.ended ? 'info' : riskLevelType(props.level)))
const color = computed(() =>
  props.ended ? '#909399' : props.level ? RISK_LEVEL_MAP[props.level]?.color : '#909399',
)
</script>

<template>
  <el-tag :type="type" :size="size" effect="dark" :style="{ backgroundColor: color, borderColor: color }">
    {{ label }}
  </el-tag>
</template>
