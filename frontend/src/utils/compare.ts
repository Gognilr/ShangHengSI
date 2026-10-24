import type { PreviewNode } from '@/api/preview'
import type { RedactionItemPos, Suspect } from '@/api/redaction'

export interface CompareSeg { text: string; placeholder?: string; category?: string; suspect?: boolean }
export interface CompareRow {
  kind: 'header' | 'pair' | 'grouped' | 'table_note' | 'not_sent' | 'unmatched'
  loc: string | null
  label: string
  left: CompareSeg[][]
  right: CompareSeg[] | null
  hasRedaction: boolean
  hasPendingSuspect: boolean
}

/** 后端位置按 Unicode 码点计；弹窗与对照区共用，不在浏览器重做脱敏。 */
export function splitOriginal(text: string, items: RedactionItemPos[]): CompareSeg[] {
  const chars = Array.from(text)
  const result: CompareSeg[] = []
  let pos = 0
  for (const item of [...items].sort((a, b) => a.char_start - b.char_start)) {
    if (item.char_start < pos || item.char_end <= item.char_start || item.char_end > chars.length) continue
    if (pos < item.char_start) result.push({ text: chars.slice(pos, item.char_start).join('') })
    result.push({ text: chars.slice(item.char_start, item.char_end).join(''), placeholder: item.placeholder, category: item.category })
    pos = item.char_end
  }
  if (pos < chars.length) result.push({ text: chars.slice(pos).join('') })
  return result
}

/** 只识别接口提供的占位符，最长匹配优先；其余字符原样保留。 */
export function splitOutbound(text: string, items: RedactionItemPos[]): CompareSeg[] {
  const known = new Map(items.map(item => [item.placeholder, item.category]))
  known.delete('')
  const result: CompareSeg[] = []
  let pos = 0
  while (pos < text.length) {
    let start = text.length
    let placeholder = ''
    for (const candidate of known.keys()) {
      const index = text.indexOf(candidate, pos)
      if (index >= 0 && (index < start || (index === start && candidate.length > placeholder.length))) {
        start = index
        placeholder = candidate
      }
    }
    if (start > pos) result.push({ text: text.slice(pos, start) })
    if (!placeholder) break
    result.push({ text: placeholder, placeholder, category: known.get(placeholder) })
    pos = start + placeholder.length
  }
  return result
}

export function markPending(segments: CompareSeg[], suspects: Pick<Suspect, 'decision' | 'text'>[]): CompareSeg[] {
  const texts = [...new Set(suspects.filter(s => s.decision === 'pending' && s.text).map(s => s.text))]
  return segments.flatMap(seg => {
    if (seg.placeholder || !texts.length) return [seg]
    const spans: Array<[number, number]> = []
    for (const text of texts) {
      let start = seg.text.indexOf(text)
      while (start >= 0) {
        spans.push([start, start + text.length])
        start = seg.text.indexOf(text, start + text.length)
      }
    }
    spans.sort((a, b) => a[0] - b[0])
    const merged: Array<[number, number]> = []
    for (const span of spans) {
      const prev = merged[merged.length - 1]
      if (prev && span[0] <= prev[1]) prev[1] = Math.max(prev[1], span[1])
      else merged.push([...span])
    }
    const result: CompareSeg[] = []
    let pos = 0
    for (const [start, end] of merged) {
      if (pos < start) result.push({ text: seg.text.slice(pos, start) })
      result.push({ text: seg.text.slice(start, end), suspect: true })
      pos = end
    }
    if (pos < seg.text.length) result.push({ text: seg.text.slice(pos) })
    return result
  })
}

function nodeLabel(node: PreviewNode): string {
  if (node.cell_ref) return `${node.sheet ?? '报价明细'} · ${node.cell_ref}`
  if (node.clause_no) return node.clause_no
  if (node.page) return `第 ${node.page} 页`
  const para = /-P(\d+)$/.exec(node.loc)
  return para ? `第 ${para[1]} 段` : node.loc
}

