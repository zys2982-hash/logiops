<script setup lang="ts">
/**
 * SLA 影响卡（**口径 2026-10-08：延误按「预计到达时间」判定**）：
 * - 承诺到达：规则算（发车 + SLA 规则偏移），只读；
 * - **预计到达**：判定时点（订单的 `planned_delivery_at`），录错时用「修改」改正
 *   → `PATCH /orders/{id}`，后端立刻按新时间重算该订单的延误单（仍不自动收口，等人工点）；
 * - 延误时长 = 预计到达 − 承诺到达（是否违约由规则判定）。
 *
 * 「实际送达」自 2026-10-08 起**不再参与延误判定**，所以卡片不再显示它
 * （订单页仍保留「修正实际送达时间」，那是订单事实的纠错入口）。
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
/** 改的是**订单**上的预计到达时间，所以看 order.manage 权限 */
const canEdit = computed(() => auth.can(Perm.ORDER_MANAGE))
const saving = ref(false)
const editing = ref(false)
const draft = ref('')

watch(
  () => props.exception.planned_delivery_at,
  (value) => {
    draft.value = value ? formatDateTime(value, 'YYYY-MM-DD HH:mm') : ''
  },
  { immediate: true },
)

const delayText = computed(() => formatDelay(props.exception.sla_delay_minutes))
const promisedText = computed(() => formatDateTime(props.exception.promised_delivery_at))
const plannedText = computed(() => formatDateTime(props.exception.planned_delivery_at))

async function save(): Promise<void> {
  if (!draft.value) {
    ElMessage.warning('请选择预计到达时间')
    return
  }
  const plannedUtc = displayIsoToUtc(draft.value)
  if (!plannedUtc) {
    ElMessage.warning('预计到达时间格式不正确')
    return
  }
  saving.value = true
  try {
    await orderApi.updateOrder(props.exception.order_id, { planned_delivery_at: plannedUtc })
    ElMessage.success('预计到达时间已修正，延误与风险分已按新时间重算')
    editing.value = false
    emit('updated')
  } catch {
    // 403 / 409 / 422 已由响应拦截器提示
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
            ? '规则口径：超出允许延迟即违约（§8.3）—— 按「预计到达时间」判定'
            : '预计到达仍在允许延迟范围内'
        }}
      </div>
    </el-alert>

    <el-descriptions :column="1" size="small" border>
      <el-descriptions-item label="承诺到达">
        {{ promisedText }}
      </el-descriptions-item>
      <el-descriptions-item label="预计到达">
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
          {{ plannedText }}
          <el-button v-if="canEdit" size="small" text type="primary" @click="editing = true">
            修改
          </el-button>
        </template>
      </el-descriptions-item>
      <el-descriptions-item label="延误时长">
        <el-tag :type="exception.sla_breached ? 'danger' : 'info'" size="small">{{ delayText }}</el-tag>
        <span v-if="editing" class="u-text-muted">= 预计到达 − 承诺到达，保存后按规则重算</span>
      </el-descriptions-item>
    </el-descriptions>

    <div v-if="editing" class="u-mt-8">
      <el-button size="small" type="primary" :loading="saving" @click="save">保存并重算</el-button>
      <el-button size="small" text @click="editing = false">取消</el-button>
    </div>
    <div v-else class="u-text-muted u-mt-8">
      {{
        canEdit
          ? '预计到达时间由运营在订单页登记；录错时可用「修改」改正，改完立刻重算延误与风险等级'
          : '没有 order.manage 权限，预计到达时间只读（§9.2 权限矩阵）'
      }}
    </div>
  </PanelCard>
</template>
