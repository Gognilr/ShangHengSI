import type { DiffChange } from './assistant'
import { http } from './http'

export interface ContractChange extends Pick<DiffChange, 'id' | 'type' | 'clause' | 'brief' | 'segs'> {
  baseline_location: string | null
  revised_location: string | null
}

export interface ContractComparison {
  changes: ContractChange[]
  converted: boolean
}

export function compareContracts(baseline: File, revised: File, signal: AbortSignal): Promise<ContractComparison> {
  const form = new FormData()
  form.set('baseline', baseline)
  form.set('revised', revised)
  return http.postForm('/review/contract-compare', form, signal)
}
