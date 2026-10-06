<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import PanelCard from '@/components/PanelCard.vue'
import ExceptionTable from '@/components/ExceptionTable.vue'
import RiskTag from '@/components/RiskTag.vue'
import { exceptionApi, systemApi } from '@/api'
import { dashboardSummary as summaryFixture } from '@/mocks/fixtures'
import { exceptionStatusLabel, exceptionStatusType, exceptionTypeLabel } from '@/utils/format'
import type { DashboardExceptionBrief, DashboardSummary, ExceptionListItem } from '@/types'

const router = useRouter()

const summary = ref<DashboardSummary>({ ...summaryFixture })
const topRisk = ref<ExceptionListItem[]>([])
const loading = ref(false)

const cards = computed(() => [
  { key: 'today_orders', label: '今日订单', value: summary.value.today_orders, color: '#2f6fed', icon: 'Van' },
  { key: 'in_transit', label: '运输中', value: summary.value.in_transit, color: '#409eff', icon: 'Location' },
  { key: 'open_exceptions', label: '未关闭异常', value: summary.value.open_exceptions, color: '#e6a23c', icon: 'Warning' },
  { key: 'high_risk', label: '高风险/严重', value: summary.value.high_risk, color: '#c0392b', icon: 'AlarmClock' },
  // 实测字段名是 pending（早期按 pending_handling 写会显示 0）
  { key: 'pending', label: '待处理', value: summary.value.pending, color: '#909399', icon: 'Clock' },
  { key: 'resolved', label: '已解决', value: summary.value.resolved, color: '#67c23a', icon: 'CircleCheck' },
])

/** 待处置异常：未结束的单，后端已按"当前风险倒序 → 挂得越久越靠前"排好 */
const actionQueue = computed<DashboardExceptionBrief[]>(() => summary.value.action_queue ?? [])

/** 已挂时长（分钟）→ `2 天 3 小时` / `6 小时 20 分钟` / `35 分钟` */
function agingText(minutes?: number | null): string {
  if (minutes === null || minutes === undefined) return '—'
  if (minutes < 60) return `${minutes} 分钟`
  const days = Math.floor(minutes / 1440)
  const hours = Math.floor((minutes % 1440) / 60)
  const mins = minutes % 60
  if (days > 0) return `${days} 天${hours > 0 ? ` ${hours} 小时` : ''}`
  return `${hours} 小时${mins > 0 ? ` ${mins} 分钟` : ''}`
}

/** 挂太久给个视觉提示：超过 8 小时标红，超过 2 小时标黄 */
function agingClass(minutes?: number | null): string {
  if (minutes === null || minutes === undefined) return ''
  if (minutes >= 480) return 'log-level-CRITICAL'
  if (minutes >= 120) return 'u-text-warn'
  return ''
}

