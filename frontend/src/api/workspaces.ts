import { del, get, patch, post } from './request'
import type {
  MemberCreatePayload,
  MemberUpdatePayload,
  Workspace,
  WorkspaceMember,
} from '@/types'

/** GET /workspaces → 我加入的工作区 */
export function listWorkspaces(): Promise<Workspace[]> {
  return get<Workspace[]>('/workspaces')
}

/** POST /workspaces → 创建工作区（创建者自动 OWNER） */
export function createWorkspace(payload: { name: string; code?: string }): Promise<Workspace> {
  return post<Workspace>('/workspaces', payload)
}

/** GET /workspaces/current */
export function currentWorkspace(): Promise<Workspace> {
  return get<Workspace>('/workspaces/current')
}

/** GET /workspaces/current/members */
export function listMembers(): Promise<WorkspaceMember[]> {
  return get<WorkspaceMember[]>('/workspaces/current/members')
}

/** POST /workspaces/current/members（按 email 直接添加） */
export function addMember(payload: MemberCreatePayload): Promise<WorkspaceMember> {
  return post<WorkspaceMember>('/workspaces/current/members', payload)
}

/** PATCH /workspaces/current/members/{id} 改角色 */
export function updateMember(id: number, payload: MemberUpdatePayload): Promise<WorkspaceMember> {
  return patch<WorkspaceMember>(`/workspaces/current/members/${id}`, payload)
}

/** DELETE /workspaces/current/members/{id} */
export function removeMember(id: number): Promise<{ ok: boolean }> {
  return del<{ ok: boolean }>(`/workspaces/current/members/${id}`)
}
