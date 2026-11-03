import { http } from './http'

/** 回程文字中未能还原的标记（SPEC 9.9），位置为码点下标。 */
export type MarkKind = 'variant' | 'unknown' | 'shared' | 'note' | 'date' | 'literal' | 'calc'
export interface Mark { start: number; end: number; kind: MarkKind }
export type MarkedField = 'title' | 'problem' | 'impact' | 'suggestion' | 'suggested_text' | 'basis'
export type UnableReason = 'redacted' | 'absent' | 'other'

export interface RunCreated {
  id: string
  run_no: number
  status: string
}

export interface RunListItem {
  id: string
  run_no: number
  status: string
  queued_at: string
  finished_at: string | null
  model_id: number | null
  model_label: string | null
  model_name: string | null
}

export interface RequirementResult {
  req_id: string
  req_source: string
  req_text: string
  dimension: string | null
  verdict: string
  note: string | null
  unable_reason: UnableReason | null
}

export type HandlingStatus = 'pending' | 'adopted' | 'ignored'

export interface Finding {
  law_refs: { title: string; article_no: string | null; text: string }[]
  law_unverified: boolean
  source: string
  id: string
  seq: number
  title: string
  category: string
  severity: string
  req_ids: string[]
  locs: string[]
  location: string | null
  missing: boolean
  cite_status: string
  quote: string | null
  problem: string
  impact: string | null
  suggestion: string | null
  suggested_text: string | null
  basis: string | null
  handling_status: HandlingStatus
  handling_note: string | null
  quote_excerpt: string | null
  marks: Partial<Record<MarkedField, Mark[]>>
  unresolved: number
}

/** 外发回执（SPEC 18.29）：只有数字、布尔与模型显示名；旧轮次为 null。 */
export interface RunReceipt {
  model: string | null
  protected: Record<string, number>
  protected_total: number
  kept: number
  date_shifted: boolean
  ratios_sent: boolean
  files: number
  not_sent: { images: number; comments: number; headers_footers: number; hidden_sheets: number }
  sent_at: string | null
  calls: number
  chars: number
  rechecks: number
  blocked: number
}

export interface Coverage {
  summary: string
  notes: string[]
  unable_by_reason?: Record<UnableReason, number>
  /** V31Q-67 ①：L-21 条数，旧轮次没有 */
  ai_instructions?: number
}

/** 本轮实际发送的文字；外发记录正文已按保留期清理时为 { available: false }。 */
export interface SentItem { seq: number; chars: number; text: string }
export type SentText = SentItem[] | { available: false }

export interface RunDetail {
  template: { name: string; status: string } | null
  id: string
  task_id: string
  run_no: number
  status: string
  error_message: string | null
  stance: string
  source: string
  user_note: string | null
  main_doc_type: string
  model_id: number | null
  model_label: string | null
  model_name: string | null
  prompt_version: string | null
  builtin_req_version: string | null
  company_req_version: number | null
  queued_at: string
  started_at: string | null
  finished_at: string | null
  queue_position: number | null
  chunk_count: number
  progress: { done: number; total: number } | null
  coverage: Coverage | null
  doc_summary: string | null
  doc_summary_marks: Mark[]
  receipt: RunReceipt | null
  restore_stats: { restored?: number; marked?: number }
  sanctions: { level: string | null; reasons: string[] }
  requirement_results: RequirementResult[]
  findings: Finding[]
}

export const runsApi = {
  create: (taskId: string, useTemplate = true, modelId?: number) => http.post<RunCreated>(`/tasks/${taskId}/runs`, { use_template: useTemplate, model_id: modelId }),
  list: (taskId: string) => http.get<{ items: RunListItem[] }>(`/tasks/${taskId}/runs`),
  get: (runId: string) => http.get<RunDetail>(`/runs/${runId}`),
  sent: (runId: string) => http.get<SentText>(`/runs/${runId}/sent`),
  patchFinding: (findingId: string, body: { handling_status?: HandlingStatus; handling_note?: string | null }) =>
    http.patch<Finding>(`/findings/${findingId}`, body),
}

/** 确认发送时后端返回的 409 / 422 错误码对应的中文提示（设计八）。 */
export const RUN_ERROR_MESSAGES: Record<string, string> = {
  CAP_REACHED: '已达本月调用上限，请联系管理员',
  USER_CAP_REACHED: '已达今日个人调用上限，请明天再试或联系管理员',
  LOCAL_ONLY: '该任务的外发策略为“不外发，仅本地校验”，不能发送给模型',
  TASK_STATE_CONFLICT: '任务状态已变化，请刷新后再试',
  FILES_PARSING: '还有文件正在解析，请稍候',
  FILE_PARSE_FAILED: '请先删除解析失败的文件',
  OUR_PARTY_REQUIRED: '请先选择哪一方是我方',
  OUTBOUND_BLOCKED: '外发文本中发现未脱敏的敏感信息，已阻止发送',
}
