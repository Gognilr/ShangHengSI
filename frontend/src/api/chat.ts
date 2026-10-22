import { http } from './http'

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
    http.post<{ redacted: string; changed: boolean }>(`/tasks/${taskId}/chat/preview`, { message }),
  send: (taskId: string, message: string) =>
    http.post<{ user: ChatMessage; assistant: ChatMessage }>(`/tasks/${taskId}/chat`, { message }),
}
