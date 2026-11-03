/** 最小 Markdown 子集；结果只用模板文本节点渲染。 */
export interface Inline { text: string; bold: boolean; mark?: string }
export type Block = { kind: 'heading' | 'paragraph' | 'list'; text: string; level: number }
  | { kind: 'table'; headers: string[]; rows: string[][] } | { kind: 'rule' }
export function inline(text: string, tokens: ReadonlySet<string> = new Set()): Inline[] {
  return text.split(/(\*\*[^*]+\*\*)/g).filter(Boolean).flatMap(piece => {
    const bold = piece.startsWith('**'), value = bold ? piece.slice(2, -2) : piece
    const out: Inline[] = []
    const sorted = [...tokens].filter(Boolean).sort((a,b) => b.length - a.length)
    let cursor = 0, plain = 0
    while (cursor < value.length) {
      const token = sorted.find(t => value.startsWith(t, cursor))
      if (!token) { cursor++; continue }
      if (cursor > plain) out.push({ text: value.slice(plain, cursor), bold })
      out.push({ text: token, bold, mark: token }); cursor += token.length; plain = cursor
    }
    if (plain < value.length) out.push({ text: value.slice(plain), bold })
    return out
  })
}
function cells(line: string) { return line.trim().replace(/^\||\|$/g, '').split(/(?<!\\)\|/).map(c => c.trim().replace(/\\\|/g, '|')) }
export function markdown(text: string): Block[] {
  const lines = text.split('\n'), out: Block[] = []
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]!.trim()
    if (!line) continue
    if (line.includes('|') && lines[i + 1] && cells(lines[i + 1]!).every(c => /^:?-{3,}:?$/.test(c))) {
      const headers = cells(line), rows: string[][] = []; i += 2
      while (i < lines.length && lines[i]!.includes('|') && lines[i]!.trim()) { rows.push(cells(lines[i]!)); i++ }
      i--; out.push({ kind: 'table', headers, rows }); continue
    }
    const heading = /^(#{1,3})\s+(.+)$/.exec(line), list = /^(?:- |\d+\. )(.+)$/.exec(line)
    if (heading) out.push({ kind: 'heading', text: heading[2]!, level: heading[1]!.length })
    else if (/^-{3,}$/.test(line)) out.push({ kind: 'rule' })
    else if (list) out.push({ kind: 'list', text: list[1]!, level: 0 })
    else out.push({ kind: 'paragraph', text: line, level: 0 })
  }
  return out
}
