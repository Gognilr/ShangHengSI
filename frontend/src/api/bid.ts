import { http } from './http'

export type BidStage = 'preparing' | 'ready' | 'checking' | 'checked'
export interface BidSummary { id: string; title: string; stage: BidStage; status: string; opening_date: string; last_activity_at: string }
export interface BidFile {
  id: string; bidder_id: string | null; name: string; ext: string; size_bytes: number; page_count: number | null
  has_password: boolean; needs_password: boolean; state: 'failed' | 'done' | 'waiting'; error: string | null; warnings: string[]; original_deleted: boolean
}
export interface Bidder { id: string; name: string; code: string; seq: number }
export interface BidProject extends BidSummary {
  policy: 'ai' | 'local_only'; busy: boolean; error_message: string | null; bidders: Bidder[]; files: BidFile[]
  limits: { bid_files: number; tender_files: number; file_mb: number; pdf_pages: number; ocr_pages: number }
  requirements: { enabled: number; extract_state: BidExtractState }
}
export type BidGroup = 'qualification' | 'substantive' | 'veto' | 'pricing' | 'validity' | 'signing' | 'format'
export type BidMethod = 'local' | 'model' | 'manual'
export type BidExtractState = null | 'redacting' | 'awaiting' | 'extracting' | 'done' | 'failed'
export interface BidLoc { file_id: string; loc: string; start: number; end: number }
export interface BidRequirement { id: string; group: BidGroup; text: string; locs: BidLoc[]; method: BidMethod; enabled: boolean; source: 'marked' | 'model' | 'default' | 'manual'; seq: number; file: string | null }
export interface BidParameters { ceiling: string | null; currency: 'CNY' | 'USD' | 'EUR' | 'HKD'; validity_days: number | null; deposit: string | null; deposit_forms: string; consortium: boolean | null; tax_included: boolean | null }
export interface BidCount { total: number; covered: number; missing: (BidLoc & { text: string })[] }
export interface BidRequirements {
  parameters: BidParameters; parameter_sources: Record<string, { file_id: string; loc: string }>; parameters_confirmed: boolean
  extracted: boolean; tender_changed: boolean; items: BidRequirement[]; counts: { star: BidCount; veto: BidCount }; version: number
  extract_state: BidExtractState; extract_error: string | null; stage: BidStage
  receipt: { chars: number; calls: number; protected: number; remaining: { monthly: number | null; daily: number | null } } | null
}
export interface BidTender { files: Record<string, string>; nodes: { file_id: string; loc: string; kind: string; text: string }[]; markers: { kind: 'star' | 'veto'; loc: string; start: number; end: number }[] }
export const bidGroupLabel: Record<BidGroup, string> = {
  qualification: '资格条件', substantive: '实质性条款（★）', veto: '否决条款', pricing: '报价要求', validity: '有效期与保证金', signing: '签章与格式页', format: '投标文件格式（范本）',
}
export const bidMethodLabel: Record<BidMethod, string> = { local: '本地规则', model: '模型判断', manual: '人工核对' }
export const bidSourceLabel: Record<BidRequirement['source'], string> = { marked: '原文标记', model: '模型提取 · 请核对', default: '默认（第五十一条）', manual: '手工' }
export interface BidSettings { title: string; opening_date: string; policy: 'ai' | 'local_only' }
export const bidStageLabel: Record<BidStage, string> = { preparing: '准备中', ready: '待开标', checking: '初审中', checked: '待核对' }
export const bidPolicyLabel: Record<BidSettings['policy'], string> = { local_only: '只做本地检查', ai: '本机检查 + 模型核对' }
/** 列表与项目页共用的状态：文字 + 圆点色调（visual.md 第五节“圆点 + 文字”） */
export function bidState(p: { stage: BidStage; status: string }): { label: string; tone: 'run' | 'warn' | 'ok' | 'idle' } {
  if (p.status === 'parsing') return { label: '解析中', tone: 'run' }
  if (p.status === 'parse_failed') return { label: '文件待处理', tone: 'warn' }
  if (p.stage === 'checking') return { label: '初审中', tone: 'run' }
  if (p.stage === 'checked') return { label: '待核对', tone: 'warn' }
  return { label: bidStageLabel[p.stage], tone: p.stage === 'ready' ? 'ok' : 'idle' }
}
export function formatSize(bytes: number): string {
  return bytes >= 1024 * 1024 ? `${(bytes / 1024 / 1024).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`
}
export const bidApi = {
  list: (page = 1) => http.get<{ items: BidSummary[]; total: number; checking_count: number }>('/bid', { page }),
  create: (body: BidSettings) => http.post<BidProject>('/bid', body),
  get: (id: string) => http.get<BidProject>(`/bid/${id}`),
  update: (id: string, body: BidSettings) => http.patch<BidProject>(`/bid/${id}`, body),
  remove: (id: string) => http.delete<void>(`/bid/${id}`),
  addBidder: (id: string, name: string) => http.post<BidProject>(`/bid/${id}/bidders`, { name }),
  renameBidder: (id: string, bidder: string, name: string) => http.patch<BidProject>(`/bid/${id}/bidders/${bidder}`, { name }),
  removeBidder: (id: string, bidder: string) => http.delete<BidProject>(`/bid/${id}/bidders/${bidder}`),
  upload: (id: string, files: File[], bidder?: string, groups?: string[], onProgress?: (loaded: number, total: number) => void) => {
    const form = new FormData()
    files.forEach(f => form.append('files', f))
    if (bidder) form.append('bidder_id', bidder)
    if (groups) form.append('groups', JSON.stringify(groups))
    return http.postFormWithProgress<BidProject>(`/bid/${id}/files`, form, onProgress ?? (() => {}))
  },
  removeFile: (id: string, fid: string) => http.delete<BidProject>(`/bid/${id}/files/${fid}`),
  password: (id: string, fid: string, password: string) => http.put<BidProject>(`/bid/${id}/files/${fid}/password`, { password }),
  reveal: (id: string, fid: string) => http.get<{ password: string | null }>(`/bid/${id}/files/${fid}/password`),
  parse: (id: string) => http.post<BidProject>(`/bid/${id}/parse`),
  tender: (id: string, view: 'original' | 'ai' = 'original') => http.get<BidTender>(`/bid/${id}/tender`, { view }),
  requirements: (id: string) => http.get<BidRequirements>(`/bid/${id}/requirements`),
  parameters: (id: string, body: BidParameters) => http.put<BidRequirements>(`/bid/${id}/parameters`, body),
  addRequirement: (id: string, body: { group: BidGroup; text: string; method: BidMethod; locs?: BidLoc[] }) => http.post<BidRequirements>(`/bid/${id}/requirements`, body),
  patchRequirement: (id: string, rid: string, body: Partial<Pick<BidRequirement, 'group' | 'text' | 'method' | 'enabled'>>) => http.patch<BidRequirements>(`/bid/${id}/requirements/${rid}`, body),
  removeRequirement: (id: string, rid: string) => http.delete<BidRequirements>(`/bid/${id}/requirements/${rid}`),
  reextract: (id: string) => http.post<BidRequirements>(`/bid/${id}/requirements/extract`),
  assist: (id: string, action: 'start' | 'confirm' | 'cancel') => http.post<BidRequirements>(`/bid/${id}/assist/${action}`),
  confirm: (id: string) => http.post<BidRequirements>(`/bid/${id}/confirm`),
}
