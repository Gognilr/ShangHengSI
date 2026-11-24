import { http } from './http'

export type SharedContentKind = 'template' | 'bid'
export interface SharedContent {
  id: string
  text: string
  doc_type: string | null
  stance: string | null
  category: string | null
  shared_at: string
}

const paths = {
  template: '/admin/templates/shared-clauses',
  bid: '/admin/bid-shared-requirements',
}

export const sharedContentApi = {
  finding: (id: string, active: boolean) => change(`/findings/${id}/template-reuse`, active),
  bid: (id: string, rid: string, active: boolean) => change(`/bid/${id}/requirements/${rid}/company-reuse`, active),
  list: (kind: SharedContentKind) => http.get<{ items: SharedContent[] }>(paths[kind]),
  remove: (kind: SharedContentKind, id: string) => http.delete<void>(`${paths[kind]}/${id}`),
}

function change(path: string, active: boolean) {
  return active ? http.post<{ active: boolean }>(path) : http.delete<{ active: boolean }>(path)
}
