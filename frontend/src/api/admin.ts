/** 管理接口（SPEC 19.7）：设置页各标签页的函数按页面分段追加，互不改动。 */
import { http } from './http'
import type { Role } from './auth'

export interface LawsyncSettings {
  enabled: boolean; interval: 'monthly' | 'weekly'; last_run_at: string | null
  last_status: { ok: boolean; at: string; reason?: string } | null
}
export const lawsyncApi = {
  get: () => http.get<LawsyncSettings>('/admin/settings/lawsync'),
  save: (enabled: boolean, interval: string) => http.put<LawsyncSettings>('/admin/settings/lawsync', { enabled, interval }),
  run: () => http.post<{ queued: boolean }>('/admin/lawsync/run'),
}

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
  user_daily_cap: number
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
  sources: string[]
  enabled: boolean
  sort: number
  created_at: string
  updated_at: string
}

export type CompanyReqBody = Pick<CompanyReqRow, 'text' | 'doc_types' | 'stances' | 'sources' | 'enabled' | 'sort'>

export interface RequirementVersionRow {
  version: number
  created_by_name: string | null
  created_at: string
  snapshot: {
    requirements: { code: string; text: string; doc_types: string[]; stances: string[]; sources?: string[]; sort: number }[]
    company_profile: string
  }
}

export const adminRequirementsApi = {
  list: () => http.get<{ items: CompanyReqRow[]; version: number | null; combo_counts: { doc_type: string; stance: string; source: string; count: number }[] }>('/admin/company-requirements'),
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
  ourParties: () => http.get<{ items: TermRow[] }>('/admin/our-parties'),
  addOurParty: (name: string) => http.post<TermRow>('/admin/our-parties', { name }),
  removeOurParty: (id: string) => http.delete<void>(`/admin/our-parties/${id}`),
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
  free_measured_at: string | null
  latest: BackupRow | null
  items: BackupRow[]
}

export interface OutboundRow {
  id: number
  created_at: string
  user_id: string | null
  user_name: string | null
  task_title: string | null
  purpose: 'review' | 'chat' | 'test' | 'assist'
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
  body_notice: string | null
}

export interface OutboundQuery {
  purpose?: 'review' | 'chat' | 'test' | 'assist'
  user_id?: string
  from?: string
  to?: string
  status?: string
  page?: number
}

export interface AuditRow {
  id: number
  created_at: string
  user_name: string | null
  action: string
  target_type: string | null
  target_id: string | null
  detail: Record<string, unknown>
  ip: string | null
}

export interface AuditQuery {
  user_id?: string
  action?: string
  from?: string
  to?: string
  page?: number
}

export interface SystemStatus {
  assistant_sessions: number
  version: string
  queued_jobs: number
  data_free_bytes: number | null
  files_bytes: number | null
  db_bytes: number | null
  backup_dir_bytes: number | null
  latest_backup_bytes: number | null
  backup_free_bytes: number | null
  backup_stats_at: string | null
  estimated_days: number | null
  estimate_reason: 'insufficient_history' | 'no_growth' | 'unavailable' | null
  storage_measured_at: string
}

export interface RetentionSettings {
  original_days: number
  result_days: number
  outbound_days: number
  audit_days: number
  unfinished_days: number
}

export const adminDataApi = {
  getBackupSettings: () => http.get<BackupSettings>('/admin/settings/backup'),
  saveBackupSettings: (time: string, keep: number) => http.put<BackupSettings>('/admin/settings/backup', { time, keep }),
  runBackup: () => http.post<{ queued: boolean }>('/admin/backup/run'),
  backupRecords: () => http.get<BackupRecords>('/admin/backup/records'),
  getRetention: () => http.get<RetentionSettings>('/admin/settings/retention'),
  saveRetention: (values: RetentionSettings) => http.put<RetentionSettings>('/admin/settings/retention', values),
  outboundLogs: (q: OutboundQuery) =>
    http.get<{ items: OutboundRow[]; total: number; page: number; page_size: number }>('/admin/outbound-logs', { ...q }),
  outboundLog: (id: number) => http.get<OutboundDetail>(`/admin/outbound-logs/${id}`),
  systemStatus: () => http.get<SystemStatus>('/admin/system/status'),
  auditLogs: (q: AuditQuery) => http.get<{ items: AuditRow[]; total: number; page: number; page_size: number }>('/admin/audit-logs', { ...q }),
}