async function load(): Promise<void> {
  loading.value = true
  try {
    const [summaryResult, listResult] = await Promise.allSettled([
      systemApi.getDashboardSummary(),
      exceptionApi.listExceptions({ page: 1, page_size: 5, sort: '-risk_score,-created_at' }),
    ])
    if (summaryResult.status === 'fulfilled') summary.value = summaryResult.value
    if (listResult.status === 'fulfilled') topRisk.value = listResult.value.items
    // summary 自带 high_risk_top：列表接口异常时用它兜底
    if (topRisk.value.length === 0 && summary.value.high_risk_top?.length) {
      topRisk.value = summary.value.high_risk_top
    }
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="page">
    <el-alert
      type="info"
      :closable="false"
      show-icon
      class="u-mb-16"
      title="异常优先的运营总览"
      description="统计卡与两张表都来自 GET /dashboard/summary：待处置异常＝未结束的单（按当前风险倒序 → 挂得越久越靠前），高风险 Top5＝未结束且当前等级为高/严重；口径与异常中心一致（§8.6）。"
    />

    <el-row :gutter="12" class="u-mb-16">
      <el-col v-for="card in cards" :key="card.key" :xs="12" :sm="8" :md="4">
        <el-card shadow="never" class="stat-card page-card">
          <div class="stat-icon" :style="{ backgroundColor: card.color }">
            <el-icon><component :is="card.icon" /></el-icon>
          </div>
          <div>
            <div class="stat-value">{{ card.value }}</div>
            <div class="u-text-muted">{{ card.label }}</div>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <el-row :gutter="12">
      <el-col :md="14">
        <PanelCard
          title="待处置异常"
          subtitle="未结束的单 · 当前风险倒序 → 挂得越久越靠前"
          icon="AlarmClock"
          class="u-h-full"
        >
          <template #actions>
            <el-button size="small" text type="primary" @click="router.push('/exceptions')">
              进入异常中心
            </el-button>
          </template>
          <el-table
            v-loading="loading"
            :data="actionQueue"
            size="small"
            border
            stripe
            row-key="id"
            empty-text="当前没有待处置异常（待确认 / 处理中）"
            @row-click="(row: DashboardExceptionBrief) => router.push(`/exceptions/${row.id}`)"
          >
            <el-table-column label="异常编号" min-width="150">
              <template #default="{ row }">
                <b class="u-mono">{{ row.case_no }}</b>
                <div class="u-text-muted">
                  <router-link :to="`/orders/${row.order_id}`" class="order-link" @click.stop>
                    {{ row.order_no ?? `#${row.order_id}` }}
                  </router-link>
                </div>
              </template>
            </el-table-column>
            <el-table-column label="客户" min-width="120">
              <template #default="{ row }">{{ row.customer_name ?? '—' }}</template>
            </el-table-column>
            <el-table-column label="当前问题" width="100" align="center">
              <template #default="{ row }">
                <el-tag size="small" effect="plain">{{ exceptionTypeLabel(row.current_type ?? row.type) }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="当前风险" width="130" align="center">
              <template #default="{ row }">
                <RiskTag
                  :level="row.current_level ?? row.level"
                  :score="row.current_risk_score ?? row.risk_score"
                  show-score
                />
              </template>
            </el-table-column>
            <el-table-column label="状态" width="90" align="center">
              <template #default="{ row }">
                <el-tag size="small" :type="exceptionStatusType(row.status)">
                  {{ exceptionStatusLabel(row.status) }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="承运商" min-width="110">
              <template #default="{ row }">{{ row.carrier_name ?? '—' }}</template>
            </el-table-column>
            <el-table-column label="已挂时长" width="110" align="center">
              <template #default="{ row }">
                <span :class="agingClass(row.age_minutes)">{{ agingText(row.age_minutes) }}</span>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="80" fixed="right">
              <template #default="{ row }">
                <router-link :to="`/exceptions/${row.id}`" @click.stop>
                  <el-button size="small" text type="primary">详情</el-button>
                </router-link>
              </template>
            </el-table-column>
          </el-table>
        </PanelCard>
      </el-col>

      <el-col :md="10">
        <PanelCard title="高风险异常 Top5" subtitle="按 risk_score 倒序" icon="WarnTriangleFilled">
          <template #actions>
            <el-button size="small" text type="primary" @click="router.push('/exceptions')">进入异常中心</el-button>
          </template>
          <ExceptionTable :items="topRisk" :loading="loading" @row-click="(row) => router.push(`/exceptions/${row.id}`)" />
        </PanelCard>
      </el-col>
    </el-row>
  </div>
</template>

<style scoped>
.stat-card {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 12px;
}

.stat-card :deep(.el-card__body) {
  display: flex;
  align-items: center;
  gap: 10px;
}

.stat-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  border-radius: 8px;
  color: #fff;
  flex: none;
}

.stat-value {
  font-size: 22px;
  font-weight: 700;
  line-height: 1.1;
}

.chart {
  display: flex;
  align-items: flex-end;
  gap: 12px;
  height: 200px;
  padding: 8px 0;
}

.chart-col {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  height: 100%;
  justify-content: flex-end;
  font-size: 11px;
}

.chart-bars {
  display: flex;
  align-items: flex-end;
  gap: 4px;
  height: 130px;
}

.chart-bar {
  width: 14px;
  border-radius: 3px 3px 0 0;
  min-height: 2px;
  transition: height 0.3s ease;
}

.chart-bar--exception {
  background: #e6a23c;
}

.chart-bar--breach {
  background: #f56c6c;
}
</style>
