import { http } from './http'
import type { TaskDetail, TaskFile, TaskListItem, Source, Stance } from './tasks'
import type { OcrCorrection, PreviewNodes } from './preview'
import type { Calc, ChatDecision, ChatPreview } from './chat'

export type AssistantPreview = Omit<ChatPreview, 'suspects'> & {
  material_version?: number
  suspects: (ChatPreview['suspects'][number] & { in_files: boolean })[]
}
import type { Mark, RunCreated, RunListItem } from './runs'
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
/** 历史记录行（V32-B-01）：工作台会话与旧审核任务合并列出 */
export type TranslationEngine = "local" | "cloud"
export interface HistoryItem extends TaskListItem { kind: 'session' | 'review' | 'bid'; purpose?: Purpose | null; /** 标书初审的阶段（高风险为本机否决类问题数，待处理恒为 0） */ stage?: string | null; /** 标书分类名（V34-B-01a） */ category?: string | null; last_activity_at: string; run_count: number; file_count: number
  /** 最新一轮：待处理问题数、可导出报告的轮次 id、审核进行中（V321-E-04） */
  pending_count: number; latest_run_id: string | null; reviewing: boolean
  /** 最新一轮被发送前复检拦下（V35-F-03） */
  blocked?: boolean; is_review?: boolean; translation_engine?: TranslationEngine | null; translation_status?: string | null
  /** 往返线（V36-F-01）：外发策略、最新一轮状态、正在生成回复、会议说明行、标书模型补查状态 */
  policy?: 'ai' | 'local_only'; run_status?: string | null; generating?: boolean; meeting?: SessionItem['meeting']; model_state?: string | null }
/** 侧栏菜单右侧的数字：待确认遮住的会话、旧审核任务与标书项目（V35-F-02） */
export interface Waiting { workbench: number; review: number; bid: number; meeting: number; translate: number }
/** 工作台信任条的本人本月数字（V35-F-03）；daily_cap 为 0 表示不限 */
export interface MyStats { masked: number; blocked: number; calls: number; today_calls: number; daily_cap: number }
export interface HistoryPage { items: HistoryItem[]; total: number; page: number; page_size: number }
/** 会话发起审核（V32-A-02）；file_ids 不传为全部已确认文件 */
export interface SessionRunInput {
  stance: Stance; source: Source; model_id?: number; user_note?: string | null
  file_ids?: string[]; main_file_id?: string; expected_material_version?: number; use_template?: boolean
  /** 同一事务里先确认待确认的文件再发起（V321-E-06） */
  confirm_pending?: boolean
  /** delta：只审变化与上轮未决问题（V35-D-03） */
  scope?: 'full' | 'delta'
}
/** 审核范围预览（V35-D-03，diff.md 4.1）：能否只审变化、变化条款与上轮问题数 */
export interface DeltaInfo {
  available: boolean; reason?: string; recommended?: boolean; identical?: boolean; empty?: boolean
  no?: number; prev_no?: number; base_run_no?: number; changed_clauses?: number; total_clauses?: number
  priors_sent?: number; priors_total?: number; advice?: string
}
export interface SessionItem {
  id: string; title: string; status: string; file_count: number; last_activity_at: string
  original_deleted: boolean; generating?: boolean; purpose: Purpose
  /** 审核排队或进行中、已发起的轮数（侧栏列表；V321-E-03） */
  reviewing?: boolean; run_count?: number; is_review?: boolean
  translation_engine?: TranslationEngine | null; translation_status?: string | null
  /** 会议列表的说明行（V35-F-08）：时长、发言人数、状态 */
  meeting?: { duration_ms: number; speakers: number; state: 'transcribing' | 'failed' | 'proofread' | 'confirmed' | 'minuted' } | null
  /** 往返线（V36-F-01）：外发策略与最新一轮的状态 */
  policy?: 'ai' | 'local_only'; run_status?: string | null
}
/** 会话的入口（V31-U5）：智能助手、会议纪要、翻译各列自己的记录 */
export type Purpose = 'chat' | 'meeting' | 'translate'
export function isReviewSession(item?: { is_review?: boolean; run_count?: number } | null) {
  return Boolean(item?.is_review || item?.run_count)
}

export function sessionLocation(item: { id: string; purpose?: Purpose | null; is_review?: boolean; run_count?: number }, canReview: boolean) {
  const prefix = item.purpose === 'meeting' || item.purpose === 'translate' ? item.purpose : 'assistant'
  return { path: `/${prefix}/${item.id}`, query: prefix === 'assistant' && canReview && isReviewSession(item) ? { mode: 'review' } : {} }
}

