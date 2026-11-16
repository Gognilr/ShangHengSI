import type { InjectionKey, Ref } from 'vue'
import type { BidCheck, BidLevel, BidReview } from '@/api/bid'

/** 结果页（V34-B-06）各页签共用：数据、名称、乐观更新的核对函数与“查看原件页”。 */
export interface BidResultsContext {
  projectId: string
  data: Ref<BidCheck | undefined>
  names: Ref<Record<string, string>>
  fileNames: Ref<Record<string, string>>
  /** 可显示页面图的文件（仍保留原件的 PDF 与图片） */
  renderable: Ref<Record<string, boolean>>
  /** 改核对状态或备注：先改本地，请求失败时回滚并提示 */
  reviewItem: (id: string, patch: Partial<BidReview>) => Promise<void>
  reviewCell: (requirementId: string, bidderId: string, patch: Partial<BidReview>) => Promise<void>
  openPage: (fileId: string, page: number) => void
}
export const bidResultsKey: InjectionKey<BidResultsContext> = Symbol('bidResults')

export const bidTypeLabel: Record<string, string> = {
  mixed: '混装', contact: '联系信息相同', props: '文件属性相同', image: '相同图片', similar: '相似段落', pricing: '报价规律',
  multi_total: '多个总价', sum_total: '合计与总价', ceiling: '超最高限价', uppercase: '大小写', row_calc: '分项计算', sum_rows: '合计计算',
  validity: '投标有效期', deposit: '保证金', cert_expired: '证书有效期', ai_instruction: '写给 AI 的指令',
  model_response: '模型判断', deviation_conflict: '偏离表与正文',
}
export const pricingTypes = new Set(['multi_total', 'sum_total', 'ceiling', 'uppercase', 'row_calc', 'sum_rows'])

/** 强 / 中 / 弱只用文字颜色区分（visual.md 7.5） */
export function levelTone(level: BidLevel): 'strong' | 'medium' | 'weak' {
  return level === 'veto' || level === 'strong' ? 'strong' : level === 'general' || level === 'medium' ? 'medium' : 'weak'
}

/** 两段文字中相同的部分（连续 8 字以上）：返回交替的 [文字, 是否相同] 片段，用于并排对照高亮 */
export function sameSpans(text: string, other: string, n = 8): [string, boolean][] {
  if (text.length < n || other.length < n) return [[text, false]]
  const grams = new Set<string>()
  for (let i = 0; i + n <= other.length; i++) grams.add(other.slice(i, i + n))
  const hit = new Array<boolean>(text.length).fill(false)
  for (let i = 0; i + n <= text.length; i++) if (grams.has(text.slice(i, i + n))) hit.fill(true, i, i + n)
  const out: [string, boolean][] = []
  for (let i = 0; i < text.length;) {
    let j = i
    while (j < text.length && hit[j] === hit[i]) j++
    out.push([text.slice(i, j), hit[i]!])
    i = j
  }
  return out
}