export function buildCompareRows(outboundText: string, nodes: PreviewNode[], items: RedactionItemPos[], suspects: Suspect[]): CompareRow[] {
  const byLoc = new Map(nodes.map((node, index) => [node.loc, { node, index }]))
  const lines = outboundText ? outboundText.split('\n') : []
  const parsed = lines.map((text, index) => {
    const match = /^\[([^\]\s]+)\] /.exec(text)
    return { loc: match?.[1] ?? null, content: match ? text.slice(match[0].length) : text, header: index === 0 && !text.startsWith('[') }
  })
  const outboundLocs = new Set(parsed.map(line => line.loc).filter((loc): loc is string => loc !== null))
  const used = new Set<string>()
  const rows: Array<{ row: CompareRow; anchor: number | null }> = []
  const original = (node: PreviewNode) => markPending(splitOriginal(node.text ?? '', items.filter(item => item.loc === node.loc)), suspects.filter(s => s.loc === node.loc))
  for (const line of parsed) {
    const exact = line.loc ? byLoc.get(line.loc) : undefined
    const tableNote = !exact && !!line.loc && /^F\d+-(?:S\d+|T\d+|PG\d+-T\d+)$/.test(line.loc)
      && nodes.some(node => node.loc.startsWith(`${line.loc}-`) && outboundLocs.has(node.loc))
    const grouped = !exact && line.loc ? nodes.filter(node => node.loc.startsWith(`${line.loc}-`) && !outboundLocs.has(node.loc) && !used.has(node.loc)) : []
    const paired = exact ? [exact.node] : grouped
    paired.forEach(node => used.add(node.loc))
    const locs = new Set(paired.map(node => node.loc))
    if (line.loc) locs.add(line.loc)
    const rowItems = line.header ? items : items.filter(item => locs.has(item.loc))
    const rowSuspects = suspects.filter(s => locs.has(s.loc))
    const kind = line.header ? 'header' : exact ? 'pair' : tableNote ? 'table_note' : grouped.length ? 'grouped' : 'unmatched'
    let right = markPending(splitOutbound(line.content, rowItems), rowSuspects)
    // 展示分段必须可逆；任何失配都退回后端原行，绝不修饰将发送的内容。
    const invalid = right.map(seg => seg.text).join('') !== line.content
    if (invalid) right = [{ text: line.content }]
    const left = line.header ? [[{ text: '系统生成的签约方说明，原文无对应段落' }]]
      : tableNote ? [[{ text: '系统生成的报价明细表说明，原文无对应段落' }]] : paired.map(original)
    rows.push({
      anchor: exact?.index ?? (grouped.length ? byLoc.get(grouped[0]!.loc)!.index : null),
      row: {
        kind: invalid ? 'unmatched' : kind, loc: line.loc,
        label: line.header ? '主体说明' : tableNote ? '报价明细表说明' : exact ? nodeLabel(exact.node) : line.loc ?? '—',
        left, right,
        hasRedaction: left.some(segs => segs.some(seg => !!seg.placeholder)) || right.some(seg => !!seg.placeholder),
        hasPendingSuspect: rowSuspects.some(s => s.decision === 'pending'),
      },
    })
  }
  nodes.forEach((node, index) => {
    if (used.has(node.loc)) return
    const left = [original(node)]
    const row: CompareRow = { kind: 'not_sent', loc: node.loc, label: nodeLabel(node), left, right: null,
      hasRedaction: left[0]!.some(seg => !!seg.placeholder), hasPendingSuspect: suspects.some(s => s.loc === node.loc && s.decision === 'pending') }
    const before = rows.findIndex(entry => entry.anchor !== null && entry.anchor > index)
    rows.splice(before < 0 ? rows.length : before, 0, { row, anchor: index })
  })
  return rows.map(entry => entry.row)
}

/** NOTE 独立于正文行协议，保留多行原文和服务端 note 的全部字符。 */
export function buildNoteRow(note: string, outboundNote: string | null, items: RedactionItemPos[], suspects: Suspect[]): CompareRow | null {
  if (!note) return null
  const noteItems = items.filter(item => item.loc === 'NOTE')
  const noteSuspects = suspects.filter(item => item.loc === 'NOTE')
  const left = markPending(splitOriginal(note, noteItems), noteSuspects)
  let right = markPending(splitOutbound(outboundNote ?? '', noteItems), noteSuspects)
  const invalid = outboundNote === null || right.map(seg => seg.text).join('') !== outboundNote
  if (invalid) right = [{ text: outboundNote ?? '' }]
  return { kind: invalid ? 'unmatched' : 'pair', loc: 'NOTE', label: '本次补充说明', left: [left], right,
    hasRedaction: [...left, ...right].some(seg => !!seg.placeholder), hasPendingSuspect: noteSuspects.some(s => s.decision === 'pending') }
}
