import { http } from './http'
import type { TaskDetail, TaskFile, Source, Stance } from './tasks'
import type { PreviewNodes } from './preview'
import type { Calc, ChatDecision, ChatPreview } from './chat'
import type { Mark } from './runs'
import type { LocalFinding } from './localFindings'

export type Template = 'translate' | 'summary' | 'reply' | 'crosscheck' | 'compare' | 'minutes'
export interface Media {
  stage: 'decoding' | 'queued' | 'waiting_memory' | 'transcribing' | 'done' | 'failed'
  progress: number; duration_ms?: number; error: 'unavailable' | 'timeout' | 'memory_timeout' | 'transcribe_failed' | 'decode_failed' | null
  queue_position: number
  /** 现场录音时的打点（毫秒偏移），转写后在逐字稿对应位置显示“标记” */
  marks?: number[]
}
export interface SessionFile extends TaskFile { confirmed_at: string | null; media: Media | null; speakers: Record<string, string | null> | null }
export interface Suggestion { loc: string; start: number; end: number; from: string; to: string }
export const ASSISTANT_ERROR_MESSAGES: Record<string, string> = { ASR_DISABLED: '录音转写未启用，请联系管理员' }
export interface SessionItem {
  id: string; title: string; status: string; file_count: number; last_activity_at: string
  original_deleted: boolean; generating?: boolean; purpose: Purpose
}
/** 会话的入口（V31-U5）：智能助手、会议纪要、翻译各列自己的记录 */
export type Purpose = 'chat' | 'meeting' | 'translate'
export interface Session extends SessionItem {
  stance: Stance; source: Source; policy: 'ai' | 'local_only'; error_message: string | null
  created_at: string; files: SessionFile[]; can_send: boolean; send_reason: string | null; code_count: number
  kb_enabled?: boolean
  participants: string[]; asr_enabled: boolean; mt_enabled: boolean
}
export interface SessionPage { items: SessionItem[]; total: number; page: number; page_size: number; kb_enabled?: boolean; asr_enabled: boolean; mt_enabled: boolean }
export interface Quote { file: number; unit_price: string | null; amount: string | null; currency: string | null; difference_pct: string | null; status: string; qty: string }
export interface Comparison {
  notice: string; notes: string; suppliers: { file: number; name: string; terms: string }[]
  rows: { item: string; unit: string; quotes: Quote[]; minimum_files: number[]; status: string }[]
  totals: Record<string, { amount: string | null; currency: string | null; status: string }>
}
export interface Message {
  marks: Mark[]; calcs: Calc[]
  model_id: number | null; model_label: string | null
  id: number; role: 'user' | 'assistant'; content: string; status: string; template: Template | null
  params: Record<string, unknown>; created_at: string; error: string | null; error_code: string | null
  extras: { segments_total?: number; segments_done?: number; kb_sent?: number; kb_dropped?: number
    history_sources?: { task_id: string; month: string }[]; translation_unaligned?: number[] }
  translation?: Record<string, string>; translation_notice?: string; compare?: Comparison; draft?: string
  /** 用户消息：实际发给模型的提问文字（只含代号）；本地翻译的消息没有 */
  sent?: string
}
export interface MessageInput {
  model_id?: number
  message: string; template?: Template | null; params: Record<string, unknown>
  use_kb: boolean; use_history: boolean; decisions?: ChatDecision[]
}
const base = '/assistant/sessions'
export const assistantApi = {
  list: (page = 1, q = '', purpose: Purpose | '' = '') => http.get<SessionPage>(base, { page, q, purpose }),
  create: (purpose: Purpose = 'chat') => http.post<Session>(base, { purpose }),
  get: (id: string) => http.get<Session>(`${base}/${id}`),
  patch: (id: string, body: { title?: string; stance?: Stance; source?: Source }) => http.patch<Session>(`${base}/${id}`, body),
  remove: (id: string) => http.delete<void>(`${base}/${id}`),
  addFiles: (id: string, files: File[], onProgress: (loaded: number, total: number) => void = () => {}, marks: number[] = []) => {
    const form = new FormData(); files.forEach(file => form.append('files', file, file.name))
    if (marks.length) form.append('marks', JSON.stringify(marks))
    return http.postFormWithProgress<Session>(`${base}/${id}/files`, form, onProgress)
  },
  localFindings: (id: string) => http.get<{ items: LocalFinding[] }>(`${base}/${id}/local-findings`),
  participants: (id: string, names: string[]) => http.put<{ names: string[] }>(`${base}/${id}/participants`, { names }),
  speakers: (id: string, fid: string, map: Record<string, string | null>) => http.put<Record<string, string | null>>(`${base}/${id}/files/${fid}/speakers`, map),
  utterance: (id: string, fid: string, loc: string, body: { text?: string; speaker?: number }) => http.patch<{ ok: boolean }>(`${base}/${id}/files/${fid}/utterances/${encodeURIComponent(loc)}`, body),
  utterancesSpeaker: (id: string, fid: string, locs: string[], speaker: number) => http.post<{ ok: boolean }>(`${base}/${id}/files/${fid}/utterances-speaker`, { locs, speaker }),
  suggestions: (id: string, fid: string) => http.get<Suggestion[]>(`${base}/${id}/files/${fid}/suggestions`),
  applySuggestions: (id: string, fid: string, items: Omit<Suggestion, 'from'>[]) => http.post<{ ok: boolean }>(`${base}/${id}/files/${fid}/suggestions/apply`, { items }),
  audio: (id: string, fid: string) => http.getBlob(`${base}/${id}/files/${fid}/audio`),
  transcript: (id: string, fid: string) => http.getDownload(`${base}/${id}/files/${fid}/transcript`),
  retranscribe: (id: string, fid: string) => http.post<Session>(`${base}/${id}/files/${fid}/retranscribe`),
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
    original_expires_at: null, task_expires_at: null, template_suggestion: null, busy: false }
}
