import { http } from './http'

export interface LawRow {
  id: number; title: string; category: string; status: string
  published_on: string | null; effective_on: string | null; replaced_by: string | null
  source: string; has_fulltext: boolean
}
export interface LawDiffItem {
  title: string; published_on: string; category?: string; status?: string
  changes?: Record<string, [unknown, unknown]>; articles?: number; replace?: boolean
}
export interface ImportDiff {
  added?: LawDiffItem[]; changed?: LawDiffItem[]; matched?: LawDiffItem[]; fulltext?: LawDiffItem[]
  skipped?: { file: number; row: number; reason: string }[]
  unmatched?: { file: string; reason: string }[]
  fulltext_failed?: { title: string; reason: string }[]; duplicates?: number
}
export interface LawImport {
  id: string; kind: string; status: string; added: number; changed: number; unchanged: number; skipped: number
  error: string | null; created_at: string; file_names: string[]; diff?: ImportDiff | null
}
export const lawsApi = {
  list: (query: { q?: string; category?: string; status?: string; page?: number; page_size?: number }) =>
    http.get<{ items: LawRow[]; total: number; counts: Record<string, number> }>('/admin/laws', query),
  update: (id: number, replaced_by: string) => http.patch<LawRow>(`/admin/laws/${id}`, { replaced_by }),
  settings: () => http.get<{ refs_enabled: boolean }>('/admin/settings/laws'),
  saveSettings: (refs_enabled: boolean) => http.put<{ refs_enabled: boolean }>('/admin/settings/laws', { refs_enabled }),
  imports: () => http.get<{ items: LawImport[] }>('/admin/law-imports'),
  detail: (id: string) => http.get<LawImport>(`/admin/law-imports/${id}`),
  apply: (id: string) => http.post<LawImport>(`/admin/law-imports/${id}/apply`),
  discard: (id: string) => http.post<LawImport>(`/admin/law-imports/${id}/discard`),
  upload: (kind: string, status: string, files: File[]) => {
    const form = new FormData()
    form.append('kind', kind)
    if (kind === 'catalog') form.append('status', status)
    files.forEach(file => form.append('files', file, file.name))
    return http.postForm<{ id: string }>('/admin/law-imports', form)
  },
}
