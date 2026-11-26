import type { DiffChange } from './assistant'
import { http } from './http'

export interface ContractChange extends Pick<DiffChange, 'id' | 'type' | 'clause' | 'brief' | 'segs'> {
  baseline_location: string | null
  revised_location: string | null
  /** 缝线（V36-F-04）：差异在两稿里的段落位置；新增没有基准稿位置，删除没有修改稿位置 */
  baseline_loc: string | null
  revised_loc: string | null
  /** 删除：挂在修改稿哪一段之前；删在文末时挂在最后一段之后（after） */
  anchor: string | null
  after: boolean
}

/** 两稿正文的一段（或一个表格单元格），只回给上传者本人的浏览器 */
export interface CompareNode { loc: string; kind: string; text: string }

export interface ContractComparison {
  changes: ContractChange[]
  baseline: CompareNode[]
  revised: CompareNode[]
  /** 基准稿位置 → 修改稿位置（未变、修改、位置调整的段落） */
  pairs: Record<string, string>
  converted: boolean
}

export function compareContracts(baseline: File, revised: File, signal: AbortSignal): Promise<ContractComparison> {
  const form = new FormData()
  form.set('baseline', baseline)
  form.set('revised', revised)
  return http.postForm('/review/contract-compare', form, signal)
}
