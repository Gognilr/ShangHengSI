import { http } from './http'

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
  model_name: string | null
}

export interface RequirementResult {
  req_id: string
  req_source: string
  req_text: string
  dimension: string | null
  verdict: string
  note: string | null
}

export type HandlingStatus = 'pending' | 'adopted' | 'ignored'

export interface Finding {
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
}

export interface RunDetail {
  id: string
  task_id: string
  run_no: number
  status: string
  error_message: string | null
  stance: string
  source: string
  user_note: string | null
  main_doc_type: string
  model_name: string | null
  prompt_version: string | null
  builtin_req_version: string | null
  company_req_version: number | null
  queued_at: string
  started_at: string | null
  finished_at: string | null
  queue_position: number | null
  coverage: unknown | null
  doc_summary: string | null
  sanctions: { level: string | null; reasons: string[] }
  requirement_results: RequirementResult[]
  findings: Finding[]
}

export const runsApi = {
  create: (taskId: string) => http.post<RunCreated>(`/tasks/${taskId}/runs`),
  list: (taskId: string) => http.get<{ items: RunListItem[] }>(`/tasks/${taskId}/runs`),
  get: (runId: string) => http.get<RunDetail>(`/runs/${runId}`),
  patchFinding: (findingId: string, body: { handling_status?: HandlingStatus; handling_note?: string | null }) =>
    http.patch<Finding>(`/findings/${findingId}`, body),
}

/** 确认发送时后端返回的 409 / 422 错误码对应的中文提示（设计八）。 */
export const RUN_ERROR_MESSAGES: Record<string, string> = {
  LOCAL_ONLY: '该任务的外发策略为“不外发，仅本地校验”，不能发送给模型',
  TASK_STATE_CONFLICT: '任务状态已变化，请刷新后再试',
  SUSPECTS_PENDING: '还有疑似遗漏没有处理，请全部处理后再提交',
  OUR_PARTY_REQUIRED: '请先选择哪一方是我方',
  OUTBOUND_BLOCKED: '外发文本中发现未脱敏的敏感信息，已阻止发送',
}
