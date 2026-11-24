import { http } from './http'

export type DraftKind = 'template' | 'bid'
type DraftStatus = 'pending' | 'approved' | 'returned'
export interface ContentDraft {
  id: string
  text: string
  doc_type: string | null
  stance: string | null
  category: string | null
  created_at: string
}

const paths = {
  template: '/admin/templates/drafts',
  bid: '/admin/bid-requirement-drafts',
}

export const contentDraftsApi = {
  submitFinding: (id: string) => http.post<{ status: DraftStatus }>(`/findings/${id}/template-draft`),
  submitBid: (id: string, rid: string) => http.post<{ status: DraftStatus }>(`/bid/${id}/requirements/${rid}/company-draft`),
  list: (kind: DraftKind) => http.get<{ items: ContentDraft[] }>(paths[kind]),
  decide: (kind: DraftKind, id: string, action: 'approve' | 'return', templateId?: string) =>
    http.post<void>(`${paths[kind]}/${id}/${action}`, templateId ? { template_id: templateId } : undefined),
}

export const draftStatusMessage: Record<DraftStatus, string> = {
  pending: '草稿已提交，等待审批',
  approved: '这条草稿已批准',
  returned: '这条草稿已退回',
}
