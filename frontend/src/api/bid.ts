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
