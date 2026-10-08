<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessageBox } from 'element-plus'
import { ArrowDown, Lock, SwitchButton } from '@element-plus/icons-vue'

import ChangePasswordDialog from '@/components/ChangePasswordDialog.vue'
import DemoBanner from './DemoBanner.vue'
import { Perm } from '@/types'
import { roleLabel } from '@/utils/permissions'
import { useAuthStore } from '@/stores/auth'
import { useDemoStore } from '@/stores/demo'
import { useUiStore } from '@/stores/ui'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const demo = useDemoStore()
const ui = useUiStore()

interface NavItem {
  path: string
  label: string
  icon: string
  perm?: Perm
}

interface NavGroup {
  title: string
  items: NavItem[]
}

const groups: NavGroup[] = [
  {
    title: '运营',
    items: [
      { path: '/dashboard', label: '总览', icon: 'DataBoard', perm: Perm.DASHBOARD_VIEW },
      { path: '/exceptions', label: '异常中心', icon: 'Warning', perm: Perm.EXCEPTION_VIEW },
      { path: '/orders', label: '运输订单', icon: 'Tickets', perm: Perm.ORDER_VIEW },
      { path: '/knowledge', label: '知识库', icon: 'Notebook', perm: Perm.KNOWLEDGE_VIEW },
    ],
  },
  {
    title: '主数据',
    items: [
      { path: '/customers', label: '客户', icon: 'OfficeBuilding', perm: Perm.CUSTOMER_VIEW },
      // 承运商（ADR-A17）：只是归属字典，不单独占菜单项；入口在「车辆」页的操作区
      // 以及订单/异常详情里的只读字段；路由 /carriers 仍然可用（管理员可直达）
      { path: '/vehicles', label: '车辆', icon: 'Van', perm: Perm.VEHICLE_VIEW },
      { path: '/drivers', label: '司机', icon: 'User', perm: Perm.DRIVER_VIEW },
      { path: '/sla-rules', label: 'SLA 规则', icon: 'Timer', perm: Perm.SLA_VIEW },
    ],
  },
  {
    title: '系统',
    items: [
      { path: '/members', label: '成员与角色', icon: 'UserFilled', perm: Perm.MEMBER_VIEW },
      { path: '/audit', label: '审计日志', icon: 'Document', perm: Perm.AUDIT_VIEW },
      { path: '/demo', label: '演示工具', icon: 'MagicStick' },
    ],
  },
]

const visibleGroups = computed(() =>
  groups
    .map((group) => ({
      ...group,
      items: group.items.filter((item) => !item.perm || auth.can(item.perm)),
    }))
    .filter((group) => group.items.length > 0),
)

const activeMenu = computed(() => {
  if (route.path.startsWith('/orders')) return '/orders'
  if (route.path.startsWith('/exceptions')) return '/exceptions'
  return route.path
})

async function handleLogout(): Promise<void> {
  try {
    await ElMessageBox.confirm('确定退出登录？', '提示', { type: 'warning' })
  } catch {
    return
  }
  await auth.logout()
  router.push({ name: 'login' })
}

/** 顶栏用户菜单：修改密码 / 退出登录 */
const passwordVisible = ref(false)

const userInitial = computed(() => (auth.user?.name ?? '?').slice(0, 1))

function handleUserCommand(command: string): void {
  if (command === 'password') {
    passwordVisible.value = true
    return
  }
  if (command === 'logout') void handleLogout()
}
</script>

<template>
  <el-container style="height: 100%">
    <el-aside :width="ui.sidebarCollapsed ? '64px' : '216px'" style="background: var(--logiops-sidebar)">
      <div class="brand">
        <span class="brand-logo">LO</span>
        <span v-if="!ui.sidebarCollapsed" class="brand-text">LogiOps 异常协同</span>
      </div>
      <el-menu
        :default-active="activeMenu"
        :collapse="ui.sidebarCollapsed"
        background-color="#1f2d3d"
        text-color="#c0c4cc"
        active-text-color="#ffffff"
        router
        style="border-right: none"
      >
        <template v-for="group in visibleGroups" :key="group.title">
          <div v-if="!ui.sidebarCollapsed" class="nav-group-title">{{ group.title }}</div>
          <el-menu-item v-for="item in group.items" :key="item.path" :index="item.path">
            <el-icon><component :is="item.icon" /></el-icon>
            <template #title>{{ item.label }}</template>
          </el-menu-item>
        </template>
      </el-menu>
    </el-aside>

    <el-container>
      <el-header height="48px" class="topbar">
        <div class="topbar-left">
          <el-button text :icon="ui.sidebarCollapsed ? 'Expand' : 'Fold'" @click="ui.toggleSidebar()" />
          <span class="topbar-title">{{ route.meta.title ?? '' }}</span>
          <el-tag v-if="demo.isReplay" size="small" type="warning" effect="plain" class="u-nowrap">
            AI 来源：回放样本
          </el-tag>
          <el-tag v-else size="small" type="danger" effect="plain" class="u-nowrap">AI 来源：真实大模型</el-tag>
        </div>
        <div class="topbar-right">
          <span class="u-text-muted">业务时间 {{ demo.businessTimeText }}</span>
          <el-divider direction="vertical" />
          <el-dropdown trigger="click" @command="handleUserCommand">
            <span class="user-chip">
              <span class="user-avatar">{{ userInitial }}</span>
              <span class="user-name">{{ auth.user?.name ?? '未登录' }}</span>
              <el-tag size="small" effect="plain">{{ roleLabel(auth.role) }}</el-tag>
              <el-icon class="user-arrow"><ArrowDown /></el-icon>
            </span>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="password" :icon="Lock">修改密码</el-dropdown-item>
                <el-dropdown-item command="logout" :icon="SwitchButton" divided>退出登录</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </el-header>

      <DemoBanner />

      <el-main style="padding: 0; overflow: auto">
        <router-view v-slot="{ Component }">
          <component :is="Component" />
        </router-view>
      </el-main>
    </el-container>

    <ChangePasswordDialog v-model="passwordVisible" />
  </el-container>
</template>

<style scoped>
.brand {
  display: flex;
  align-items: center;
  gap: 8px;
  height: 48px;
  padding: 0 12px;
  color: #fff;
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
}

.brand-logo {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: 6px;
  background: var(--logiops-sidebar-active);
  font-weight: 700;
  font-size: 12px;
}

.brand-text {
  font-size: 14px;
  font-weight: 600;
  white-space: nowrap;
}

.nav-group-title {
  padding: 12px 16px 4px;
  font-size: 11px;
  color: #6b7885;
  letter-spacing: 1px;
}

.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: #fff;
  border-bottom: 1px solid #e4e7ed;
}

.topbar-left,
.topbar-right {
  display: flex;
  align-items: center;
  gap: 8px;
}

.topbar-title {
  font-weight: 600;
  margin-right: 8px;
}

.user-chip {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 4px 8px;
  border-radius: 6px;
  cursor: pointer;
  outline: none;
  transition: background 0.2s;
}

.user-chip:hover {
  background: #f2f6fd;
}

.user-avatar {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border-radius: 50%;
  background: linear-gradient(135deg, #2f6fed 0%, #1f2d3d 100%);
  color: #fff;
  font-size: 12px;
  font-weight: 600;
}

.user-name {
  font-weight: 500;
}

.user-arrow {
  color: #909399;
  font-size: 12px;
}
</style>
