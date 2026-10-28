import { http } from './http'
import type { TaskDetail, TaskFile, Source, Stance } from './tasks'
import type { PreviewNodes } from './preview'
import type { ChatDecision, ChatPreview } from './chat'

export type Template = 'translate' | 'summary' | 'reply' | 'crosscheck' | 'compare'
export interface SessionFile extends TaskFile { confirmed_at: string | null }
export interface SessionItem {
  id: string; title: string; status: string; file_count: number; last_activity_at: string
  original_deleted: boolean; generating?: boolean
}
export interface Session extends SessionItem {
  stance: Stance; source: Source; policy: 'ai' | 'local_only'; error_message: string | null
  created_at: string; files: SessionFile[]; can_send: boolean; send_reason: string | null; code_count: number
  kb_enabled?: boolean
}
export interface SessionPage { items: SessionItem[]; total: number; page: number; page_size: number; kb_enabled?: boolean }
export interface Quote { file: number; unit_price: string | null; amount: string | null; currency: string | null; difference_pct: string | null; status: string; qty: string }
export interface Comparison {
  notice: string; notes: string; suppliers: { file: number; name: string; terms: string }[]
  rows: { item: string; unit: string; quotes: Quote[]; minimum_files: number[]; status: string }[]
  totals: Record<string, { amount: string | null; currency: string | null; status: string }>
}
export interface Message {
  id: number; role: 'user' | 'assistant'; content: string; status: string; template: Template | null
  params: Record<string, unknown>; created_at: string; error: string | null; error_code: string | null
  extras: { segments_total?: number; segments_done?: number; kb_sent?: number; kb_dropped?: number
    history_sources?: { task_id: string; month: string }[]; translation_unaligned?: number[] }
  translation?: Record<string, string>; translation_notice?: string; compare?: Comparison; draft?: string
}
export interface MessageInput {
  message: string; template?: Template | null; params: Record<string, unknown>
  use_kb: boolean; use_history: boolean; decisions?: ChatDecision[]
}
const base = '/assistant/sessions'
export const assistantApi = {
  list: (page = 1, q = '') => http.get<SessionPage>(base, { page, q }),
  create: () => http.post<Session>(base),
  get: (id: string) => http.get<Session>(`${base}/${id}`),
  patch: (id: string, body: { title?: string; stance?: Stance; source?: Source }) => http.patch<Session>(`${base}/${id}`, body),
  remove: (id: string) => http.delete<void>(`${base}/${id}`),
  addFiles: (id: string, files: File[]) => {
    const form = new FormData(); files.forEach(file => form.append('files', file, file.name))
    return http.postForm<Session>(`${base}/${id}/files`, form)
  },
  removeFile: (id: string, fid: string) => http.delete<Session>(`${base}/${id}/files/${fid}`),
  nodes: (id: string, fid: string) => http.get<PreviewNodes>(`${base}/${id}/files/${fid}/nodes`),
  confirm: (id: string) => http.post<Session>(`${base}/${id}/confirm`),
  outbound: (id: string) => http.get<{ text: string; note: string | null }>(`${base}/${id}/outbound`),
  preview: (id: string, body: MessageInput) => http.post<ChatPreview>(`${base}/${id}/preview`, body),
  send: (id: string, body: MessageInput) => http.post<{ user: Message; assistant: Message }>(`${base}/${id}/messages`, body),
  messages: (id: string, after_id = 0) => http.get<{ items: Message[] }>(`${base}/${id}/messages`, { after_id }),
  delta: (id: string, mid: number, offset: number) => http.get<{ status: string; text_from_offset: string; offset: number; error: string | null; compare?: Comparison }>(`${base}/${id}/messages/${mid}`, { offset }),
  stop: (id: string, mid: number) => http.post<Message>(`${base}/${id}/messages/${mid}/stop`),
  retry: (id: string, mid: number) => http.post<{ user: Message; assistant: Message }>(`${base}/${id}/messages/${mid}/retry`),
  download: (id: string, mid: number, format: 'docx' | 'xlsx', variant = 'translation') => http.getDownload(`${base}/${id}/messages/${mid}/export?format=${format}&variant=${variant}`),
}
/** 确认组件复用审核的只读展示类型；不调用审核任务接口。 */
export function confirmationTask(session: Session): TaskDetail {
  return { ...session, status: 'awaiting_confirm', user_note: null, latest_run: null, queue_position: null,
    original_expires_at: null, task_expires_at: null, template_suggestion: null }
}
