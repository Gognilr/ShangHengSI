import type { InjectionKey, Ref } from 'vue'
import { ApiError } from '@/api/http'
import type { Bidder, BidCheck, BidLevel, BidPricing, BidSigning, BidSignMark } from '@/api/bid'

/** 结果页各页签共用（V35-E-01）：数据、名称、“不是问题”、签章页标注与“查看原件页”。 */
export interface BidResultsContext {
  projectId: string
  data: Ref<BidCheck | undefined>
  names: Ref<Record<string, string>>
  /** 简称，悬停显示全称 */
  shorts: Ref<Record<string, string>>
  fileNames: Ref<Record<string, string>>
  /** 可显示页面图的文件（仍保留原件的 PDF 与图片） */
  renderable: Ref<Record<string, boolean>>
  /** “不是问题”：标记或撤销（可撤销） */
  dismiss: (key: string, dismissed: boolean) => Promise<void>
  /** 签章页标注；多页时一次请求（本家全部已盖章） */
  markSigning: (pages: BidSigning[], mark: BidSignMark | null) => Promise<void>
  openPage: (fileId: string, page: number) => void
}
export const bidResultsKey: InjectionKey<BidResultsContext> = Symbol('bidResults')

export const bidTypeLabel: Record<string, string> = {
  mixed: '混装', contact: '联系信息相同', props: '文件属性相同', image: '相同图片', similar: '相似段落', pricing: '报价规律',
  multi_total: '多个总价', sum_total: '合计与总价', low_price: '异常低价', uppercase: '大小写', row_calc: '分项计算', sum_rows: '合计计算', tax_calc: '税价换算',
  cert_expired: '证书有效期', ai_instruction: '写给 AI 的指令', manual_total_diff: '唱标价',
  construction_max: '工期', delivery_max: '交货期', warranty_min: '质保期', defect_min: '缺陷责任期', performance: '类似业绩',
  model_response: '模型补查', deviation_conflict: '偏离表与正文',
}
/** 总价旁的“最高”“最低”（V34Q-27：仍按录入顺序排列，不排序） */
export const markText: Record<string, string> = { highest: '最高', lowest: '最低' }
/** 强 / 中 / 弱只用文字颜色区分（visual.md 7.5） */
export function levelTone(level: BidLevel): 'strong' | 'medium' | 'weak' {
  return level === 'veto' || level === 'strong' ? 'strong' : level === 'general' || level === 'medium' ? 'medium' : 'weak'
}

/** 投标人简称：后端 checks.display_short_names 按同一规则算好（字号 2～4 字，重名时放宽），这里只取用 */
export function shortNames(bidders: Bidder[]): Record<string, string> {
  return Object.fromEntries(bidders.map(b => [b.id, b.short || b.name]))
}

/** 投标总价：唯一时直接显示；多个时显示最高的一个（后端已升序排列），另注“n 个总价” */
export function totalView(price: BidPricing['bidders'][string] | undefined): { text: string | null; multi: string[] } {
  if (!price) return { text: null, multi: [] }
  if (price.total) return { text: price.total, multi: [] }
  const found = price.totals_found
  return found.length > 1 ? { text: found[found.length - 1]!, multi: found } : { text: null, multi: [] }
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

/** 唱标价保存失败的提示：格式不对按规范文案，其余（初审中、原件已删除等）用后端的说明 */
export function openingError(e: unknown, body: { upper?: string | null }) {
  if (e instanceof ApiError && e.status !== 422) return e.message
  return body.upper ? '请检查金额，大写例如 壹佰万元整' : '请输入金额，例如 1234567.89'
}
