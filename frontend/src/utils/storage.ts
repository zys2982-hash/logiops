/**
 * 本地存储键与读写（token / workspace_id）。
 */
const TOKEN_KEY = 'logiops.token'
const WORKSPACE_KEY = 'logiops.workspace_id'

export function getStoredToken(): string | null {
  try {
    return window.localStorage.getItem(TOKEN_KEY)
  } catch {
    return null
  }
}

export function setStoredToken(token: string | null): void {
  try {
    if (token) window.localStorage.setItem(TOKEN_KEY, token)
    else window.localStorage.removeItem(TOKEN_KEY)
  } catch {
    /* 忽略隐私模式下的写入失败 */
  }
}

export function getStoredWorkspaceId(): number | null {
  try {
    const raw = window.localStorage.getItem(WORKSPACE_KEY)
    return raw ? Number(raw) : null
  } catch {
    return null
  }
}

export function setStoredWorkspaceId(id: number | null): void {
  try {
    if (id) window.localStorage.setItem(WORKSPACE_KEY, String(id))
    else window.localStorage.removeItem(WORKSPACE_KEY)
  } catch {
    /* 忽略 */
  }
}
