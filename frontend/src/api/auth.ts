import { http } from './http'

export type Role = 'user' | 'admin'

export interface User {
  id: string
  username: string
  display_name: string
  role: Role
  must_change_password: boolean
}

/** 管理员提醒（SPEC 6.7）：code 为 BACKUP_FAILED / BACKUP_SPACE_LOW / CALL_CAP_NEAR，界面只显示 message。 */
export interface AdminAlert {
  code?: string
  message: string
}

export interface LoginResult {
  user: User
  csrf_token: string
}

export interface MeResult extends LoginResult {
  alerts: AdminAlert[]
}

export const authApi = {
  login: (username: string, password: string) =>
    http.post<LoginResult>('/auth/login', { username, password }, { silent401: true }),
  logout: () => http.post<void>('/auth/logout'),
  // 探测登录状态：未登录是正常情况，由调用方（路由守卫）决定去向，不触发全局 401 跳转
  me: () => http.get<MeResult>('/auth/me', undefined, { silent401: true }),
  changePassword: (oldPassword: string, newPassword: string) =>
    http.post<void>('/auth/password', { old_password: oldPassword, new_password: newPassword }),
}
