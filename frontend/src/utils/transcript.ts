// 逐字稿的段落合并（V31-U5F-05）：与后端 assistant/meeting.py 的 paragraphs 同一规则。
// 同一发言人、前后两句间隔不足 30 秒的连续句子合成一段；只在显示层合并，脱敏与编辑仍按句。
export const GAP_MS = 30000

interface Sentence { kind?: string; speaker?: number | null; t_start_ms?: number | null; t_end_ms?: number | null }

export function sameParagraph(last: Sentence, item: Sentence) {
  return item.kind === 'utterance' && last.kind === 'utterance' && last.speaker === item.speaker
    && (item.t_start_ms ?? 0) - (last.t_end_ms ?? 0) < GAP_MS
}

export function groupSentences<T extends Sentence>(items: T[]): T[][] {
  const groups: T[][] = []
  for (const item of items) {
    const group = groups[groups.length - 1]
    const last = group?.[group.length - 1]
    if (group && last && sameParagraph(last, item)) group.push(item)
    else groups.push([item])
  }
  return groups
}

export function stamp(ms = 0) {
  const s = Math.floor(ms / 1000)
  return [Math.floor(s / 3600), Math.floor(s / 60) % 60, s % 60].map(v => String(v).padStart(2, '0')).join(':')
}
