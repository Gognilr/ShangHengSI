import type { ComputedRef, InjectionKey, Ref } from 'vue'
import { ElMessageBox } from 'element-plus'
import { bidGroupLabel } from '@/api/bid'
import type { Bidder, BidCell, BidCheck, BidItem, BidLevel, BidPricing, BidReview, BidReviewStatus, BidSigning, BidSignMark } from '@/api/bid'

/** 结果页（V34-B-06）各页签共用：数据、名称、乐观更新的核对函数与“查看原件页”。 */
export interface BidResultsContext {
  projectId: string
  /** 开标日（YYYY-MM-DD，文件时间线的竖线） */
  openingDate: string
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
  /** 批量核对（确认本机计算的 n 条、各家都符合）：一次请求，失败整体回滚 */
  reviewBatch: (items: string[], cells: { requirement_id: string; bidder_id: string }[], status: BidReviewStatus) => Promise<boolean>
  /** 签章页标注；多页时一次请求（本家全部已盖章） */
  markSigning: (pages: BidSigning[], mark: BidSignMark | null) => Promise<void>
  /** “待你处理”清单（按 疑似否决 → 强线索 → 中线索 → 一般问题 → 人工核对 → 签章页） */
  steps: ComputedRef<TodoStep[]>
  /** 进入逐条核对，从 key 这一步开始（缺省为第一条待核） */
  focus: (key?: string) => void
  openPage: (fileId: string, page: number) => void
}
export const bidResultsKey: InjectionKey<BidResultsContext> = Symbol('bidResults')

