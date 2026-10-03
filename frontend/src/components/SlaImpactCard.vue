<script setup lang="ts">
/**
 * SLA 影响卡（docs/08 新口径）：
 * - 承诺到达：规则算（发车 + SLA 规则偏移），只读
 * - 预计送达：**人工录入**（有 exception.handle 权限时可改）→ 提交时换算成"延误分钟"，
 *   由后端规则重新判定是否违约
 * - 不再展示「当前 ETA」（程序按车速推算的 ETA 已废弃）
 */
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'

import PanelCard from './PanelCard.vue'
import { exceptionApi } from '@/api'
import { displayIsoToUtc, formatDateTime, formatDelay } from '@/utils/datetime'
import { Perm, type ExceptionDetail } from '@/types'
import { useAuthStore } from '@/stores/auth'

const props = defineProps<{ exception: ExceptionDetail }>()
const emit = defineEmits<{ (e: 'updated'): void }>()

const auth = useAuthStore()
const canEdit = computed(() => auth.can(Perm.EXCEPTION_HANDLE))
const saving = ref(false)
const editing = ref(false)
const draft = ref('')

watch(
  () => props.exception.expected_eta_at,
  (value) => {
    draft.value = value ? formatDateTime(value, 'YYYY-MM-DD HH:mm') : ''
  },
  { immediate: true },
)

const delayText = computed(() => formatDelay(props.exception.sla_delay_minutes))

/** 承诺到达（规则算的承诺） */
const promisedText = computed(() => formatDateTime(props.exception.promised_delivery_at))

async function save(): Promise<void> {
  if (!draft.value) {
    ElMessage.warning('请选择预计送达时间')
    return
  }
  const promised = props.exception.promised_delivery_at
  const version = props.exception.version
  const expectedUtc = displayIsoToUtc(draft.value)
  if (!promised || !expectedUtc) {
    ElMessage.warning('该异常缺少承诺到达时间，无法换算延误')
    return
  }
  if (typeof version !== 'number') {
    ElMessage.warning('异常版本信息缺失，请刷新后重试')
    return
  }
  // 延误 = 预计送达 − 承诺到达（人工提供两个时间事实，规则负责判违约）
  const minutes = Math.max(0, Math.round((Date.parse(expectedUtc) - Date.parse(promised)) / 60000))
  saving.value = true
  try {
    await exceptionApi.recordDelay(props.exception.id, {
      expected_version: version,
      delay_minutes: minutes,
      note: '人工录入预计送达',
    })
    ElMessage.success(`预计送达已更新；延误 ${minutes} 分钟，是否违约由规则判定`)
    editing.value = false
    emit('updated')
  } catch {
    // 409（版本冲突）/422 已由响应拦截器提示
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <PanelCard title="SLA 影响" icon="Timer">
    <el-alert
      :type="exception.sla_breached ? 'error' : exception.sla_delay_minutes && exception.sla_delay_minutes > 0 ? 'warning' : 'success'"
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
            ? '规则口径：超出允许延迟即违约（§8.3）'
            : '仍在允许延迟范围内'
        }}
      </div>
    </el-alert>

    <el-descriptions :column="1" size="small" border>
      <el-descriptions-item label="承诺到达">
        {{ promisedText }}
      </el-descriptions-item>
      <el-descriptions-item label="预计送达">
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
          {{ formatDateTime(exception.expected_eta_at) }}
          <el-button v-if="canEdit" size="small" text type="primary" @click="editing = true">
            修改
          </el-button>
        </template>
      </el-descriptions-item>
      <el-descriptions-item label="延误时长">
        <el-tag :type="exception.sla_breached ? 'danger' : 'info'" size="small">{{ delayText }}</el-tag>
        <span v-if="editing" class="u-text-muted">
          = 预计送达 − 承诺到达，保存后由规则重判是否违约
        </span>
      </el-descriptions-item>
    </el-descriptions>

    <div v-if="editing" class="u-mt-8">
      <el-button size="small" type="primary" :loading="saving" @click="save">保存预计送达</el-button>
      <el-button size="small" text @click="editing = false">取消</el-button>
    </div>
    <div v-else-if="!canEdit" class="u-text-muted u-mt-8">
      没有 exception.handle 权限，预计送达只读（§9.2 权限矩阵）
    </div>
  </PanelCard>
</template>
