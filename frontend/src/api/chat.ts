import { http } from './http'
import type { UserCategory } from './redaction'
import type { Mark } from './runs'

/** 回复中【算式】行的本机计算（SPEC 13）；line_start / line_end 为还原后回复的码点偏移。 */
export type CalcStatus = 'ok' | 'unknown_code' | 'unparsable' | 'currency_mixed' | 'div_zero'
export interface Calc { line_start: number; line_end: number; expression: string; result: string | null; status: CalcStatus }

export interface ChatSuspect { start: number; end: number; text: string; reason: string }
export interface ChatPreview { redacted: string; changed: boolean; suspects: ChatSuspect[] }
export interface ChatDecision { start: number; end: number; decision: 'replace' | 'keep'; category?: UserCategory }

export interface ChatMessage {
  id: number
  role: string
  content: string
  created_at: string
  marks: Mark[]
  calcs: Calc[]
}

export interface ChatList {
  available: boolean
  reason: string | null
  items: ChatMessage[]
}

export const chatApi = {
  list: (taskId: string) => http.get<ChatList>(`/tasks/${taskId}/chat`),
  preview: (taskId: string, message: string) =>
    http.post<ChatPreview>(`/tasks/${taskId}/chat/preview`, { message }),
  send: (taskId: string, message: string, decisions: ChatDecision[] = []) =>
    http.post<{ user: ChatMessage; assistant: ChatMessage }>(`/tasks/${taskId}/chat`, { message, decisions }),
}