export const bidTypeLabel: Record<string, string> = {
  mixed: '混装', contact: '联系信息相同', props: '文件属性相同', image: '相同图片', similar: '相似段落', pricing: '报价规律',
  multi_total: '多个总价', sum_total: '合计与总价', ceiling: '超最高限价', low_price: '异常低价', uppercase: '大小写', row_calc: '分项计算', sum_rows: '合计计算',
  validity: '投标有效期', deposit: '保证金', cert_expired: '证书有效期', ai_instruction: '写给 AI 的指令',
  model_response: '模型判断', deviation_conflict: '偏离表与正文',
}
export const pricingTypes = new Set(['multi_total', 'sum_total', 'ceiling', 'low_price', 'uppercase', 'row_calc', 'sum_rows'])
/** 总价旁的“最高”“最低”（V34Q-27：仍按录入顺序排列，不排序） */
export const markText: Record<string, string> = { highest: '最高', lowest: '最低' }
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
/** 未填限价时各页不显示“与限价”列（V34Q-27），这里只在填了限价时调用 */
export function ceilingView(price: BidPricing['bidders'][string] | undefined): CeilingView {
  const over = price?.over_ceiling ?? price?.over_ceiling_max ?? null
  if (over == null) return { tone: 'none', text: '—', diff: null }
  if (over.startsWith('-')) return { tone: 'under', text: '低于限价', diff: over.slice(1) }
  if (over === '0.00') return { tone: 'equal', text: '等于限价', diff: null }
  return { tone: 'over', text: '▲ 超限价', diff: over }
}
/** 总价规律的说明（与后端线索标题同义） */
export function patternStep(t: NonNullable<BidPricing['total_patterns']>[number]) {
  return t.kind === 'diff' ? `从低到高每家相差 ${t.step}` : `从低到高每家约为前一家的 ${t.step} 倍`
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
/** 已排除、不计入核对进度的事项数（进度条第三段）：问题与线索、单元格 */
export function excludedCount(d: BidCheck | undefined, bidder?: string): number {
  if (!d) return 0
  const has = (ids: string[]) => !bidder || ids.includes(bidder)
  return d.items.filter(i => i.kind !== 'normal_dup' && i.review_status === 'excluded' && has(i.bidder_ids)).length
    + d.cells.filter(c => c.review_status === 'excluded' && has([c.bidder_id])).length
}
export function progress(todos: ReviewTodo[], bidder?: string) {
  const list = bidder ? todos.filter(t => t.bidder_ids.includes(bidder)) : todos
  return { done: list.filter(t => t.done).length, total: list.length }
}

/**
 * “待你处理”清单与逐条核对的一步（V34Q-27 第二部分）：与 reviewTodos 同一口径，只是人工核对按要求合并（各家并排），
 * 签章页按投标人合并（本家全部已盖章，只改例外页）。done 为这一步的待核事项都已核。
 */
export type StepGroup = 'veto' | 'strong' | 'medium' | 'general' | 'manual' | 'signing'
export const stepGroupLabel: Record<StepGroup, string> = { veto: '疑似否决', strong: '强线索', medium: '中线索', general: '一般问题', manual: '人工核对', signing: '签章页' }
export type TodoStep =
  | { key: string; group: StepGroup; kind: 'item'; item: BidItem; done: boolean }
  | { key: string; group: StepGroup; kind: 'cell'; cell: BidCell; requirement: BidCheck['requirements'][number]; done: boolean }
  | { key: string; group: StepGroup; kind: 'manual'; requirement: BidCheck['requirements'][number]; bidder_ids: string[]; checked: string[]; done: boolean }
  | { key: string; group: StepGroup; kind: 'signing'; bidder_id: string; pages: BidSigning[]; done: boolean }
const groupOrder: StepGroup[] = ['veto', 'strong', 'medium', 'general', 'manual', 'signing']
export function todoSteps(d: BidCheck | undefined, bidders: Bidder[]): TodoStep[] {
  if (!d) return []
  const out: TodoStep[] = []
  for (const i of d.items) {
    if (i.kind === 'normal_dup' || i.review_status === 'excluded') continue
    const group: StepGroup = i.kind === 'issue' ? (i.level === 'veto' ? 'veto' : 'general') : i.level === 'strong' ? 'strong' : i.level === 'medium' ? 'medium' : 'general'
    out.push({ key: 'i' + i.id, group, kind: 'item', item: i, done: i.review_status === 'verified' })
  }
  const reqs = new Map(d.requirements.map(r => [r.id, r]))
  const cellOf = new Map(d.cells.map(c => [`${c.requirement_id}:${c.bidder_id}`, c]))
  for (const c of d.cells) {
    const r = reqs.get(c.requirement_id)
    if (!r || r.method === 'manual' || c.verdict === 'manual' || c.review_status === 'excluded') continue
    if (c.verdict === 'unclear' || c.cite === 'unverified') out.push({ key: `c${c.requirement_id}:${c.bidder_id}`, group: 'manual', kind: 'cell', cell: c, requirement: r, done: c.review_status === 'verified' })
  }
  for (const r of d.requirements) {
    if (r.method !== 'manual') continue
    const ids = bidders.map(b => b.id).filter(id => cellOf.get(`${r.id}:${id}`)?.review_status !== 'excluded')
    if (!ids.length) continue
    const checked = ids.filter(id => cellOf.get(`${r.id}:${id}`)?.review_status === 'verified')
    out.push({ key: 'm' + r.id, group: 'manual', kind: 'manual', requirement: r, bidder_ids: ids, checked, done: checked.length === ids.length })
  }
  for (const b of bidders) {
    const pages = d.signing.filter(s => s.bidder_id === b.id)
    if (pages.length) out.push({ key: 's' + b.id, group: 'signing', kind: 'signing', bidder_id: b.id, pages, done: pages.every(s => !!s.mark) })
  }
  return out.map((s, i) => [s, i] as const).sort((a, b) => groupOrder.indexOf(a[0].group) - groupOrder.indexOf(b[0].group) || a[1] - b[1]).map(x => x[0])
}
/** 清单行与逐条核对标题的一句话与小字 */
export function stepLine(s: TodoStep, shorts: Record<string, string>): { text: string; meta: string } {
  const who = (ids: string[]) => ids.map(id => shorts[id] ?? '已移除').join(' · ')
  if (s.kind === 'item') return { text: s.item.title, meta: `${bidTypeLabel[s.item.type] ?? s.item.type}${s.item.source === 'model' ? ' · 模型' : ''} · ${who(s.item.bidder_ids)}` }
  if (s.kind === 'cell') return { text: s.requirement.text, meta: `${who([s.cell.bidder_id])} · ${s.cell.verdict === 'unclear' ? '无法判断' : '引文待核实'} · ${bidGroupLabel[s.requirement.group]}` }
  if (s.kind === 'manual') return { text: s.requirement.text, meta: `${bidGroupLabel[s.requirement.group]} · 已核 ${s.checked.length} / ${s.bidder_ids.length} 家` }
  const left = s.pages.filter(p => !p.mark).length
  return { text: `${who([s.bidder_id])}的签章页`, meta: left ? `${left} 页待标注 · 共 ${s.pages.length} 页` : `已全部标注 · 共 ${s.pages.length} 页` }
}
/** 涉及的投标人（清单行与进度用） */
export function stepBidders(s: TodoStep): string[] {
  return s.kind === 'item' ? s.item.bidder_ids : s.kind === 'cell' ? [s.cell.bidder_id] : s.kind === 'manual' ? s.bidder_ids : [s.bidder_id]
}

/** “确认本机计算的 n 条”：本机确定性计算得出、尚待核对的报价类问题（大小写、合计、分项、多个总价、超限价） */
export const calcTypes = new Set(['uppercase', 'sum_total', 'row_calc', 'sum_rows', 'multi_total', 'ceiling'])
export function calcPending(d: BidCheck | undefined): BidItem[] {
  return (d?.items ?? []).filter(i => i.kind === 'issue' && i.source === 'local' && calcTypes.has(i.type) && i.review_status === 'pending')
}

/** 确认本机计算的 n 条：先说明其中的否决项会以肯定语气写入报告，确认后一次请求 */
export async function confirmCalc(ctx: BidResultsContext) {
  const list = calcPending(ctx.data.value)
  if (!list.length) return
  const veto = list.filter(i => i.level === 'veto').length
  try {
    await ElMessageBox.confirm(`把 ${list.length} 条由本机计算得出的报价问题（大小写、合计、分项、多个总价、超限价）标为“已核实”。`
      + (veto ? `其中 ${veto} 条为疑似否决项，报告中将以肯定语气写出。` : '') + '之后仍可逐条改回。', '确认本机计算结果', { confirmButtonText: '全部核实', cancelButtonText: '取消' })
  } catch { return }
  await ctx.reviewBatch(list.map(i => i.id), [], 'verified')
}

/** 投标人表“结论”列：每家一个词（已排除的不算） */
export type Verdict = { text: string; tone: 'd' | 'w' | '' | 'ok' }
export function bidderVerdict(d: BidCheck | undefined, bidder: string): Verdict {
  const own = (d?.items ?? []).filter(i => i.bidder_ids.includes(bidder) && i.review_status !== 'excluded')
  if (own.some(i => i.kind === 'issue' && i.level === 'veto')) return { text: '疑似否决', tone: 'd' }
  if (own.some(i => i.kind === 'clue')) return { text: '有串标线索', tone: 'w' }
  if (own.some(i => i.kind === 'issue')) return { text: '只有一般问题', tone: '' }
  return { text: '未见问题', tone: 'ok' }
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
