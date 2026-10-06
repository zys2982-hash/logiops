<script setup lang="ts">
import RiskTag from './RiskTag.vue'
import { formatDateTime } from '@/utils/datetime'
import {
  customerLevelLabel,
  exceptionStatusLabel,
  exceptionStatusType,
  exceptionTypeLabel,
  formatNumber,
} from '@/utils/format'
import type { ExceptionListItem } from '@/types'

defineProps<{
  items: ExceptionListItem[]
  loading?: boolean
  showOrder?: boolean
  emptyText?: string
}>()

const emit = defineEmits<{ (e: 'row-click', row: ExceptionListItem): void }>()

/** 列表接口不返回 customer_level，优先用嵌套 customer.level（详情场景），否则留空 */
function levelText(row: ExceptionListItem): string {
  const level = row.customer?.level ?? row.customer_level
  return level ? customerLevelLabel(level) : ''
}

/** 二级说明：客户等级 + 车牌（两者都没有时显示占位） */
function subText(row: ExceptionListItem): string {
  return [levelText(row), row.vehicle_plate].filter((v) => v).join(' · ') || '—'
}

/** 已结束（已解决 / 已关闭）：没有"当前风险"，等级列显示 0 */
function isEnded(status?: string | null): boolean {
  return status === 'RESOLVED' || status === 'CLOSED'
}
</script>

<template>
  <el-table
    v-loading="loading"
    :data="items"
    size="small"
    border
    stripe
    row-key="id"
    :empty-text="emptyText ?? '暂无异常数据'"
    @row-click="(row: ExceptionListItem) => emit('row-click', row)"
  >
    <el-table-column label="异常单号" min-width="170">
      <template #default="{ row }">
        <!-- 主行＝**异常单号**（点它进异常详情）；副行＝这张异常挂在哪张**运输订单**上。
             原来是反的（蓝字给订单号、灰字给异常号），第一次看的人会分不清（用户反馈 2026-10-06）。 -->
        <router-link :to="`/exceptions/${row.id}`" class="case-link" @click.stop>
          {{ row.case_no }}
        </router-link>
        <div class="order-sub">
          <span class="u-text-muted">订单 </span>
          <router-link :to="`/orders/${row.order_id}`" class="order-link" @click.stop>
            {{ row.order_no ?? `#${row.order_id}` }}
          </router-link>
        </div>
      </template>
    </el-table-column>
    <el-table-column label="客户" min-width="150">
      <template #default="{ row }">
        <div>{{ row.customer_name ?? `#${row.customer_id}` }}</div>
        <div class="u-text-muted">{{ subText(row) }}</div>
      </template>
    </el-table-column>
    <el-table-column label="等级" width="110" align="center">
      <template #default="{ row }">
        <!-- 「等级」列显示**当前风险**：已解决/已关闭的单没有当前风险 → 无风险 · 0 分 -->
        <RiskTag
          :level="row.current_level ?? row.level"
          :score="row.current_risk_score ?? row.risk_score"
          :ended="isEnded(row.status)"
          show-score
        />
      </template>
    </el-table-column>
    <el-table-column label="异常类别" width="120" align="center">
      <template #default="{ row }">
        <!-- 2026-10-06：列表把「SLA 影响」换成「异常类别」。与全局口径一致，显示**当前问题**：
             未结束的单按风险因子实时推导（车修好了就会变成延误风险）；已结束的沿用建单原因。
             SLA 影响（承诺/实际/延误）在异常详情的 SLA 卡里看。 -->
        <el-tag size="small" effect="plain">{{ exceptionTypeLabel(row.current_type ?? row.type) }}</el-tag>
      </template>
    </el-table-column>
    <el-table-column label="状态" width="110" align="center">
      <template #default="{ row }">
        <el-tag size="small" :type="exceptionStatusType(row.status)">{{ exceptionStatusLabel(row.status) }}</el-tag>
      </template>
    </el-table-column>
    <el-table-column label="更新时间" width="150">
      <template #default="{ row }">{{ formatDateTime(row.updated_at ?? row.created_at) }}</template>
    </el-table-column>
    <el-table-column v-if="showOrder" label="合并" width="70" align="center">
      <template #default="{ row }">{{ formatNumber(row.merged_count) }}</template>
    </el-table-column>
    <el-table-column label="操作" width="100" fixed="right">
      <template #default="{ row }">
        <router-link :to="`/exceptions/${row.id}`" @click.stop>
          <el-button size="small" text type="primary">详情</el-button>
        </router-link>
      </template>
    </el-table-column>
  </el-table>
</template>

<style scoped>
/* 主行：异常单号（可点 → 异常详情） */
.case-link {
  color: #2f6fed;
  text-decoration: none;
  font-weight: 600;
}

/* 副行：这张异常对应的运输订单号（可点 → 订单详情） */
.order-sub {
  font-size: 12px;
  line-height: 1.6;
}
.order-link {
  color: #6b7280;
  text-decoration: none;
}
.order-link:hover {
  color: #2f6fed;
  text-decoration: underline;
}
</style>
