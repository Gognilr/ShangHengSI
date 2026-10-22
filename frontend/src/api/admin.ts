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

// ---- 模型（设置 - 模型） ----
export interface ModelSettings {
  base_url: string
  name: string
  temperature: number
  max_tokens: number
  timeout_s: number
  json_mode: boolean
  extra_params: Record<string, unknown>
  chunk_chars: number
  price_in_per_m: string
  price_out_per_m: string
  monthly_call_cap: number
  api_key_set: boolean
  api_key_last4: string | null
}

export type ModelSettingsIn = Omit<ModelSettings, 'api_key_set' | 'api_key_last4'> & { api_key?: string }

export interface ModelTestResult {
  ok: boolean
  model?: string | null
  duration_ms?: number | null
  reply?: string | null
  message?: string | null
}

export interface UsageOut {
  calls: number
  cap: number
  tokens_in: number
  tokens_out: number
  cost: string
  month: string
}

export const adminModelApi = {
  get: () => http.get<ModelSettings>('/admin/settings/model'),
  save: (body: ModelSettingsIn) => http.put<ModelSettings>('/admin/settings/model', body),
  test: () => http.post<ModelTestResult>('/admin/settings/model/test'),
  usage: () => http.get<UsageOut>('/admin/usage'),
}

// ---- 审核要求（设置 - 审核要求） ----
export interface CompanyReqRow {
  id: string
  code: string
  text: string
  doc_types: string[]
  stances: string[]
  enabled: boolean
  sort: number
  created_at: string
  updated_at: string
}

export type CompanyReqBody = Pick<CompanyReqRow, 'text' | 'doc_types' | 'stances' | 'enabled' | 'sort'>

export interface RequirementVersionRow {
  version: number
  created_by_name: string | null
  created_at: string
  snapshot: {
    requirements: { code: string; text: string; doc_types: string[]; stances: string[]; sort: number }[]
    company_profile: string
  }
}

export const adminRequirementsApi = {
  list: () => http.get<{ items: CompanyReqRow[]; version: number | null }>('/admin/company-requirements'),
  create: (body: CompanyReqBody) => http.post<CompanyReqRow>('/admin/company-requirements', body),
  update: (id: string, body: CompanyReqBody) => http.put<CompanyReqRow>(`/admin/company-requirements/${id}`, body),
  remove: (id: string) => http.delete<void>(`/admin/company-requirements/${id}`),
  getProfile: () => http.get<{ text: string; version: number | null }>('/admin/company-profile'),
  saveProfile: (text: string) => http.put<{ text: string; version: number | null }>('/admin/company-profile', { text }),
  versions: () => http.get<{ items: RequirementVersionRow[] }>('/admin/requirement-versions'),
}

// ---- 脱敏（设置 - 脱敏） ----
export interface TermRow {
  id: string
  kind: 'learned' | 'custom'
  term: string
  category: string
  is_our_party: boolean
  enabled: boolean
  hit_count: number
  last_hit_at: string | null
  created_by_name: string | null
  created_at: string
}

export interface TermPage {
  items: TermRow[]
  total: number
  page: number
  page_size: number
}

export const adminRedactionApi = {
  getSetting: () => http.get<{ mask_quantity: boolean }>('/admin/settings/redaction'),
  saveSetting: (maskQuantity: boolean) =>
    http.put<{ mask_quantity: boolean }>('/admin/settings/redaction', { mask_quantity: maskQuantity }),
  terms: (kind: 'learned' | 'custom', q: string, page: number) =>
    http.get<TermPage>('/admin/terms', { kind, q: q || undefined, page }),
  addTerm: (term: string) => http.post<TermRow>('/admin/terms', { term }),
  setTermEnabled: (id: string, enabled: boolean) => http.patch<TermRow>(`/admin/terms/${id}`, { enabled }),
  removeTerm: (id: string) => http.delete<void>(`/admin/terms/${id}`),
}

// ---- 数据（设置 - 数据） ----
export interface BackupSettings {
  time: string
  keep: number
  dir: string
}

export interface BackupRow {
  id: number
  trigger: 'schedule' | 'manual'
  status: 'running' | 'success' | 'failed'
  file_name: string | null
  size_bytes: number | null
  sha256: string | null
  error_message: string | null
  started_at: string
  finished_at: string | null
}

export interface BackupRecords {
  dir: string
  free_bytes: number | null
  latest: BackupRow | null
  items: BackupRow[]
}

export interface OutboundRow {
  id: number
  created_at: string
  user_id: string | null
  user_name: string | null
  task_title: string | null
  purpose: 'review' | 'chat' | 'test'
  model_name: string
  chars: number
  status: 'sent' | 'blocked' | 'error'
  tokens_in: number
  tokens_out: number
}

export interface OutboundDetail extends OutboundRow {
  request_text: string | null
  response_text: string | null
  block_reason: string | null
  error_message: string | null
}

export interface OutboundQuery {
  user_id?: string
  from?: string
  to?: string
  status?: string
  page?: number
}

export interface SystemStatus {
  version: string
  queued_jobs: number
  data_free_bytes: number | null
}

export const adminDataApi = {
  getBackupSettings: () => http.get<BackupSettings>('/admin/settings/backup'),
  saveBackupSettings: (time: string, keep: number) => http.put<BackupSettings>('/admin/settings/backup', { time, keep }),
  runBackup: () => http.post<{ queued: boolean }>('/admin/backup/run'),
  backupRecords: () => http.get<BackupRecords>('/admin/backup/records'),
  getRetention: () => http.get<{ original_days: number }>('/admin/settings/retention'),
  saveRetention: (days: number) => http.put<{ original_days: number }>('/admin/settings/retention', { original_days: days }),
  outboundLogs: (q: OutboundQuery) =>
    http.get<{ items: OutboundRow[]; total: number; page: number; page_size: number }>('/admin/outbound-logs', { ...q }),
  outboundLog: (id: number) => http.get<OutboundDetail>(`/admin/outbound-logs/${id}`),
  systemStatus: () => http.get<SystemStatus>('/admin/system/status'),
}
