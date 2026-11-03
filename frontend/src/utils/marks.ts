import type { Calc } from '@/api/chat'
import type { Mark, MarkKind } from '@/api/runs'

/** 回程标记与本机计算的切分（V31-U3 1.2）。下标一律按码点，与后端 Python 字符串下标一致。 */
export interface MarkSeg { text: string; kind?: MarkKind }
/** start 为该段在整段回复中的码点起点，供 sliceMarks 取本段的标记。 */
export type CalcSeg = { text: string; start: number; calc?: undefined } | { text: string; start: number; calc: Calc }

/** 按标记切分文字；越界、空区间或与前一个重叠的标记丢弃。 */
export function splitMarks(text: string, marks: readonly Mark[] | undefined, skip: readonly MarkKind[] = []): MarkSeg[] {
  const chars = Array.from(text)
  const out: MarkSeg[] = []
  let cursor = 0
  for (const mark of [...(marks ?? [])].sort((a, b) => a.start - b.start)) {
    if (skip.includes(mark.kind) || mark.start < cursor || mark.end <= mark.start || mark.end > chars.length) continue
    if (mark.start > cursor) out.push({ text: chars.slice(cursor, mark.start).join('') })
    out.push({ text: chars.slice(mark.start, mark.end).join(''), kind: mark.kind })
    cursor = mark.end
  }
  if (cursor < chars.length || !out.length) out.push({ text: chars.slice(cursor).join('') })
  return out
}

/** 标记对应的原样文字（Markdown 回复按文字匹配标出，U4 2.1）。 */
export function markTokens(text: string, marks: readonly Mark[] | undefined, skip: readonly MarkKind[] = ['calc']): Map<string, MarkKind> {
  const tokens = new Map<string, MarkKind>()
  for (const seg of splitMarks(text, marks, skip)) if (seg.kind && seg.text.trim()) tokens.set(seg.text, seg.kind)
  return tokens
}

/** 不含 calc 的标记数，用于“有 N 处未能还原”。 */
export function unresolvedCount(marks: readonly Mark[] | undefined): number {
  return (marks ?? []).filter(mark => mark.kind !== 'calc').length
}

/** 按 calcs 的行区间把回复切段，算式行交给 CalcResult 就地替换；区间不合法的计算忽略。 */
export function calcLines(text: string, calcs: readonly Calc[] | undefined): CalcSeg[] {
  const chars = Array.from(text)
  const out: CalcSeg[] = []
  let cursor = 0
  for (const calc of [...(calcs ?? [])].sort((a, b) => a.line_start - b.line_start)) {
    if (calc.line_start < cursor || calc.line_end < calc.line_start || calc.line_end > chars.length) continue
    if (calc.line_start > cursor) out.push({ text: chars.slice(cursor, calc.line_start).join(''), start: cursor })
    out.push({ text: chars.slice(calc.line_start, calc.line_end).join(''), start: calc.line_start, calc })
    cursor = calc.line_end
  }
  if (cursor < chars.length || !out.length) out.push({ text: chars.slice(cursor).join(''), start: cursor })
  return out
}

/** 取落在 [start, start + length) 内的标记并改为相对下标（与 calcLines 的段配合）。 */
export function sliceMarks(marks: readonly Mark[] | undefined, start: number, length: number): Mark[] {
  return (marks ?? []).filter(mark => mark.start >= start && mark.end <= start + length)
    .map(mark => ({ ...mark, start: mark.start - start, end: mark.end - start }))
}

/** 问题引用片段在节点原文中的码点区间；片段为空、不存在或出现多次时返回 null（V31-U3 3.3）。 */
export function excerptRange(text: string, excerpt: string | null | undefined): [number, number] | null {
  if (!excerpt) return null
  const at = text.indexOf(excerpt)
  if (at < 0 || text.indexOf(excerpt, at + 1) >= 0) return null
  const start = Array.from(text.slice(0, at)).length
  return [start, start + Array.from(excerpt).length]
}

/**
 * 把码点区间 [start, end) 叠加到已切好的段上：普通段按区间拆开，其余段（胶囊、保留文字）整段打上同一标注。
 * 已有标注的段保持不变（先叠加的优先）。段文字按原文计长度，胶囊的文字就是原文。
 */
export function overlayRange<S extends { text: string; k: string; hl?: H }, H>(segs: S[], start: number, end: number, hl: H): S[] {
  const out: S[] = []
  let pos = 0
  for (const seg of segs) {
    const chars = Array.from(seg.text)
    const from = pos, to = pos + chars.length
    pos = to
    if (seg.hl !== undefined || to <= start || from >= end) { out.push(seg); continue }
    if (seg.k !== 'plain') { out.push({ ...seg, hl }); continue }
    const a = Math.max(start, from) - from, b = Math.min(end, to) - from
    if (a > 0) out.push({ ...seg, text: chars.slice(0, a).join('') })
    out.push({ ...seg, text: chars.slice(a, b).join(''), hl })
    if (b < chars.length) out.push({ ...seg, text: chars.slice(b).join('') })
  }
  return out
}
