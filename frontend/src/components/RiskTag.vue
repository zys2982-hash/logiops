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
  }>(),
  { size: 'default', showScore: false, level: null, score: null },
)

const label = computed(() =>
  props.showScore && props.score !== null && props.score !== undefined
    ? `${riskLevelLabel(props.level)} · ${props.score} 分`
    : riskLevelLabel(props.level),
)
const type = computed(() => riskLevelType(props.level))
const color = computed(() => (props.level ? RISK_LEVEL_MAP[props.level]?.color : '#909399'))
</script>

<template>
  <el-tag :type="type" :size="size" effect="dark" :style="{ backgroundColor: color, borderColor: color }">
    {{ label }}
  </el-tag>
</template>
