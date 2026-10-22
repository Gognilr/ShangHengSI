import { http } from './http'
import type { Source, Stance } from './tasks'

export interface BuiltinRequirement {
  id: string
  dimension: string | null
  text: string
  doc_types: string[]
}

export interface CompanyRequirement {
  code: string
  text: string
  doc_types: string[]
  stances: string[]
}

export interface RequirementsView {
  builtin_version: string
  builtin: BuiltinRequirement[]
  company_version: number | null
  company: CompanyRequirement[]
}

export const requirementsApi = {
  /** docType 为空表示全部类型。 */
  get: (query: { stance?: Stance; source?: Source; docType?: string | null }) =>
    http.get<RequirementsView>('/requirements', {
      stance: query.stance,
      source: query.source,
      doc_type: query.docType ?? undefined,
    }),
}
