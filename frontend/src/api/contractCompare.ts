import type { DiffChange } from './assistant'
import { http } from './http'

export interface ContractLocationPair {
  baseline_location: string | null
  revised_location: string | null
  /** 缝线（V36-F-04）：差异在两稿里的段落位置；新增没有基准稿位置，删除没有修改稿位置 */
  baseline_loc: string | null
  revised_loc: string | null
  segs: DiffChange['segs']
}

export interface ContractChange extends ContractLocationPair, Pick<DiffChange, 'id' | 'type' | 'clause' | 'brief' | 'untracked' | 'mixed'> {
  /** 删除：挂在修改稿哪一段之前；删在文末时挂在最后一段之后（after） */
  anchor: string | null
  after: boolean
  detail_type: DiffChange['detail_type'] | null
  visibility: string | null
  visibility_segs: { s: string; u: boolean }[]
  members: ContractLocationPair[]
}

/** 两稿可对比的文字，只回给上传者本人的浏览器。 */
export interface CompareNode { loc: string; kind: string; part: 'body' | 'header' | 'footer' | 'textbox'; text: string }

export interface ContractComparison {
  changes: ContractChange[]
  baseline: CompareNode[]
  revised: CompareNode[]
  baseline_omitted: string[]
  revised_omitted: string[]
  /** 基准稿位置 → 修改稿位置（未变、修改、位置调整的段落） */
  pairs: Record<string, string>
  converted: boolean
}

function contractForm(baseline: File, revised: File) {
  const form = new FormData()
  form.set('baseline', baseline)
  form.set('revised', revised)
  return form
}

export function compareContracts(baseline: File, revised: File, signal: AbortSignal): Promise<ContractComparison> {
  return http.postForm('/review/contract-compare', contractForm(baseline, revised), signal)
}

export function exportContractComparison(baseline: File, revised: File, signal: AbortSignal): Promise<Blob> {
  return http.postFormBlob('/review/contract-compare/export', contractForm(baseline, revised), signal)
}
