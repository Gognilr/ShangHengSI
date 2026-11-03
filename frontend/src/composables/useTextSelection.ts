import type { RedactionItemPos } from '@/api/redaction'

/** 划选结果；start / end 按 Unicode 码点计，与后端 Python 字符串下标一致。 */
export interface TextSelection {
  loc: string
  start: number
  end: number
  text: string
  rect: DOMRect
}

const cpLen = (s: string) => Array.from(s).length

/**
 * 读取页面上当前的划选（V31-U2 4.3）。每个节点的文字放在独立的 .txt 元素中（data-loc），
 * 元素的文字内容与原文逐字相同；选区必须落在同一个 .txt 内，且不与已替换片段重叠、长度 2–100。
 * 返回 null 表示没有选择；notice 为不能处理时的原因。
 */
export function readTextSelection(itemsByLoc: Map<string, RedactionItemPos[]>): { selection?: TextSelection; notice?: string } | null {
  const sel = window.getSelection()
  if (!sel || sel.rangeCount === 0 || sel.isCollapsed) return null
  const range = sel.getRangeAt(0)
  const holder = (n: Node) => (n.nodeType === 1 ? (n as Element) : n.parentElement)?.closest<HTMLElement>('.txt') ?? null
  const a = holder(range.startContainer)
  const b = holder(range.endContainer)
  if (!a && !b) return null
  if (!a || !b || a !== b) return { notice: '请在同一段内选择' }
  const pre = document.createRange()
  pre.selectNodeContents(a)
  pre.setEnd(range.startContainer, range.startOffset)
  const full = a.textContent ?? ''
  const picked = range.toString()
  const start = cpLen(full.slice(0, pre.toString().length))
  const end = start + cpLen(picked)
  const loc = a.dataset.loc ?? ''
  if ((itemsByLoc.get(loc) ?? []).some(it => it.char_start < end && it.char_end > start)) return { notice: '所选文字已被替换' }
  const len = cpLen(picked.trim())
  if (len < 2 || len > 100) return { notice: '请选择 2–100 个字符' }
  return { selection: { loc, start, end, text: picked, rect: range.getBoundingClientRect() } }
}

/** 结果页“追问这段”的划选（V31-U3 3.3）：选区在同一个 .txt 内且非空白；选中文字是真实原文，照常经追问的脱敏与确认。 */
export function readAskSelection(): { loc: string; text: string; rect: DOMRect } | null {
  const sel = window.getSelection()
  if (!sel || sel.rangeCount === 0 || sel.isCollapsed) return null
  const range = sel.getRangeAt(0)
  const holder = (n: Node) => (n.nodeType === 1 ? (n as Element) : n.parentElement)?.closest<HTMLElement>('.txt') ?? null
  const a = holder(range.startContainer)
  if (!a || a !== holder(range.endContainer)) return null
  const text = range.toString().trim()
  return text ? { loc: a.dataset.loc ?? '', text, rect: range.getBoundingClientRect() } : null
}
