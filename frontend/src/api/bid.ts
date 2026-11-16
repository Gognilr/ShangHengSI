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
}
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
}
