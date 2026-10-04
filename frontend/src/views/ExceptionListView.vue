<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'

import ExceptionTable from '@/components/ExceptionTable.vue'
import PanelCard from '@/components/PanelCard.vue'
import { exceptionApi, orderApi } from '@/api'
import { Perm } from '@/types'
import type { ExceptionLevel, ExceptionListItem, ExceptionStatus, OrderBrief, Page } from '@/types'
import { useAuthStore } from '@/stores/auth'
import { useDemoStore } from '@/stores/demo'
import { businessNowText, displayIsoToUtc } from '@/utils/datetime'
import { EXCEPTION_STATUS_OPTIONS, LEVEL_OPTIONS } from '@/utils/format'

const router = useRouter()
const auth = useAuthStore()
const demo = useDemoStore()

const query = reactive({
  q: '',
  status: '' as ExceptionStatus | '',
  level: '' as ExceptionLevel | '',
  sla_breached: '' as '' | 'true' | 'false',
  // 次键用 -created_at：risk_score 同为 4 时，让最新建单的主案例排在首行（实测 -risk_score,created_at 会把最旧的顶上来）
  sort: '-risk_score,-created_at',
  page: 1,
  page_size: 20,
})

const result = ref<Page<ExceptionListItem>>({ items: [], total: 0, page: 1, page_size: 20 })
const loading = ref(false)

const canHandle = computed(() => auth.can(Perm.EXCEPTION_HANDLE))
const canCreate = computed(() => auth.can(Perm.EXCEPTION_CREATE))
const canForceClose = computed(() => auth.can(Perm.EXCEPTION_FORCE_CLOSE))

const sortOptions = [
  { value: '-risk_score,-created_at', label: '风险分（高→低）+ 最新优先' },
  { value: '-risk_score,created_at', label: '风险分（高→低）+ 最早优先' },
  { value: '-sla_delay_minutes', label: 'SLA 延误（多→少）' },
  { value: '-updated_at', label: '更新时间（新→旧）' },
  { value: 'occurred_at', label: '发生时间（早→晚）' },
]

/* ------------------------------------------------------ 手工建单（ADMIN+） */

const createVisible = ref(false)
const creating = ref(false)
const orderOptions = ref<OrderBrief[]>([])
const createForm = reactive({
  order_id: null as number | null,
  level: 'MEDIUM' as ExceptionLevel,
  occurred_at: '',
  note: '',
})

async function load(): Promise<void> {
  loading.value = true
  try {
    result.value = await exceptionApi.listExceptions({
      q: query.q || undefined,
      status: query.status || undefined,
      level: query.level || undefined,
      // 布尔筛选必须传 true/false，空串表示不过滤
      sla_breached: query.sla_breached === '' ? undefined : query.sla_breached === 'true',
      sort: query.sort,
      page: query.page,
      page_size: query.page_size,
    })
  } finally {
    loading.value = false
  }
}

function search(): void {
  query.page = 1
  void load()
}

function resetFilters(): void {
  query.q = ''
  query.status = ''
  query.level = ''
  query.sla_breached = ''
  query.sort = '-risk_score,-created_at'
  query.page = 1
  void load()
}

function onPageChange(page: number): void {
  query.page = page
  void load()
}

function onSizeChange(size: number): void {
  query.page_size = size
  query.page = 1
  void load()
}

async function openCreate(): Promise<void> {
  createVisible.value = true
  // 发生时间默认取**业务时间**（真实时间或演示时钟），不要用电脑时间
  if (!demo.businessNowUtc) await demo.refresh()
  createForm.occurred_at = businessNowText(demo.businessNowUtc)
  if (orderOptions.value.length === 0) {
    try {
      const page = await orderApi.listOrders({ page: 1, page_size: 50 })
      orderOptions.value = page.items
    } catch {
      orderOptions.value = []
    }
  }
}

async function submitCreate(): Promise<void> {
  if (!createForm.order_id) {
    ElMessage.warning('请选择订单')
    return
  }
  if (!createForm.note.trim()) {
    ElMessage.warning('MANUAL 建单必须填备注（note）')
    return
  }
  creating.value = true
  try {
    const occurredAt = displayIsoToUtc(createForm.occurred_at)
    if (!occurredAt) {
      ElMessage.warning('请选择发生时间')
      return
    }
    const created = await exceptionApi.createException({
      order_id: createForm.order_id,
      // 不传 type：异常单的问题会实时变化，类型不作为录入项；
      // 后端按订单现场推"建单原因"，界面显示的「当前问题」按风险因子实时推导
      // 指定等级仅 ADMIN 可用（后端强制）；非 ADMIN 不传，由规则算等级
      level: canForceClose.value ? createForm.level : undefined,
      occurred_at: occurredAt,
      note: createForm.note.trim(),
    })
    ElMessage.success(`已创建异常 ${created.case_no}`)
    createVisible.value = false
    createForm.note = ''
    await load()
  } catch {
    // 409 OPEN_EXCEPTION_EXISTS 等已由拦截器提示
  } finally {
    creating.value = false
  }
}

async function quickAnalyze(row: ExceptionListItem): Promise<void> {
  try {
    const start = await exceptionApi.analyzeException(row.id, { expected_version: row.version })
    ElMessage.success(`已触发 AI 分析（analysis_id=${start.analysis_id}）`)
    await router.push(`/exceptions/${row.id}`)
  } catch {
    // 拦截器已提示（含 409 AI_ANALYSIS_IN_PROGRESS）
  }
}

