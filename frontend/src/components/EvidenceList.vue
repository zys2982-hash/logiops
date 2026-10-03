<script setup lang="ts">
/** 证据来源列表（AI 输出的 evidence_refs）。
 *
 *  跳转规则（"点击可跳转"必须是真的）：
 *  1) 同页有对应区域（承运商消息 / 操作记录时间线）→ 平滑滚动过去并短暂高亮；
 *  2) 否则跳到有意义的页面：订单详情 / 异常详情 / SLA 规则 / 车辆 / 知识库；
 *  3) 都没有的（理论上不该出现）→ 不显示可点样式，避免"点了没反应"。
 */
import { useRouter } from 'vue-router'
import type { AiEvidenceRef } from '@/types'

const props = defineProps<{
  refs: AiEvidenceRef[]
  /** 本页异常所属订单：轨迹证据在本页找不到锚点时，跳到订单详情看轨迹 */
  orderId?: number | null
  /** 本页异常 id：承运商消息锚点缺失时，跳回异常详情 */
  exceptionId?: number | null
}>()

const router = useRouter()

const TYPE_LABELS: Record<string, string> = {
  ORDER: '订单',
  TRACKING_EVENT: '轨迹事件',
  CARRIER_MESSAGE: '承运商消息',
  KNOWLEDGE_CHUNK: '知识库分片',
  SLA_RULE: 'SLA 规则',
  VEHICLE: '车辆',
  EXCEPTION: '异常单',
  CUSTOMER: '客户',
}

function typeLabel(type: string): string {
  return TYPE_LABELS[type] ?? type
}

/** 同页锚点（存在就优先滚动，体验最顺） */
function anchorOf(type: string): string | null {
  if (type === 'CARRIER_MESSAGE') return 'evidence-carrier'
  if (type === 'TRACKING_EVENT') return 'evidence-timeline'
  return null
}

/** 锚点不存在时的路由目标；null = 该类证据没有可跳页面 */
function routeOf(ref: AiEvidenceRef): string | null {
  switch (ref.type) {
    case 'ORDER':
      return `/orders/${ref.id}`
    case 'TRACKING_EVENT':
      return props.orderId ? `/orders/${props.orderId}` : '/orders'
    case 'CARRIER_MESSAGE':
      return props.exceptionId ? `/exceptions/${props.exceptionId}` : null
    case 'SLA_RULE':
      return '/sla-rules'
    case 'VEHICLE':
      return '/vehicles'
    case 'KNOWLEDGE_CHUNK':
      return '/knowledge'
    case 'EXCEPTION':
      return `/exceptions/${ref.id}`
    default:
      return null
  }
}

function canJump(ref: AiEvidenceRef): boolean {
  return anchorOf(ref.type) !== null || routeOf(ref) !== null
}

/** 鼠标悬浮提示：明确告诉用户"点了会去哪" */
function jumpHint(ref: AiEvidenceRef): string {
  switch (ref.type) {
    case 'CARRIER_MESSAGE':
      return '定位到本页「承运商消息」'
    case 'TRACKING_EVENT':
      return '定位到本页「运输轨迹时间线」，或打开订单详情'
    case 'ORDER':
      return '打开订单详情'
    case 'SLA_RULE':
      return '打开 SLA 规则页'
    case 'VEHICLE':
      return '打开车辆列表'
    case 'KNOWLEDGE_CHUNK':
      return '打开知识库'
    case 'EXCEPTION':
      return '打开异常详情'
    default:
      return ''
  }
}

/** 短暂高亮被定位的区域，让用户确认"确实跳过去了" */
function flash(element: HTMLElement): void {
  const previous = element.style.outline
  element.style.outline = '2px solid #409eff'
  element.style.outlineOffset = '2px'
  window.setTimeout(() => {
    element.style.outline = previous
    element.style.outlineOffset = ''
  }, 1200)
}

function jump(ref: AiEvidenceRef): void {
  const anchor = anchorOf(ref.type)
  if (anchor) {
    const element = document.getElementById(anchor)
    if (element) {
      element.scrollIntoView({ behavior: 'smooth', block: 'center' })
      flash(element)
      return
    }
  }
  const target = routeOf(ref)
  if (target) void router.push(target)
}
</script>

<template>
  <div>
    <el-empty v-if="refs.length === 0" description="本次分析没有引用来源" :image-size="50" />
    <div
      v-for="ref in refs"
      :key="`${ref.type}-${ref.id}`"
      class="evidence-item"
      :class="{ 'u-clickable': canJump(ref) }"
      :title="canJump(ref) ? jumpHint(ref) : '该来源没有可跳转的页面'"
      @click="jump(ref)"
    >
      <el-tag size="small" effect="plain">{{ typeLabel(ref.type) }}</el-tag>
      <span class="u-mono">#{{ ref.id }}</span>
      <span v-if="ref.doc" class="u-text-muted">
        {{ ref.doc }}<template v-if="ref.section"> · {{ ref.section }}</template>
      </span>
      <span class="evidence-note">{{ ref.note ?? '—' }}</span>
      <el-icon v-if="canJump(ref)"><Top /></el-icon>
    </div>
  </div>
</template>

<style scoped>
.evidence-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 8px;
  border-radius: 6px;
}

.evidence-item.u-clickable:hover {
  background: #f5f7fa;
}

.evidence-note {
  margin-left: auto;
  color: #606266;
  font-size: 12px;
  text-align: right;
}
</style>
