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

export interface RedactionInfo {
  /** {类别代码: 替换次数} */
  summary: Record<string, number>
  /** M2 恒为空，疑似遗漏在 M4 提供 */
  suspects: unknown[]
}

export const redactionApi = {
  parties: (taskId: string) => http.get<{ items: Party[] }>(`/tasks/${taskId}/parties`),
  saveParties: (taskId: string, items: PartyUpdate[]) =>
    http.put<{ items: Party[] }>(`/tasks/${taskId}/parties`, { items }),
  redaction: (taskId: string) => http.get<RedactionInfo>(`/tasks/${taskId}/redaction`),
  /** 外发文本预览（代号版本），只能按纯文本显示。 */
  outbound: (taskId: string) => http.get<{ text: string }>(`/tasks/${taskId}/redaction/outbound`),
}
