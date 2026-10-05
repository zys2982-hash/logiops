<script setup lang="ts">
/**
 * SLA 影响卡（2026-10-05 新口径：**只有延误单才有 SLA 影响**）：
 * - 承诺到达：规则算（发车 + SLA 规则偏移），只读；
 * - **实际送达**：订单事实（订单点「已送达」时的实际时间）；录错时可用「修改」修正
 *   → 调 `PATCH /orders/{id}/delivered-at`，后端立刻按新时间重算延误单（仍违约→重算；不再违约→自动解决）；
 * - 延误时长 = 实际送达 − 承诺到达（是否违约由规则判定）。
 * 车辆故障单不渲染本卡片（用户口径："普通的车辆异常订单不应该有 sla 影响"）。
 */
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'

import PanelCard from './PanelCard.vue'
import { orderApi } from '@/api'
import { displayIsoToUtc, formatDateTime, formatDelay } from '@/utils/datetime'
import { Perm, type ExceptionDetail } from '@/types'
import { useAuthStore } from '@/stores/auth'

const props = defineProps<{ exception: ExceptionDetail }>()
const emit = defineEmits<{ (e: 'updated'): void }>()

const auth = useAuthStore()
/** 修正实际送达时间改的是**订单**，所以看 order.manage 权限 */
const canEdit = computed(() => auth.can(Perm.ORDER_MANAGE))
const saving = ref(false)
const editing = ref(false)
const draft = ref('')

watch(
  () => props.exception.delivered_at,
  (value) => {
    draft.value = value ? formatDateTime(value, 'YYYY-MM-DD HH:mm') : ''
  },
  { immediate: true },
)

const delayText = computed(() => formatDelay(props.exception.sla_delay_minutes))
const promisedText = computed(() => formatDateTime(props.exception.promised_delivery_at))
const deliveredText = computed(() => formatDateTime(props.exception.delivered_at))

async function save(): Promise<void> {
  if (!draft.value) {
    ElMessage.warning('请选择实际送达时间')
    return
  }
  const deliveredUtc = displayIsoToUtc(draft.value)
  if (!deliveredUtc) {
    ElMessage.warning('实际送达时间格式不正确')
    return
  }
  saving.value = true
  try {
    await orderApi.correctDeliveredAt(props.exception.order_id, {
      delivered_at: deliveredUtc,
      note: '修正实际送达时间（异常单 SLA 卡）',
    })
    ElMessage.success('实际送达时间已修正，延误与风险分已按新时间重算')
    editing.value = false
    emit('updated')
  } catch {
    // 409（订单未送达）/403/422 已由响应拦截器提示
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <PanelCard title="SLA 影响" icon="Timer">
    <el-alert
      :type="exception.sla_breached ? 'error' : 'success'"
      :closable="false"
      show-icon
      class="u-mb-12"
    >
      <template #title>
        {{ exception.sla_breached ? `SLA 已违约 · ${delayText}` : `未违约 · ${delayText}` }}
      </template>
      <div class="u-text-muted">
        {{
          exception.sla_breached
            ? '规则口径：超出允许延迟即违约（§8.3）—— 送达时按实际时间判定'
            : '实际送达仍在允许延迟范围内'
        }}
      </div>
    </el-alert>

    <el-descriptions :column="1" size="small" border>
      <el-descriptions-item label="承诺到达">
        {{ promisedText }}
      </el-descriptions-item>
      <el-descriptions-item label="实际送达">
        <template v-if="editing">
          <el-date-picker
            v-model="draft"
            type="datetime"
            value-format="YYYY-MM-DD HH:mm"
            size="small"
            style="width: 190px"
          />
        </template>
        <template v-else>
          {{ deliveredText }}
          <el-button v-if="canEdit" size="small" text type="primary" @click="editing = true">
            修改
          </el-button>
        </template>
      </el-descriptions-item>
      <el-descriptions-item label="延误时长">
        <el-tag :type="exception.sla_breached ? 'danger' : 'info'" size="small">{{ delayText }}</el-tag>
        <span v-if="editing" class="u-text-muted">= 实际送达 − 承诺到达，保存后按规则重算</span>
      </el-descriptions-item>
    </el-descriptions>

    <div v-if="editing" class="u-mt-8">
      <el-button size="small" type="primary" :loading="saving" @click="save">保存并重算</el-button>
      <el-button size="small" text @click="editing = false">取消</el-button>
    </div>
    <div v-else class="u-text-muted u-mt-8">
      {{ canEdit ? '实际送达时间录错时可用「修改」修正，改完立刻重算延误与风险等级' : '没有 order.manage 权限，实际送达时间只读（§9.2 权限矩阵）' }}
    </div>
  </PanelCard>
</template>
