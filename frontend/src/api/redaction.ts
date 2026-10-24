import { http } from './http'

export interface Party {
  id: string
  code: string
  name: string
  role_label: string
  country: string | null
  org_type: string | null
  is_our_party: boolean
}

export interface PartyUpdate {
  id: string
  is_our_party: boolean
  country: string
  org_type: string
}

/** 疑似遗漏（text、before、after 是本人原文，只能按纯文本显示）。 */
export interface Suspect {
  id: number
  loc: string
  reason: string
  /** pending / replace / ignore */
  decision: string
  category: string | null
  text: string
  before: string
  after: string
}

/** 已替换的片段；char_start / char_end 按 Unicode 字符（码点）计，与后端一致。 */
export interface RedactionItemPos {
  loc: string
  char_start: number
  char_end: number
  category: string
  placeholder: string
  can_revoke: boolean
}

export interface RedactionInfo {
  /** {类别代码: 替换次数} */
  summary: Record<string, number>
  suspects: Suspect[]
  items: RedactionItemPos[]
}

/** 确认脱敏页的十类；追问界面仍只提供既有三类。 */
export type UserCategory = 'ORG' | 'PERSON' | 'IM' | 'ACCOUNT' | 'IDNO' | 'REFNO' | 'ADDR' | 'AMOUNT' | 'PROJECT' | 'CUSTOM'

/** 手动补充、疑似遗漏决定的错误提示（按错误码，其余用接口返回的 message）。 */
export const REDACTION_ERROR_MESSAGES: Record<string, string> = {
  INVALID_AMOUNT: '金额须为可解析的数字，请重新选择',
  NOT_REVOCABLE: '该替换项不能撤销，请刷新后核对',
  TERM_IN_PROMPT: '所选文字在审核提示中也会出现，设为敏感信息后将无法发送，请只选择具体的名称或编号',
  TASK_STATE_CONFLICT: '任务状态已变化，请刷新后再试',
  SUSPECT_NOT_FOUND: '该条疑似遗漏已不存在，请刷新后再试',
  INVALID_SELECTION: '所选位置不正确',
  ALREADY_REDACTED: '所选文字已被替换，请选择未替换的文字',
  SELECTION_LENGTH: '请选择 2–100 个字符',
}

export const redactionApi = {
  parties: (taskId: string) => http.get<{ items: Party[] }>(`/tasks/${taskId}/parties`),
  saveParties: (taskId: string, items: PartyUpdate[]) =>
    http.put<{ items: Party[] }>(`/tasks/${taskId}/parties`, { items }),
  redaction: (taskId: string) => http.get<RedactionInfo>(`/tasks/${taskId}/redaction`),
  decideSuspect: (taskId: string, suspectId: number, decision: 'replace' | 'ignore', category: UserCategory | null) =>
    http.post<RedactionInfo>(`/tasks/${taskId}/redaction/suspects/${suspectId}`, { decision, category }),
  manual: (taskId: string, body: { loc: string; char_start: number; char_end: number; category: UserCategory }) =>
    http.post<RedactionInfo>(`/tasks/${taskId}/redaction/manual`, body),
  revoke: (taskId: string, body: { loc: string; char_start: number; char_end: number }) =>
    http.post<RedactionInfo>(`/tasks/${taskId}/redaction/revoke`, body),
  /** 外发文本预览（代号版本），只能按纯文本显示。 */
  outbound: (taskId: string) => http.get<{ text: string; note: string | null }>(`/tasks/${taskId}/redaction/outbound`),
}
