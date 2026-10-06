<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import PanelCard from '@/components/PanelCard.vue'
import { workspaceApi } from '@/api'
import { Perm, Role } from '@/types'
import type { Role as RoleType, WorkspaceMember } from '@/types'
import { useAuthStore } from '@/stores/auth'
import { formatDateTime } from '@/utils/datetime'
import { memberStatusLabel } from '@/utils/format'
import { roleLabel } from '@/utils/permissions'

const auth = useAuthStore()
const canManage = computed(() => auth.can(Perm.MEMBER_MANAGE))

const members = ref<WorkspaceMember[]>([])
const loading = ref(false)
const saving = ref(false)
const dialogVisible = ref(false)
const form = reactive({ email: '', name: '', role: 'OPERATOR' as RoleType })

const roleOptions = [
  { value: Role.OWNER, label: '所有者（OWNER）' },
  { value: Role.ADMIN, label: '管理员（ADMIN）' },
  { value: Role.OPERATOR, label: '运营（OPERATOR）' },
  { value: Role.VIEWER, label: '只读（VIEWER）' },
]

const matrix = [
  { label: '查看（Dashboard/订单/异常/审计/知识库）', viewer: true, operator: true, admin: true, owner: true },
  { label: '录入轨迹 / 录入承运商消息', viewer: false, operator: true, admin: true, owner: true },
  { label: '创建异常 / 确认 / 触发 AI 分析', viewer: false, operator: true, admin: true, owner: true },
  { label: '审批（批准/驳回/修改）AI 建议', viewer: false, operator: true, admin: true, owner: true },
  { label: '通知批准 / 模拟发送', viewer: false, operator: true, admin: true, owner: true },
  { label: '主数据与 SLA 规则维护', viewer: false, operator: false, admin: true, owner: true },
  { label: '异常强制关闭 / 成员管理', viewer: false, operator: false, admin: true, owner: true },
  { label: '删除 Workspace / 转移 OWNER', viewer: false, operator: false, admin: false, owner: true },
]

async function load(): Promise<void> {
  loading.value = true
  try {
    members.value = await workspaceApi.listMembers()
  } catch {
    members.value = []
  } finally {
    loading.value = false
  }
}

async function submit(): Promise<void> {
  if (!form.email.trim()) {
    ElMessage.warning('邮箱为必填（按 email 直接添加，不做邮件邀请）')
    return
  }
  saving.value = true
  try {
    await workspaceApi.addMember({ email: form.email.trim(), role: form.role, name: form.name || undefined })
    ElMessage.success('成员已添加')
    dialogVisible.value = false
    form.email = ''
    form.name = ''
    await load()
  } finally {
    saving.value = false
  }
}

async function changeRole(row: WorkspaceMember, role: RoleType): Promise<void> {
  try {
    await workspaceApi.updateMember(row.id, { role })
    ElMessage.success(`已将 ${row.name ?? row.email} 的角色改为 ${roleLabel(role)}`)
    await load()
  } catch {
    await load()
  }
}

async function remove(row: WorkspaceMember): Promise<void> {
  try {
    await ElMessageBox.confirm(`移除成员 ${row.name ?? row.email}？不能移除自己或 OWNER。`, '移除成员', {
      type: 'warning',
    })
  } catch {
    return
  }
  try {
    await workspaceApi.removeMember(row.id)
    ElMessage.success('成员已移除')
    await load()
  } catch {
    await load()
  }
}

onMounted(load)
</script>

<template>
  <div class="page">
    <PanelCard
      title="成员与角色"
      :subtitle="`当前工作区：${auth.currentWorkspace?.name ?? '—'}（${members.length} 名成员）`"
      icon="UserFilled"
    >
      <template #actions>
        <el-button v-if="canManage" size="small" type="primary" @click="dialogVisible = true">添加成员</el-button>
        <el-tag v-else size="small" effect="plain">只读（member.manage 才可管理）</el-tag>
      </template>

      <el-table :data="members" v-loading="loading" size="small" border stripe>
        <el-table-column label="成员" min-width="180">
          <template #default="{ row }">
            <b>{{ row.name ?? '—' }}</b>
            <div class="u-text-muted">{{ row.email ?? '—' }}</div>
          </template>
        </el-table-column>
        <el-table-column label="角色" width="200">
          <template #default="{ row }">
            <el-select
              v-if="canManage && row.role !== 'OWNER'"
              :model-value="row.role"
              size="small"
              @change="(value: RoleType) => changeRole(row, value)"
            >
              <el-option v-for="option in roleOptions" :key="option.value" :label="option.label" :value="option.value" />
            </el-select>
            <el-tag v-else size="small" effect="plain">{{ roleLabel(row.role) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="110" align="center">
          <template #default="{ row }">
            <el-tag size="small" :type="row.status === 'ACTIVE' ? 'success' : 'info'">{{ memberStatusLabel(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="加入时间" width="160">
          <template #default="{ row }">{{ formatDateTime(row.joined_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="90" fixed="right">
          <template #default="{ row }">
            <el-button
              v-if="canManage && row.role !== 'OWNER'"
              size="small"
              text
              type="danger"
              @click="remove(row)"
            >
              移除
            </el-button>
            <span v-else class="u-text-muted">—</span>
          </template>
        </el-table-column>
      </el-table>

      <div class="u-text-muted u-mt-8">
        契约：GET /workspaces/current/members · POST（按 email 添加）· PATCH（改角色）· DELETE（移除）·
        不能移除自己/Owner
      </div>
    </PanelCard>

    <PanelCard title="角色 × 操作矩阵（§9.2）" subtitle="前端按钮级权限与后端 ROLE_PERMS 一致" icon="Grid" class="u-mt-12">
      <el-table :data="matrix" size="small" border>
        <el-table-column prop="label" label="操作" min-width="240" />
        <el-table-column label="VIEWER" width="90" align="center">
          <template #default="{ row }">{{ row.viewer ? '✅' : '❌' }}</template>
        </el-table-column>
        <el-table-column label="OPERATOR" width="100" align="center">
          <template #default="{ row }">{{ row.operator ? '✅' : '❌' }}</template>
        </el-table-column>
        <el-table-column label="ADMIN" width="90" align="center">
          <template #default="{ row }">{{ row.admin ? '✅' : '❌' }}</template>
        </el-table-column>
        <el-table-column label="OWNER" width="90" align="center">
          <template #default="{ row }">{{ row.owner ? '✅' : '❌' }}</template>
        </el-table-column>
      </el-table>
      <div class="u-text-muted u-mt-8">
        前端权限码与后端 <code>core/permissions.py</code> 的 <code>Perm</code> 枚举逐字对齐（例如
        <code>exception.handle</code>、<code>approval.decide</code>）。
      </div>
    </PanelCard>

    <el-dialog v-model="dialogVisible" title="添加成员" width="460px">
      <el-form label-width="80px">
        <el-form-item label="邮箱"><el-input v-model="form.email" placeholder="someone@example.com" /></el-form-item>
        <el-form-item label="姓名"><el-input v-model="form.name" placeholder="可选" /></el-form-item>
        <el-form-item label="角色">
          <el-select v-model="form.role" style="width: 100%">
            <el-option v-for="option in roleOptions" :key="option.value" :label="option.label" :value="option.value" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="submit">添加</el-button>
      </template>
    </el-dialog>
  </div>
</template>
