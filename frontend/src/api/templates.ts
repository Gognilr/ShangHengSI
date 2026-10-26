import { http } from './http'

export interface TemplateRow {
  id: string; name: string; doc_types: string[]; stances: string[]; enabled: boolean
  clause_count: number; ready: boolean; created_at: string; updated_at: string
}
export interface TemplateClause {
  id: string; seq: number; title: string; text: string; stance: string; acceptable: string; escalate: string
}
export interface TemplateDetail extends TemplateRow { clauses: TemplateClause[] }
export const templatesApi = {
  list: () => http.get<{ items: TemplateRow[] }>('/admin/templates'),
  get: (id: string) => http.get<TemplateDetail>(`/admin/templates/${id}`),
  upload: (name: string, doc_types: string[], stances: string[], file: File) => {
    const form = new FormData()
    form.append('name', name); form.append('doc_types', JSON.stringify(doc_types)); form.append('stances', JSON.stringify(stances))
    form.append('file', file, file.name)
    return http.postForm<TemplateDetail>('/admin/templates', form)
  },
  patch: (id: string, body: { name?: string; doc_types?: string[]; stances?: string[]; enabled?: boolean }) =>
    http.patch<TemplateRow>(`/admin/templates/${id}`, body),
  remove: (id: string) => http.delete<void>(`/admin/templates/${id}`),
  clause: (id: string, cid: string, body: { stance: string; acceptable: string; escalate: string }) =>
    http.patch<TemplateClause>(`/admin/templates/${id}/clauses/${cid}`, body),
}
