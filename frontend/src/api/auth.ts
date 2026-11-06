import { http } from './http'

import type { Perm } from '@/utils/permissions'

export interface User {
  id: string
  username: string
  display_name: string
  /** 所兼角色的名称（V31-U5F-07） */
  roles: string[]
  permissions: Perm[]
  must_change_password: boolean
}

/** 登录设备：本人在线会话 */
export interface Device {
  id: string
  ip: string | null
  browser: string
  created_at: string
  last_seen_at: string
  current: boolean
}

/** 新 IP 登录提醒；没有时 ip 为 null */
export interface LoginNotice { ip: string | null; at: string | null }

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
  devices: () => http.get<Device[]>('/auth/sessions'),
  endDevice: (id: string) => http.delete<void>(`/auth/sessions/${id}`),
  endOtherDevices: () => http.post<void>('/auth/sessions/logout-others'),
  // 定时查询，不触发全局 401 跳转（过期由下一次正常请求处理）
  notice: () => http.get<LoginNotice>('/auth/notice', undefined, { silent401: true }),
  dismissNotice: () => http.post<void>('/auth/notice/dismiss'),
}