onMounted(load)
</script>

<template>
  <div class="page">
    <PanelCard title="异常中心" :subtitle="`共 ${result.total} 条 · 默认按风险分倒序`" icon="Warning">
      <template #actions>
        <el-button v-if="canCreate" size="small" type="primary" @click="openCreate">手工建单</el-button>
        <el-button size="small" :loading="loading" @click="load">刷新</el-button>
      </template>

      <el-form inline class="filter-form" @submit.prevent="search">
        <el-form-item label="搜索">
          <el-input
            v-model="query.q"
            placeholder="订单号 / 异常编号 / 客户名"
            clearable
            style="width: 220px"
            @keyup.enter="search"
          />
        </el-form-item>
        <el-form-item label="状态">
          <el-select v-model="query.status" clearable placeholder="全部" style="width: 140px" @change="search">
            <el-option
              v-for="option in EXCEPTION_STATUS_OPTIONS"
              :key="option.value"
              :label="option.label"
              :value="option.value"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="等级">
          <el-select v-model="query.level" clearable placeholder="全部" style="width: 130px" @change="search">
            <el-option v-for="option in LEVEL_OPTIONS" :key="option.value" :label="option.label" :value="option.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="SLA">
          <el-select v-model="query.sla_breached" clearable placeholder="全部" style="width: 120px" @change="search">
            <el-option label="已违约" value="true" />
            <el-option label="未违约" value="false" />
          </el-select>
        </el-form-item>
        <el-form-item label="排序">
          <el-select v-model="query.sort" style="width: 190px" @change="search">
            <el-option v-for="option in sortOptions" :key="option.value" :label="option.label" :value="option.value" />
          </el-select>
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="search">查询</el-button>
          <el-button @click="resetFilters">重置</el-button>
        </el-form-item>
      </el-form>

      <ExceptionTable
        :items="result.items"
        :loading="loading"
        show-order
        @row-click="(row) => router.push(`/exceptions/${row.id}`)"
      />

      <div class="table-foot">
        <el-pagination
          :current-page="result.page"
          :page-size="result.page_size"
          :total="result.total"
          :page-sizes="[10, 20, 50, 100]"
          layout="total, sizes, prev, pager, next, jumper"
          background
          @current-change="onPageChange"
          @size-change="onSizeChange"
        />
      </div>

      <div class="u-text-muted u-mt-12">
        契约：GET /exceptions?status&amp;level&amp;type&amp;customer_id&amp;sla_breached&amp;sort=-risk_score&amp;page ·
        分页响应 {items,total,page,page_size}
      </div>
      <div v-if="canHandle" class="u-text-muted u-mt-8">
        按钮级权限：<code>v-if="can('exception.handle')"</code>；无权限的按钮直接不渲染（而非点了报错）。
      </div>
    </PanelCard>

    <el-dialog v-model="createVisible" title="手工建单（MANUAL，ADMIN+）" width="520px">
      <el-alert
        type="info"
        :closable="false"
        show-icon
        class="u-mb-8"
        title="不需要选异常类型"
        description="一张异常单的问题是实时变化的（车辆修好、只剩延误/违约都会被自动重算），所以类型不作为录入项：系统按风险因子实时推导「当前问题」。"
      />
      <el-form label-width="90px">
        <el-form-item label="订单">
          <el-select v-model="createForm.order_id" filterable placeholder="选择订单" style="width: 100%">
            <el-option
              v-for="order in orderOptions"
              :key="order.id"
              :label="`${order.order_no}（${order.origin_city ?? '—'}→${order.dest_city ?? '—'}）`"
              :value="order.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item v-if="canForceClose" label="等级">
          <el-select v-model="createForm.level" style="width: 100%">
            <el-option v-for="option in LEVEL_OPTIONS" :key="option.value" :label="option.label" :value="option.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="发生时间">
          <el-date-picker
            v-model="createForm.occurred_at"
            type="datetime"
            value-format="YYYY-MM-DD HH:mm"
            style="width: 100%"
          />
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="createForm.note" type="textarea" :rows="3" placeholder="必填：为什么手工建单" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" :loading="creating" @click="submitCreate">创建</el-button>
      </template>
    </el-dialog>

    <el-alert
      v-if="!canForceClose && canHandle"
      type="info"
      :closable="false"
      class="u-mt-12"
      title="普通 OPERATOR 不展示「强制关闭」按钮（需要 exception.force_close，ADMIN+）"
    />
    <div class="u-text-muted u-mt-8">
      快捷分析入口：行内「分析」按钮调用 POST /exceptions/{id}/analyze；
      <b>只有「处理中」的异常可以分析</b>（4 状态模型：确认异常即进入处理中），批量审批见详情页。
      <el-button
        v-if="canHandle"
        size="small"
        text
        type="primary"
        @click="quickAnalyze(result.items[0])"
        :disabled="!result.items.length || result.items[0]?.status !== 'PROCESSING'"
      >
        对首行触发分析
      </el-button>
      <span v-if="canHandle && result.items.length && result.items[0]?.status !== 'PROCESSING'" class="u-text-muted">
        （首行当前状态 {{ result.items[0]?.status }}，不可分析）
      </span>
    </div>
  </div>
</template>

<style scoped>
.filter-form {
  margin-bottom: 8px;
}

.table-foot {
  display: flex;
  justify-content: flex-end;
  margin-top: 12px;
}
</style>
