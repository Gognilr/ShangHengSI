import { http } from './http'
import type { UserCategory } from './redaction'

export interface ChatSuspect { start: number; end: number; text: string; reason: string }
export interface ChatPreview { redacted: string; changed: boolean; suspects: ChatSuspect[] }
export interface ChatDecision { start: number; end: number; decision: 'replace' | 'keep'; category?: UserCategory }

export interface ChatMessage {
  id: number
  role: string
  content: string
  created_at: string
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
