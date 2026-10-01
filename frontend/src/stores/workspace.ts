/**
 * 当前工作区（基线 §12.3：stores/workspace）。
 */
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { workspaceApi } from '@/api'
import { useAuthStore } from './auth'
import type { Workspace, WorkspaceMember } from '@/types'

export const useWorkspaceStore = defineStore('workspace', () => {
  const members = ref<WorkspaceMember[]>([])
  const loading = ref(false)

  const auth = useAuthStore()
  const current = computed<Workspace | null>(() => auth.currentWorkspace)

  async function refreshMembers(): Promise<void> {
    loading.value = true
    try {
      members.value = await workspaceApi.listMembers()
    } catch {
      members.value = []
    } finally {
      loading.value = false
    }
  }

  function memberName(userId?: number | null): string {
    if (!userId) return '—'
    return members.value.find((m) => m.user_id === userId)?.name ?? `用户 #${userId}`
  }

  async function switchWorkspace(id: number): Promise<void> {
    auth.applyWorkspace(id)
    await Promise.all([auth.fetchProfile(), refreshMembers()])
  }

  return { members, loading, current, refreshMembers, memberName, switchWorkspace }
})
