<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import PanelCard from '@/components/PanelCard.vue'
import ExceptionTable from '@/components/ExceptionTable.vue'
import { exceptionApi, systemApi } from '@/api'
import { dashboardSummary as summaryFixture, dashboardTrend as trendFixture } from '@/mocks/fixtures'
import { formatDate } from '@/utils/datetime'
import type { DashboardSummary, DashboardTrendPoint, ExceptionListItem } from '@/types'

const router = useRouter()

const summary = ref<DashboardSummary>({ ...summaryFixture })
const trend = ref<DashboardTrendPoint[]>([...(trendFixture.items ?? [])])
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

const slaBreached = computed(() => summary.value.sla_breached_open ?? summary.value.sla_breached)

/** 趋势点实测字段是 detected / breached（旧命名 exceptions / sla_breached 仅作兼容） */
function pointDetected(point: DashboardTrendPoint): number {
  return point.detected ?? point.exceptions ?? 0
}

function pointBreached(point: DashboardTrendPoint): number {
  return point.breached ?? point.sla_breached ?? 0
}

const maxTrend = computed(() =>
  Math.max(1, ...trend.value.map((point) => Math.max(pointDetected(point), pointBreached(point)))),
)

function barHeight(value: number): string {
  return `${Math.round((value / maxTrend.value) * 100)}%`
}

async function load(): Promise<void> {
  loading.value = true
  try {
    const [summaryResult, trendResult, listResult] = await Promise.allSettled([
      systemApi.getDashboardSummary(),
      systemApi.getDashboardTrend(),
      exceptionApi.listExceptions({ page: 1, page_size: 5, sort: '-risk_score,-created_at' }),
    ])
    if (summaryResult.status === 'fulfilled') summary.value = summaryResult.value
    if (trendResult.status === 'fulfilled') trend.value = trendResult.value.items ?? []
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
      description="统计卡来自 GET /dashboard/summary，趋势来自 GET /dashboard/trend；高风险 Top5 按 risk_score desc 排序，与异常中心同一套规则等级（§8.6）。"
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
        <PanelCard title="近 7 天异常与违约趋势" icon="TrendCharts">
          <template #actions>
            <el-tag size="small" type="warning" effect="plain">异常</el-tag>
            <el-tag size="small" type="danger" effect="plain">SLA 违约</el-tag>
          </template>
          <div class="chart">
            <div v-for="point in trend" :key="point.date" class="chart-col">
              <div class="chart-bars">
                <div
                  class="chart-bar chart-bar--exception"
                  :style="{ height: barHeight(pointDetected(point)) }"
                  :title="`异常 ${pointDetected(point)}`"
                />
                <div
                  class="chart-bar chart-bar--breach"
                  :style="{ height: barHeight(pointBreached(point)) }"
                  :title="`违约 ${pointBreached(point)}`"
                />
              </div>
              <div class="u-text-muted">{{ formatDate(point.date).slice(5) }}</div>
              <div class="u-text-muted">{{ pointDetected(point) }}/{{ pointBreached(point) }}</div>
            </div>
          </div>
          <div class="u-text-muted u-mt-8">
            今日 SLA 违约数：<b class="log-level-CRITICAL">{{ slaBreached }}</b>（seed 数据，供演示折线）
          </div>
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
