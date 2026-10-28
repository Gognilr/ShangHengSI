import { http } from './http'

export interface KbDoc {
  id: string; name: string; status: 'parsing' | 'ready' | 'failed'; error_message: string | null
  enabled: boolean; chunk_count: number; created_by: string; uploaded_by: string
  created_at: string; updated_at: string
}
export interface KbChunk { id: number; seq: number; heading: string | null; text: string }
export const kbApi = {
  list: () => http.get<{ items: KbDoc[]; enabled: boolean }>('/admin/kb'),
  setting: (enabled: boolean) => http.patch<{ enabled: boolean }>('/admin/kb/settings', { enabled }),
  upload: (file: File) => { const data = new FormData(); data.append('file', file, file.name); return http.postForm<KbDoc>('/admin/kb', data) },
  patch: (id: string, body: { enabled?: boolean; name?: string }) => http.patch<KbDoc>(`/admin/kb/${id}`, body),
  remove: (id: string) => http.delete<{ ok: boolean }>(`/admin/kb/${id}`),
  chunks: (id: string) => http.get<{ items: KbChunk[] }>(`/admin/kb/${id}/chunks`),
}
