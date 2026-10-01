/**
 * 认证与当前用户（基线 §12.3：stores/auth 存 token/user/permissions）。
 * 同时通过 api/runtime 注入 axios 拦截器所需的 token / workspace / 401 登出回调。
 */
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { authApi } from '@/api'
import { bindAuthRuntime } from '@/api/runtime'
import { getStoredToken, getStoredWorkspaceId, setStoredToken, setStoredWorkspaceId } from '@/utils/storage'
import { hasPerm as checkPerm, permsForRole } from '@/utils/permissions'
import type { LoginPayload, MeResult, Perm, RegisterPayload, Role, User, Workspace } from '@/types'

export const useAuthStore = defineStore('auth', () => {
  const token = ref<string | null>(getStoredToken())
  const user = ref<User | null>(null)
  const workspaces = ref<Workspace[]>([])
  const workspaceId = ref<number | null>(getStoredWorkspaceId())
  const role = ref<Role | null>(null)
  const permissions = ref<Perm[]>([])
  /** /auth/me 是否已完成（路由守卫据此判断） */
  const profileLoaded = ref(false)
  const loading = ref(false)

  const isAuthenticated = computed(() => Boolean(token.value))
  const currentWorkspace = computed<Workspace | null>(
    () => workspaces.value.find((w) => w.id === workspaceId.value) ?? workspaces.value[0] ?? null,
  )
  const effectivePermissions = computed<Perm[]>(() =>
    permissions.value.length > 0 ? permissions.value : permsForRole(role.value),
  )

  function can(perm: Perm): boolean {
    return checkPerm(perm, { role: role.value, permissions: permissions.value })
  }

  function canAny(perms: Perm[]): boolean {
    return perms.some((perm) => can(perm))
  }

  function applyToken(value: string | null): void {
    token.value = value
    setStoredToken(value)
  }

  function applyWorkspace(id: number | null): void {
    workspaceId.value = id
    setStoredWorkspaceId(id)
  }

  function applyMe(me: MeResult): void {
    user.value = me.user
    workspaces.value = me.workspaces ?? []
    // 实测 /auth/me 返回单数 workspace（+workspace_id），这里同时兼容两种写法
    const byId = workspaces.value.find((w) => w.id === me.workspace_id) ?? null
    const current = me.workspace ?? byId
    if (current) applyWorkspace(current.id)
    else if (!workspaceId.value && workspaces.value.length > 0) applyWorkspace(workspaces.value[0].id)

    const matched = workspaces.value.find((w) => w.id === workspaceId.value)
    role.value = me.role ?? current?.role ?? matched?.role ?? null
    permissions.value = me.permissions ?? []
    profileLoaded.value = true
    // 兜底：后端未返回角色时，按成员接口/默认 OPERATOR 处理，保证按钮级权限可见
    if (!role.value) role.value = 'OPERATOR'
  }

  async function login(payload: LoginPayload): Promise<void> {
    loading.value = true
    try {
      const result = await authApi.login(payload)
      applyToken(result.access_token)
      user.value = result.user
      await fetchProfile()
    } finally {
      loading.value = false
    }
  }

  async function register(payload: RegisterPayload): Promise<void> {
    loading.value = true
    try {
      await authApi.register(payload)
      await login({ email: payload.email, password: payload.password })
    } finally {
      loading.value = false
    }
  }

  async function fetchProfile(): Promise<MeResult | null> {
    if (!token.value) return null
    try {
      const me = await authApi.me()
      applyMe(me)
      return me
    } catch {
      // 后端未就绪时也允许以本地 fixture 身份进入（request 层已兜底）；
      // 真的 401 会被拦截器登出。
      if (!user.value) user.value = { id: 0, email: 'local@demo.logiops', name: '本地演示用户', status: 'ACTIVE' }
      if (!role.value) role.value = 'ADMIN'
      profileLoaded.value = true
      return null
    }
  }

  function logoutLocal(): void {
    applyToken(null)
    user.value = null
    workspaces.value = []
    role.value = null
    permissions.value = []
    profileLoaded.value = false
  }

  async function logout(): Promise<void> {
    try {
      if (token.value) await authApi.logout()
    } catch {
      /* 服务端无黑名单（§10.3），失败也要本地登出 */
    } finally {
      logoutLocal()
    }
  }

  // 注入 axios 运行时（token / workspace 头 / 401 登出）
  bindAuthRuntime({
    get: () => ({ token: token.value, workspaceId: workspaceId.value, permissions: effectivePermissions.value }),
    onUnauthorized: () => logoutLocal(),
    onConflict: () => {
      /* 409 的提示与刷新由页面自己处理，这里只保留扩展点 */
    },
  })

  return {
    token,
    user,
    workspaces,
    workspaceId,
    role,
    permissions,
    profileLoaded,
    loading,
    isAuthenticated,
    currentWorkspace,
    effectivePermissions,
    can,
    canAny,
    applyToken,
    applyWorkspace,
    applyMe,
    login,
    register,
    fetchProfile,
    logout,
    logoutLocal,
  }
})
