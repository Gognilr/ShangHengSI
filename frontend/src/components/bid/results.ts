import type { ComputedRef, InjectionKey, Ref } from 'vue'
import type { Bidder, BidCheck, BidLevel, BidPricing, BidReview } from '@/api/bid'

/** 结果页（V34-B-06）各页签共用：数据、名称、乐观更新的核对函数与“查看原件页”。 */
export interface BidResultsContext {
  projectId: string
  data: Ref<BidCheck | undefined>
  names: Ref<Record<string, string>>
  /** 表头与结论句用的简称（V34Q-25 第 1 条），悬停显示全称 */
  shorts: Ref<Record<string, string>>
  fileNames: Ref<Record<string, string>>
  /** 可显示页面图的文件（仍保留原件的 PDF 与图片） */
  renderable: Ref<Record<string, boolean>>
  /** 待人工核对的事项（唯一算法，V34Q-25 第 3 条）：总览、各投标人、导出共用 */
  todos: ComputedRef<ReviewTodo[]>
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
export const levelText: Record<string, string> = { strong: '强', medium: '中', weak: '弱' }
/** 结论句：每个分句由片段组成，[文字, 色调] 的片段着色（d 危险墨、w 警告墨），其余为正文色 */
export type LedeSeg = string | [string, 'd' | 'w' | 'b']
export type LedePart = LedeSeg[]

/** 强 / 中 / 弱只用文字颜色区分（visual.md 7.5） */
export function levelTone(level: BidLevel): 'strong' | 'medium' | 'weak' {
  return level === 'veto' || level === 'strong' ? 'strong' : level === 'general' || level === 'medium' ? 'medium' : 'weak'
}

/** 投标人简称：去掉公司组织形式与常见行业词，过长取后 4 字；重名时退回更长的写法 */
const orgTail = /(集团)?(股份)?有限(责任)?公司$|(集团)?股份公司$|集团$|公司$/
const tradeTail = /(科技|技术|设备|仪器|工程|建设|建筑|贸易|实业|发展|信息|电子|电气|机电|环保|咨询|服务|智能|系统|网络|软件|医疗|器械|物资|供应链)+$/
export function shortNames(bidders: Bidder[]): Record<string, string> {
  const make = (name: string, keep: number) => {
    let s = name.replace(orgTail, '')
    const local = s.replace(/^(北京|上海|天津|重庆)(市)?|^[^\s]{2,3}?[省市]/, '')
    if (local.length >= 2) s = local
    const core = s.replace(tradeTail, '')
    if (core.length >= 2) s = core
    return s.length > keep ? s.slice(-keep) : s
  }
  const out: Record<string, string> = {}
  for (const keep of [4, 6, 10, 99]) {
    bidders.forEach(b => { out[b.id] = make(b.name, keep) })
    if (new Set(Object.values(out)).size === bidders.length) break
  }
  if (new Set(Object.values(out)).size !== bidders.length) bidders.forEach(b => { out[b.id] = b.name })
  return out
}

/** 与限价（V34Q-25 第 4 条）：金额比较由后端用 Decimal 完成，这里只读差额的符号 */
export interface CeilingView { tone: 'over' | 'under' | 'equal' | 'none'; text: string; diff: string | null }
export function ceilingView(price: BidPricing['bidders'][string] | undefined, hasCeiling: boolean): CeilingView {
  if (!hasCeiling) return { tone: 'none', text: '未填限价', diff: null }
  const over = price?.over_ceiling ?? price?.over_ceiling_max ?? null
  if (over == null) return { tone: 'none', text: '—', diff: null }
  if (over.startsWith('-')) return { tone: 'under', text: '低于限价', diff: over.slice(1) }
  if (over === '0.00') return { tone: 'equal', text: '等于限价', diff: null }
  return { tone: 'over', text: '▲ 超限价', diff: over }
}
/** 投标总价：唯一时直接显示；多个时显示最高的一个（后端已升序排列），另注“n 个总价” */
export function totalView(price: BidPricing['bidders'][string] | undefined): { text: string | null; multi: string[] } {
  if (!price) return { text: null, multi: [] }
  if (price.total) return { text: price.total, multi: [] }
  const found = price.totals_found
  return found.length > 1 ? { text: found[found.length - 1]!, multi: found } : { text: null, multi: [] }
}
/** 比值 "1.0300" → "1.03"（只做显示，不参与计算） */
export function trimRatio(v?: string) { return v ? v.replace(/(\.\d*?)0+$/, '$1').replace(/\.$/, '') : '' }

/**
 * 待人工核对的事项：问题与线索、无法判断或引文待核实的单元格、人工核对行（每家一项）、签章页。
 * 已排除的一律不计（V34Q-25 第 3 条）；done 为已核实或已标注。各处的数字都由这一份清单得出。
 */
export interface ReviewTodo { key: string; kind: 'item' | 'cell' | 'manual' | 'signing'; bidder_ids: string[]; done: boolean }
export function reviewTodos(d: BidCheck | undefined, bidders: Bidder[]): ReviewTodo[] {
  if (!d) return []
  const out: ReviewTodo[] = []
  for (const i of d.items) {
    if (i.kind === 'normal_dup' || i.review_status === 'excluded') continue
    out.push({ key: 'i' + i.id, kind: 'item', bidder_ids: i.bidder_ids, done: i.review_status === 'verified' })
  }
  const manual = new Set(d.requirements.filter(r => r.method === 'manual').map(r => r.id))
  const cellOf = new Map(d.cells.map(c => [`${c.requirement_id}:${c.bidder_id}`, c]))
  for (const c of d.cells) {
    if (manual.has(c.requirement_id) || c.verdict === 'manual' || c.review_status === 'excluded') continue
    if (c.verdict === 'unclear' || c.cite === 'unverified') out.push({ key: `c${c.requirement_id}:${c.bidder_id}`, kind: 'cell', bidder_ids: [c.bidder_id], done: c.review_status === 'verified' })
  }
  for (const rid of manual) for (const b of bidders) {
    const c = cellOf.get(`${rid}:${b.id}`)
    if (c?.review_status === 'excluded') continue
    out.push({ key: `m${rid}:${b.id}`, kind: 'manual', bidder_ids: [b.id], done: c?.review_status === 'verified' })
  }
  for (const s of d.signing) out.push({ key: `s${s.file_id}:${s.page ?? s.loc}`, kind: 'signing', bidder_ids: [s.bidder_id], done: !!s.mark })
  return out
}
export function progress(todos: ReviewTodo[], bidder?: string) {
  const list = bidder ? todos.filter(t => t.bidder_ids.includes(bidder)) : todos
  return { done: list.filter(t => t.done).length, total: list.length }
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
