import { http } from './http'

export interface SearchReview { id: string; title: string; status: string; created_at: string }
export interface SearchSession { id: string; title: string; last_activity_at: string }
export interface SearchResult { reviews: SearchReview[]; sessions: SearchSession[] }

/** 全局搜索：搜索词放请求体（不进地址与访问日志），只返回本人的任务。 */
export const searchApi = {
  search: (q: string) => http.post<SearchResult>('/search', { q }),
}
