import type { SessionItem } from './assistant'
import { http } from './http'

export interface SearchReview { id: string; title: string; status: string; created_at: string; policy?: 'ai' | 'local_only'; run_status?: string | null }
export interface SearchSession { id: string; title: string; purpose: 'chat' | 'meeting' | 'translate'; last_activity_at: string; is_review?: boolean
  /** 往返线（V36-F-01） */
  status?: string; policy?: 'ai' | 'local_only'; run_status?: string | null; translation_engine?: 'local' | 'cloud' | null; translation_status?: string | null
  /** 会议的说明行（V36-F-03）：迷你往返线按转写、校对、纪要的状态画 */
  meeting?: SessionItem['meeting'] }
export interface SearchFinding { id: string; task_id: string; task_title: string; kind: 'review' | 'assistant'; title: string }
export interface SearchBid { id: string; title: string; last_activity_at: string; status?: string; stage?: string; model_state?: string | null }
export interface SearchResult { reviews: SearchReview[]; sessions: SearchSession[]; findings: SearchFinding[]; bids: SearchBid[] }

/** 全局搜索：搜索词放请求体（不进地址与访问日志），只返回本人的任务。 */
export const searchApi = {
  search: (q: string) => http.post<SearchResult>('/search', { q }),
}
