/**
 * 路由与守卫（基线 §12.1 + §12.2-4）。
 * - 未登录访问业务路由 → 跳 /login（带 redirect）
 * - 路由级权限：meta.perm 不满足 → 跳 /dashboard 并提示
 */
import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'
import { ElMessage } from 'element-plus'

import { Perm } from '@/types'
import { useAuthStore } from '@/stores/auth'

const routes: RouteRecordRaw[] = [
  { path: '/', redirect: '/dashboard' },
  {
    path: '/login',
    name: 'login',
    component: () => import('@/views/LoginView.vue'),
    meta: { title: '登录', public: true, layout: 'auth' },
  },
  {
    path: '/register',
    name: 'register',
    component: () => import('@/views/RegisterView.vue'),
    meta: { title: '注册', public: true, layout: 'auth' },
  },
  {
    path: '/dashboard',
    name: 'dashboard',
    component: () => import('@/views/DashboardView.vue'),
    meta: { title: '总览', perm: Perm.DASHBOARD_VIEW },
  },
  {
    path: '/orders',
    name: 'orders',
    component: () => import('@/views/OrderListView.vue'),
    meta: { title: '订单', perm: Perm.ORDER_VIEW },
  },
  {
    path: '/orders/:id',
    name: 'order-detail',
    component: () => import('@/views/OrderDetailView.vue'),
    meta: { title: '订单详情', perm: Perm.ORDER_VIEW },
  },
  {
    path: '/exceptions',
    name: 'exceptions',
    component: () => import('@/views/ExceptionListView.vue'),
    meta: { title: '异常中心', perm: Perm.EXCEPTION_VIEW },
  },
  {
    path: '/exceptions/:id',
    name: 'exception-detail',
    component: () => import('@/views/ExceptionDetailView.vue'),
    meta: { title: '异常详情', perm: Perm.EXCEPTION_VIEW },
  },
  {
    path: '/customers',
    name: 'customers',
    component: () => import('@/views/CustomerListView.vue'),
    meta: { title: '客户', perm: Perm.CUSTOMER_VIEW },
  },
  {
    path: '/carriers',
    name: 'carriers',
    component: () => import('@/views/CarrierListView.vue'),
    meta: { title: '承运商', perm: Perm.CARRIER_VIEW },
  },
  {
    path: '/vehicles',
    name: 'vehicles',
    component: () => import('@/views/VehicleListView.vue'),
    meta: { title: '车辆', perm: Perm.VEHICLE_VIEW },
  },
  {
    path: '/drivers',
    name: 'drivers',
    component: () => import('@/views/DriverListView.vue'),
    meta: { title: '司机', perm: Perm.DRIVER_VIEW },
  },
  {
    path: '/sla-rules',
    name: 'sla-rules',
    component: () => import('@/views/SlaRuleListView.vue'),
    meta: { title: 'SLA 规则', perm: Perm.SLA_VIEW },
  },
  {
    path: '/knowledge',
    name: 'knowledge',
    component: () => import('@/views/KnowledgeView.vue'),
    meta: { title: '知识库', perm: Perm.KNOWLEDGE_VIEW },
  },
  {
    path: '/audit',
    name: 'audit',
    component: () => import('@/views/AuditView.vue'),
    meta: { title: '审计日志', perm: Perm.AUDIT_VIEW },
  },
  {
    path: '/members',
    name: 'members',
    component: () => import('@/views/MembersView.vue'),
    meta: { title: '成员与角色', perm: Perm.MEMBER_VIEW },
  },
  {
    path: '/demo',
    name: 'demo',
    component: () => import('@/views/DemoView.vue'),
    meta: { title: '演示工具' },
  },
  {
    path: '/:pathMatch(.*)*',
    name: 'not-found',
    component: () => import('@/views/NotFoundView.vue'),
    meta: { title: '页面不存在' },
  },
]

export const router = createRouter({
  history: createWebHistory(),
  routes,
  scrollBehavior: () => ({ top: 0 }),
})

router.beforeEach(async (to) => {
  const auth = useAuthStore()
  const isPublic = to.meta.public === true

  if (!auth.isAuthenticated && !isPublic) {
    return { name: 'login', query: { redirect: to.fullPath } }
  }

  if (auth.isAuthenticated && !auth.profileLoaded) {
    await auth.fetchProfile()
  }

  if (isPublic && auth.isAuthenticated) {
    return { name: 'dashboard' }
  }

  const required = to.meta.perm as Perm | undefined
  if (required && !auth.can(required)) {
    ElMessage.warning('当前角色没有访问该页面的权限')
    return { name: 'dashboard' }
  }

  return true
})

router.afterEach((to) => {
  const title = (to.meta.title as string | undefined) ?? ''
  document.title = title ? `${title} · LogiOps` : 'LogiOps'
})

export default router