export interface Session extends SessionItem {
  stance: Stance; source: Source; policy: 'ai' | 'local_only'; error_message: string | null
  created_at: string; files: SessionFile[]; can_send: boolean; send_reason: string | null; code_count: number
  kb_enabled?: boolean
  participants: string[]; asr_enabled: boolean; mt_enabled: boolean
  /** 影响外发正文的提交次数；发送与发起审核时带上，不一致返回 409 MATERIAL_CHANGED（V32-A-01） */
  material_version: number
  /** 会话发起的审核排队或进行中（V32-A-02） */
  review_running: boolean
  /** 解析阶段（V321-E-06）：整批阶段与每个文件的阶段；OCR 时 done / total 为页数。排队中为空对象，不在解析时为 null */
  parse_progress?: ParseProgress | null
  /** 跨会话提示（V35-D-02）：本人近 30 天另一个会话的主文件名去掉稿次字样后相同 */
  draft_hint?: { session_id: string; title: string; file_name: string } | null
}
/** 多稿对比（V35-D-02）：本稿与上一稿的变化，本机现算 */
export interface DiffSeg { t: 'eq' | 'ins' | 'del'; s: string; u?: boolean }
export type DiffType = 'mod' | 'add' | 'del' | 'move' | 'format'
export interface DiffChange {
  id: string; type: DiffType; loc: string | null; prev_loc: string | null
  /** 删除：挂在本稿哪一段之前；after 为删在文末，挂在最后一段之后 */
  anchor?: string | null; after?: boolean
  clause: string; prev_clause?: string; segs: DiffSeg[]
  /** null：PDF、扫描件没有修订概念 */
  untracked: boolean | null; mixed: boolean
  brief: { ctx: string; old: string; new: string; text?: string }
}
export interface DiffResult {
  file_id: string; prev_file_id: string; prev_name: string; no: number; prev_no: number
  /** exact：按“拒绝修订”文字判断；flag：本稿解析较早，只能按段落判断；none：不判断留痕 */
  trace: 'exact' | 'flag' | 'none'; ocr: boolean; elapsed_ms: number
  summary: Record<DiffType | 'untracked', number>; changes: DiffChange[]
}
export interface ParseProgress { phase?: 'files' | 'detect'; files?: Record<string, { stage: string; done: number | null; total: number | null }> }
export interface SessionPage { items: SessionItem[]; total: number; page: number; page_size: number; kb_enabled?: boolean; asr_enabled: boolean; mt_enabled: boolean }
export interface Quote { file: number; unit_price: string | null; amount: string | null; currency: string | null; difference_pct: string | null; status: string; qty: string }
export interface Comparison {
  notice: string; notes: string; suppliers: { file: number; name: string; terms: string }[]
  rows: { item: string; unit: string; quotes: Quote[]; minimum_files: number[]; status: string }[]
  totals: Record<string, { amount: string | null; currency: string | null; status: string }>
}
/** 单证核对（V34-A-08）：本机抽取比较的结果表；by = 'model' 的值由模型定位 */
export interface CrosscheckValue { text: string; loc: string; place: string; by?: 'model' }
export interface Crosscheck {
  notice: string; files: { file: number; name: string }[]
  rows: { field: string; label: string; status: string; values: Record<string, CrosscheckValue[]> }[]
  findings: string[]; notes: string[]
}
export interface Message {
  marks: Mark[]; calcs: Calc[]
  model_id: number | null; model_label: string | null
  id: number; role: 'user' | 'assistant'; content: string; status: string; template: Template | null
  params: Record<string, unknown>; created_at: string; error: string | null; error_code: string | null
  extras: { segments_total?: number; segments_done?: number; kb_sent?: number; kb_dropped?: number
    translation_unaligned?: number[] }
  /** 回复依据（V321-E-13）：只含本地登记过的来源；cites 为正文中渲染成锚点的原样文字 */
  sources?: MessageSource[]; cites?: { token: string; ref: string }[]
  translation?: Record<string, string>; translation_notice?: string; compare?: Comparison; crosscheck?: Crosscheck; draft?: string
  /** 用户消息：实际发给模型的提问文字（只含代号）；本地翻译的消息没有 */
  sent?: string
  /** 按问题讨论：消息所属的审核问题（V32-B-02）；普通对话为 null */
  finding_id?: string | null
}
export interface MessageSource { ref: string; kind: 'para' | 'kb' | 'law'; label: string; cited: boolean }
export interface SourceDetail { kind: MessageSource['kind']; title: string; text: string }
export interface MessageInput {
  expected_material_version?: number
  model_id?: number
  message: string; template?: Template | null; params: Record<string, unknown>
  use_kb: boolean; decisions?: ChatDecision[]
  finding_id?: string
}
const base = '/assistant/sessions'
export const assistantApi = {
  list: (page = 1, q = '', purpose: Purpose | '' = '') => http.get<SessionPage>(base, { page, q, purpose }),
  /** 侧栏各菜单等本人处理的数量（V35-F-02） */
  waiting: () => http.get<Waiting>(`${base}/waiting`),
  stats: () => http.get<MyStats>(`${base}/stats`),
  history: (query: { page?: number; q?: string; from?: string; to?: string; type?: 'review' | 'chat' | 'bid' | 'meeting' | 'translate' }) => http.get<HistoryPage>(`${base}/history`, query),
  runs: (id: string) => http.get<{ items: RunListItem[] }>(`${base}/${id}/runs`),
  createRun: (id: string, body: SessionRunInput) => http.post<RunCreated>(`${base}/${id}/runs`, body),
  outboundReview: (id: string, query: { stance?: Stance; file_ids?: string[]; include_pending?: boolean; scope?: 'full' | 'delta'; main_file_id?: string } = {}) =>
    http.get<{ text: string; note: string | null; material_version: number; chars?: number; delta?: DeltaInfo }>(`${base}/${id}/outbound`,
      { mode: 'review', ...query, include_pending: query.include_pending ? 'true' : undefined }),
  raw: (id: string, fid: string) => http.getBlob(`${base}/${id}/files/${fid}/raw`),
  diff: (id: string, fileId: string) => http.get<DiffResult>(`${base}/${id}/diff?file_id=${encodeURIComponent(fileId)}`),
  diffExport: (id: string, fileId: string) => http.getDownload(`${base}/${id}/diff/export?file_id=${encodeURIComponent(fileId)}`),
  setDraft: (id: string, fid: string, prev: string | null) => http.put<Session>(`${base}/${id}/files/${fid}/draft`, { prev_file_id: prev }),
  create: (purpose: Purpose = 'chat', reviewIntent = false, translationEngine?: TranslationEngine) => http.post<Session>(base, {
    purpose, ...(reviewIntent ? { review_intent: true } : {}), ...(translationEngine ? { translation_engine: translationEngine } : {}),
  }),
  get: (id: string) => http.get<Session>(`${base}/${id}`),
  patch: (id: string, body: { title?: string; stance?: Stance; source?: Source; translation_engine?: TranslationEngine }) => http.patch<Session>(`${base}/${id}`, body),
  remove: (id: string) => http.delete<void>(`${base}/${id}`),
  addFiles: (id: string, files: File[], onProgress: (loaded: number, total: number) => void = () => {}, marks: number[] = [], draftOf?: string) => {
    const form = new FormData(); files.forEach(file => form.append('files', file, file.name))
    if (marks.length) form.append('marks', JSON.stringify(marks))
    if (draftOf) form.append('draft_of', draftOf)
    return http.postFormWithProgress<Session>(`${base}/${id}/files`, form, onProgress)
  },
  addText: (id: string, text: string) => http.post<Session>(`${base}/${id}/text`, { text }),
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
  correctOcr: (id: string, fid: string, loc: string, body: OcrCorrection) =>
    http.patch<{ material_version: number }>(`${base}/${id}/files/${fid}/ocr/${encodeURIComponent(loc)}`, body),
  nodes: (id: string, fid: string) => http.get<PreviewNodes>(`${base}/${id}/files/${fid}/nodes`),
  confirm: (id: string) => http.post<Session>(`${base}/${id}/confirm`),
  outbound: (id: string) => http.get<{ text: string; note: string | null }>(`${base}/${id}/outbound`),
  preview: (id: string, body: MessageInput) => http.post<AssistantPreview>(`${base}/${id}/preview`, body),
  send: (id: string, body: MessageInput) => http.post<{ user: Message; assistant: Message }>(`${base}/${id}/messages`, body),
  messages: (id: string, after_id = 0) => http.get<{ items: Message[] }>(`${base}/${id}/messages`, { after_id }),
  delta: (id: string, mid: number, offset: number) => http.get<{ status: string; text_from_offset: string; offset: number; error: string | null; compare?: Comparison; crosscheck?: Crosscheck }>(`${base}/${id}/messages/${mid}`, { offset }),
  source: (id: string, mid: number, ref: string) => http.get<SourceDetail>(`${base}/${id}/messages/${mid}/sources/${encodeURIComponent(ref)}`),
  stop: (id: string, mid: number) => http.post<Message>(`${base}/${id}/messages/${mid}/stop`),
  retry: (id: string, mid: number) => http.post<{ user: Message; assistant: Message }>(`${base}/${id}/messages/${mid}/retry`),
  download: (id: string, mid: number, format: 'docx' | 'xlsx', variant = 'translation') => http.getDownload(`${base}/${id}/messages/${mid}/export?format=${format}&variant=${variant}`),
}
/** 确认组件复用审核的只读展示类型；不调用审核任务接口。 */
export function confirmationTask(session: Session): TaskDetail {
  return { ...session, status: 'awaiting_confirm', user_note: null, latest_run: null, queue_position: null,
    original_expires_at: null, task_expires_at: null, template_suggestion: null, busy: false }
}
