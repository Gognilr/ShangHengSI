import { assistantApi, type DiffChange, type DiffResult, type DiffType } from '@/api/assistant'

/** 多稿对比（V35-D-02）：对比结果本机现算、不存；同一会话、同一对稿只取一次，改了稿次关系换键重取。 */
const cache = new Map<string, Promise<DiffResult>>()
export function loadDiff(sessionId: string, fileId: string, prevId: string | null | undefined): Promise<DiffResult> {
  const key = `${sessionId}:${fileId}:${prevId ?? ''}`
  let promise = cache.get(key)
  if (!promise) {
    promise = assistantApi.diff(sessionId, fileId)
    cache.set(key, promise)
    promise.catch(() => cache.delete(key))
  }
  return promise
}

export const DIFF_TYPE: Record<DiffType, string> = { mod: '修改', add: '新增', del: '删除', move: '位置调整', format: '格式变化' }

/** “改 3 处 · 新增 1 条 · 删除 1 条 · 未留痕 3 处”；格式变化另计 */
export function diffParts(d: DiffResult): { text: string; warn?: boolean }[] {
  const s = d.summary
  const parts: { text: string; warn?: boolean }[] = []
  if (s.mod) parts.push({ text: `改 ${s.mod} 处` })
  if (s.add) parts.push({ text: `新增 ${s.add} 条` })
  if (s.del) parts.push({ text: `删除 ${s.del} 条` })
  if (s.move) parts.push({ text: `挪动 ${s.move} 处` })
  if (s.untracked) parts.push({ text: `未留痕 ${s.untracked} 处`, warn: true })
  return parts
}
export const substantive = (d: DiffResult) => d.changes.filter(c => c.type !== 'format')
export function traceText(c: DiffChange) {
  return c.untracked === null ? '' : c.mixed ? '部分未留痕' : c.untracked ? '未留痕' : '已留痕'
}
