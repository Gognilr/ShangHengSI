/** 管理接口（SPEC 19.7）：设置页各标签页的函数按页面分段追加，互不改动。 */
import { http } from './http'
import type { Role } from './auth'

// ---- 账号（设置 - 账号） ----
export type UserStatus = 'active' | 'disabled'

export interface UserRow {
  id: string
  username: string
  display_name: string
  role: Role
  status: UserStatus
  must_change_password: boolean
  last_login_at: string | null
  created_at: string
}

export interface UserCreateBody {
  username: string
  display_name: string
  password: string
  role: Role
}

export interface UserPatchBody {
  display_name?: string
  role?: Role
  status?: UserStatus
}

export const adminUsersApi = {
  list: () => http.get<{ items: UserRow[] }>('/admin/users'),
  create: (body: UserCreateBody) => http.post<UserRow>('/admin/users', body),
  update: (id: string, body: UserPatchBody) => http.patch<UserRow>(`/admin/users/${id}`, body),
  resetPassword: (id: string, newPassword: string) =>
    http.post<void>(`/admin/users/${id}/reset-password`, { new_password: newPassword }),
}
