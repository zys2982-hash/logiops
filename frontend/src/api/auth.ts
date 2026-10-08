import { get, post } from './request'
import type {
  ChangePasswordPayload,
  ChangePasswordResult,
  LoginPayload,
  LoginResult,
  MeResult,
  RegisterPayload,
  User,
} from '@/types'

/** POST /auth/register → 201 user */
export function register(payload: RegisterPayload): Promise<User> {
  return post<User>('/auth/register', payload)
}

/** POST /auth/login → {access_token, token_type, expires_in, user} */
export function login(payload: LoginPayload): Promise<LoginResult> {
  return post<LoginResult>('/auth/login', payload)
}

/** GET /auth/me → 当前用户 + 工作区成员列表 */
export function me(): Promise<MeResult> {
  return get<MeResult>('/auth/me')
}

/** POST /auth/logout（服务端无黑名单，客户端丢弃 token） */
export function logout(): Promise<{ ok: boolean }> {
  return post<{ ok: boolean }>('/auth/logout')
}

/** POST /auth/password → 改自己的密码（需原密码；原密码错误是 422，不会触发全局登出） */
export function changePassword(payload: ChangePasswordPayload): Promise<ChangePasswordResult> {
  return post<ChangePasswordResult>('/auth/password', payload)
}
