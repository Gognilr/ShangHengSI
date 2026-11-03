import type { Suspect } from '@/api/redaction'

export interface CompareSeg { text: string; placeholder?: string; category?: string; suspect?: boolean }

export function markPending(segments: CompareSeg[], suspects: Pick<Suspect, 'decision' | 'text'>[]): CompareSeg[] {
  const texts = [...new Set(suspects.filter(s => s.decision === 'ignore' && s.text).map(s => s.text))]
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
