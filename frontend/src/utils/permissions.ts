/**
 * 按钮级权限（基线 §9.2 / §12.2-4）：无权限的按钮直接不渲染，而不是点了报错。
 */
import { ROLE_PERMS, type Perm, type Role } from '@/types'

export function permsForRole(role?: Role | null): Perm[] {
  if (!role) return []
  return ROLE_PERMS[role] ?? []
}

/** 显式 permissions 优先（后端下发），否则按角色推导 */
export function hasPerm(
  perm: Perm,
  options: { role?: Role | null; permissions?: Perm[] | null },
): boolean {
  const explicit = options.permissions
  if (explicit && explicit.length > 0) return explicit.includes(perm)
  return permsForRole(options.role).includes(perm)
}

/** 任意一个权限命中 */
export function hasAnyPerm(
  perms: Perm[],
  options: { role?: Role | null; permissions?: Perm[] | null },
): boolean {
  return perms.some((perm) => hasPerm(perm, options))
}

export const ROLE_LABEL: Record<Role, string> = {
  OWNER: '所有者',
  ADMIN: '管理员',
  OPERATOR: '运营',
  VIEWER: '只读',
}

export function roleLabel(role?: Role | null): string {
  return role ? (ROLE_LABEL[role] ?? role) : '未分配'
}
