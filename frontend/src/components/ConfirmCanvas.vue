<script setup lang="ts">
import { ElMessageBox } from 'element-plus'
import { computed, h, nextTick, reactive, ref, watch, type FunctionalComponent, type VNode } from 'vue'
import { ApiError } from '@/api/http'
import { previewApi, type PreviewNode, type PreviewNodes } from '@/api/preview'
import type { ManualInput, RedactionItemPos, Suspect, SuspectCategory, UserCategory } from '@/api/redaction'
import type { TaskFile } from '@/api/tasks'
import DocPreview from '@/components/DocPreview.vue'
import DocSpectrum, { type SpectrumTick } from '@/components/result/DocSpectrum.vue'
import SiSegmented from '@/components/ui/SiSegmented.vue'
import { readAskSelection, readTextSelection, type TextSelection } from '@/composables/useTextSelection'
import { MANUAL_CATEGORY_OPTIONS, REDACTION_CATEGORY, SEVERITY_ORDER, toOptions } from '@/utils/labels'
import { excerptRange, overlayRange } from '@/utils/marks'
import { useFx } from '@/utils/motion'
import { sameParagraph } from '@/utils/transcript'

/**
 * 单画布“原文 / AI 视野”（V31-U2 第四节）：一张纸面显示当前文件的原文，替换处为胶囊；
 * AI 视野显示模型将看到的代号。所有原文与代号都按纯文本渲染。
 * mode=result（V31-U3 3.3）：只读；纸面上标出问题与本地校验位置，右缘光谱条，划选只能“追问这段”，可切到“原件”。
 */
/** 纸面上的一条问题标注：挂在第一个 loc 的节点上。 */
export interface CanvasAnnotation { id: string; no: string; severity: string; loc: string; excerpt: string | null }
const props = defineProps<{
  taskId: string
  files: TaskFile[]
  items: RedactionItemPos[]
  suspects: Suspect[]
  note: string | null
  selectedSuspectId: number | null
  locked: boolean
  protectedCount: number
  nodeLoader?: (taskId: string, fileId: string) => Promise<PreviewNodes>
  mode?: 'confirm' | 'result'
  annotations?: CanvasAnnotation[]
  localLocs?: string[]
  selectedFindingId?: string
  aiView?: boolean
  askEnabled?: boolean
  rawHighlight?: string[]
}>()
const isResult = computed(() => props.mode === 'result')
// 手动补充与撤销只发事件，由确认工作台乐观更新并放进保存队列（V31-U5F-03）
const emit = defineEmits<{
  manual: [input: ManualInput]
  revoke: [item: RedactionItemPos]
  keep: [s: Suspect]
  restore: [s: Suspect]
  replace: [s: Suspect, category?: SuspectCategory]
  select: [findingId: string]
  ask: [prefill: string]
}>()

const fx = useFx()
const categoryOptions = toOptions(MANUAL_CATEGORY_OPTIONS)
const FOLD_LIMIT = 800

// ---- 文件页签与节点 ----
interface FileState { loading: boolean; error: string; available: boolean; nodes: PreviewNode[] }
const states = reactive<Record<string, FileState>>({})
const sortedFiles = computed(() => [...props.files].sort((a, b) => Number(b.is_main) - Number(a.is_main) || a.seq - b.seq))
const activeId = ref('')
const active = computed(() => states[activeId.value])
const lastFileId = computed(() => sortedFiles.value[sortedFiles.value.length - 1]?.id ?? '')
const view = ref<'orig' | 'ai' | 'raw'>('orig')
function utteranceLabel(node: PreviewNode) {
  const file = activeFile.value as (TaskFile & { speakers?: Record<string, string | null> }) | undefined
  return file?.speakers?.[String(node.speaker)] || '发言人 ' + node.speaker
}
function stamp(ms = 0) { const s = Math.floor(ms/1000); return [Math.floor(s/3600), Math.floor(s/60)%60,s%60].map(v => String(v).padStart(2,'0')).join(':') }
const activeFile = computed(() => sortedFiles.value.find(f => f.id === activeId.value))
// 原件视图：PDF 或图片且原件未删除
const rawAvailable = (f: TaskFile | undefined) => !!f && !f.original_deleted_at && ['pdf', 'jpg', 'jpeg', 'png'].includes(f.ext.toLowerCase().replace(/^\./, ''))
const viewOptions = computed(() => [
  { value: 'orig' as const, label: '原文' },
  ...(props.aiView === false ? [] : [{ value: 'ai' as const, label: 'AI 视野' }]),
  ...(isResult.value && rawAvailable(activeFile.value) ? [{ value: 'raw' as const, label: '原件' }] : []),
])
watch(viewOptions, opts => { if (!opts.some(o => o.value === view.value)) view.value = 'orig' })

async function loadFile(id: string) {
  if (!id || states[id]) return
  const st = reactive<FileState>({ loading: true, error: '', available: true, nodes: [] })
  states[id] = st
  try {
    const res = await (props.nodeLoader ?? previewApi.nodes)(props.taskId, id)
    st.available = res.text_available
    st.nodes = res.items.filter(n => n.text)
  } catch (e) {
    st.error = e instanceof ApiError ? e.message : '加载失败，请重试'
  } finally { st.loading = false }
}
// 文件增删或主文件变化后重新读取节点
watch(() => sortedFiles.value.map(f => f.id).join(','), () => {
  for (const key of Object.keys(states)) delete states[key]
  if (!sortedFiles.value.some(f => f.id === activeId.value)) activeId.value = sortedFiles.value[0]?.id ?? ''
  void loadFile(activeId.value)
}, { immediate: true })

const replaceCount = (f: TaskFile) => props.items.filter(it => it.loc.startsWith(`F${f.seq}-`)).length

// ---- 分段：替换项、不确定项、保留文字 ----
type Kind = 'plain' | 'cap' | 'unc' | 'kept'
interface Hl { fid: string; sev: string }
interface Seg { text: string; k: Kind; item?: RedactionItemPos; sid?: number; i: number; hl?: Hl }
/** bar：没能标出片段的问题按最高等级画左侧竖条；tags：挂在本节点的问题。 */
interface NodeView { node: PreviewNode; segs: Seg[]; changed: boolean; bar?: string; local?: boolean; tags?: CanvasAnnotation[] }
type Block =
  | { t: 'node'; nv: NodeView }
  | { t: 'para'; nvs: NodeView[] }
  | { t: 'table'; key: string; rows: { r: number; cells: NodeView[] }[] }
  | { t: 'fold'; id: string; n: number }

const itemsByLoc = computed(() => {
  const m = new Map<string, RedactionItemPos[]>()
  for (const it of props.items) {
    const list = m.get(it.loc) ?? []
    list.push(it)
    m.set(it.loc, list)
  }
  for (const list of m.values()) list.sort((a, b) => a.char_start - b.char_start)
  return m
})
const isKept = (s: Suspect) => s.decision === 'ignore' || !!s.kept_by
const keptTexts = computed(() => new Map(props.suspects.filter(s => isKept(s) && s.text).map(s => [s.text, s.id])))
const suspectByPlaceholder = computed(() => new Map(props.suspects.filter(s => s.placeholder).map(s => [s.placeholder!, s])))

function splitKept(text: string, counter: { n: number }): Seg[] {
  const spans: Array<[number, number, number]> = []
  for (const [t, sid] of keptTexts.value) {
    let at = text.indexOf(t)
    while (at >= 0) { spans.push([at, at + t.length, sid]); at = text.indexOf(t, at + t.length) }
  }
  if (!spans.length) return [{ text, k: 'plain', i: 0 }]
  spans.sort((a, b) => a[0] - b[0] || b[1] - a[1])
  const out: Seg[] = []
  let pos = 0
  for (const [s, e, sid] of spans) {
    if (s < pos) continue
    if (pos < s) out.push({ text: text.slice(pos, s), k: 'plain', i: 0 })
    out.push({ text: text.slice(s, e), k: 'kept', sid, i: counter.n++ })
    pos = e
  }
  if (pos < text.length) out.push({ text: text.slice(pos), k: 'plain', i: 0 })
  return out
}

function buildSegs(loc: string, text: string, counter: { n: number }): Seg[] {
  const chars = Array.from(text)
  const out: Seg[] = []
  let pos = 0
  const push = (to: number) => { if (to > pos) out.push(...splitKept(chars.slice(pos, to).join(''), counter)) }
  for (const item of itemsByLoc.value.get(loc) ?? []) {
    if (item.char_start < pos || item.char_end <= item.char_start || item.char_end > chars.length) continue
    push(item.char_start)
    out.push({ text: chars.slice(item.char_start, item.char_end).join(''), k: item.detected_by === 'uncertain' ? 'unc' : 'cap', item, i: counter.n++ })
    pos = item.char_end
  }
  push(chars.length)
  return out
}

const TABLE_RE = /^(.*-T\d+)-R(\d+)-C(\d+)$/
function cellPos(n: PreviewNode): { key: string; r: number; c: number } | null {
  const m = TABLE_RE.exec(n.loc)
  if (m) return { key: m[1]!, r: Number(m[2]), c: Number(m[3]) }
  const ref = n.sheet && n.cell_ref ? /^([A-Z]+)(\d+)$/.exec(n.cell_ref) : null
  if (ref) {
    const col = Array.from(ref[1]!).reduce((sum, ch) => sum * 26 + ch.charCodeAt(0) - 64, 0)
    return { key: `S:${n.loc.split('-').slice(0, 2).join('-')}:${n.sheet}`, r: Number(ref[2]), c: col }
  }
  return null
}

const unfolded = ref(new Set<string>())
const annByLoc = computed(() => {
  const m = new Map<string, CanvasAnnotation[]>()
  for (const a of props.annotations ?? []) m.set(a.loc, [...(m.get(a.loc) ?? []), a])
  for (const list of m.values()) list.sort((a, b) => (SEVERITY_ORDER[a.severity] ?? 9) - (SEVERITY_ORDER[b.severity] ?? 9))
  return m
})
const localSet = computed(() => new Set(props.localLocs ?? []))
function annotate(nv: NodeView): NodeView {
  const anns = annByLoc.value.get(nv.node.loc)
  if (localSet.value.has(nv.node.loc)) nv.local = true
  if (!anns) return nv
  nv.tags = anns
  for (const a of anns) {
    const range = excerptRange(nv.node.text ?? '', a.excerpt)
    if (range) nv.segs = overlayRange(nv.segs, range[0], range[1], { fid: a.id, sev: a.severity })
    else nv.bar ??= a.severity
  }
  return nv
}
const noteNode = computed<PreviewNode | null>(() => props.note
  ? { loc: 'NOTE', kind: 'note', part: 'body', text: props.note, clause_no: null, page: null, bbox: null, sheet: null, cell_ref: null, ocr_conf: null, parent_loc: null } : null)

const blocks = computed<Block[]>(() => {
  const st = active.value
  if (!st || !st.available) return []
  const counter = { n: 0 }
  const nodes = activeId.value === lastFileId.value && noteNode.value ? [...st.nodes, noteNode.value] : st.nodes
  const flat: Block[] = []
  for (const node of nodes) {
    const segs = buildSegs(node.loc, node.text ?? '', counter)
    const base: NodeView = { node, segs, changed: segs.some(s => s.k !== 'plain') }
    const nv = isResult.value ? annotate(base) : base
    const pos = cellPos(node)
    const last = flat[flat.length - 1]
    if (pos) {
      if (last?.t === 'table' && last.key === pos.key) {
        const row = last.rows.find(r => r.r === pos.r)
        if (row) row.cells.push(nv)
        else last.rows.push({ r: pos.r, cells: [nv] })
      } else flat.push({ t: 'table', key: pos.key, rows: [{ r: pos.r, cells: [nv] }] })
    } else if (node.kind === 'utterance') {
      // 逐字稿按合并稿显示（V31-U5F-05）：同一发言人间隔不足 30 秒的句子并成一段，脱敏仍按句
      if (last?.t === 'para' && sameParagraph(last.nvs[last.nvs.length - 1]!.node, node)) last.nvs.push(nv)
      else flat.push({ t: 'para', nvs: [nv] })
    } else flat.push({ t: 'node', nv })
  }
  if (nodes.length <= FOLD_LIMIT || unfolded.value.has(activeId.value)) return flat
  // 长文档：只显示有替换或保留文字的块，其余折叠
  // 结果页另外保留有问题标注或本地校验位置的块
  const keep = (nv: NodeView) => nv.changed || !!nv.tags || !!nv.local
  const changed = (b: Block) => b.t === 'node' ? keep(b.nv) : b.t === 'para' ? b.nvs.some(keep) : b.t === 'table' ? b.rows.some(r => r.cells.some(keep)) : false
  const out: Block[] = []
  let run = 0
  let runId = ''
  const close = () => { if (run) { out.push({ t: 'fold', id: runId, n: run }); run = 0 } }
  for (const b of flat) {
    if (changed(b)) { close(); out.push(b) } else {
      if (!run) runId = b.t === 'node' ? b.nv.node.loc : b.t === 'para' ? b.nvs[0]!.node.loc : b.t === 'table' ? b.key : ''
      run += b.t === 'table' ? b.rows.reduce((s, r) => s + r.cells.length, 0) : b.t === 'para' ? b.nvs.length : 1
    }
  }
  close()
  return out
})

// ---- 渲染：原文 / AI 视野 ----
const selectedPlaceholder = computed(() => props.suspects.find(s => s.id === props.selectedSuspectId)?.placeholder ?? null)
const NodeText: FunctionalComponent<{ nv: NodeView; ai: boolean; selId: number | null; selPh: string | null; selFid?: string }> = ({ nv, ai, selId, selPh, selFid }) => {
  const kids: Array<VNode | string> = nv.segs.map((g) => {
    const ann = g.hl && !ai ? ['ann', `ann-${g.hl.sev}`, { on: g.hl.fid === selFid }] : []
    if (g.k === 'plain') return ann.length ? h('mark', { class: ann, 'data-fid': g.hl!.fid }, g.text) : g.text
    const selected = g.k === 'kept' ? g.sid === selId : !!selPh && g.item?.placeholder === selPh
    const attrs: Record<string, unknown> = { 'data-seg': g.k, style: { '--i': Math.min(g.i, 40) }, class: [g.k, { sel: selected }, ...ann] }
    if (ann.length) attrs['data-fid'] = g.hl!.fid
    if (g.item) attrs['data-item'] = `${g.item.loc}|${g.item.char_start}|${g.item.char_end}`
    if (g.sid !== undefined) attrs['data-sid'] = g.sid
    if (g.k === 'kept') return h('span', attrs, g.text)
    if (ai) { attrs.class = [g.k === 'unc' ? 'code unc' : 'code', { sel: selected }]; attrs['data-orig'] = g.text; return h('span', attrs, g.item!.placeholder) }
    return h('span', attrs, g.text)
  })
  return h('span', { class: ai ? 'ai-txt' : 'txt', 'data-loc': nv.node.loc }, kids)
}
NodeText.props = ['nv', 'ai', 'selId', 'selPh', 'selFid']
const nodeClass = (nv: NodeView) => [nv.bar ? `ann-bar ann-bar-${nv.bar}` : '', { 'local-bar': nv.local && !nv.bar, 'ann-hit': nv.tags?.some(a => a.id === props.selectedFindingId) }]
const tagText = (tags: CanvasAnnotation[]) => `问题 ${tags.map(a => a.no).join(' · ')}`

const flipping = ref(false)
let flipTimer: ReturnType<typeof setTimeout> | undefined
watch(view, () => {
  closePop()
  clearPick()
  if (fx.value === 'off') return
  flipping.value = true
  clearTimeout(flipTimer)
  flipTimer = setTimeout(() => { flipping.value = false }, fx.value === 'full' ? 700 : 200)
})

// ---- 点击胶囊 ----
interface Pop { left: number; top: number; seg: 'cap' | 'unc' | 'kept'; item?: RedactionItemPos; suspect?: Suspect }
const wrap = ref<HTMLElement>()
const scroller = ref<HTMLElement>()
const pop = ref<Pop | null>(null)
const closePop = () => { pop.value = null }
function onPaperClick(event: MouseEvent) {
  if (window.getSelection() && !window.getSelection()!.isCollapsed) return
  const fidEl = isResult.value ? (event.target as HTMLElement).closest<HTMLElement>('[data-fid]') : null
  if (fidEl && !(event.target as HTMLElement).closest('[data-seg]')) { closePop(); emit('select', fidEl.dataset.fid!); return }
  const el = (event.target as HTMLElement).closest<HTMLElement>('[data-seg]')
  if (!el || !wrap.value) { closePop(); return }
  const seg = el.dataset.seg as Pop['seg']
  const box = el.getBoundingClientRect()
  const outer = wrap.value.getBoundingClientRect()
  const base = { left: Math.max(8, Math.min(box.left - outer.left, outer.width - 276)), top: box.bottom - outer.top + 4, seg }
  if (seg === 'kept') {
    pop.value = { ...base, suspect: props.suspects.find(s => s.id === Number(el.dataset.sid)) }
    return
  }
  const [loc, s, e] = (el.dataset.item ?? '').split('|')
  const item = props.items.find(it => it.loc === loc && it.char_start === Number(s) && it.char_end === Number(e))
  pop.value = { ...base, item, suspect: item ? suspectByPlaceholder.value.get(item.placeholder) : undefined }
}
function popKeep() { const s = pop.value?.suspect; closePop(); if (s) emit('keep', s) }
function popRestore() { const s = pop.value?.suspect; closePop(); if (s) emit('restore', s) }
function popReplace(c: UserCategory) { const s = pop.value?.suspect; closePop(); if (s) emit('replace', s, c) }
async function popRevoke() {
  const item = pop.value?.item
  closePop()
  if (!item) return
  try {
    await ElMessageBox.confirm('撤销后，同一文字将在全文恢复原样。确认它不是敏感信息后再撤销。', '撤销误识别', { type: 'warning', confirmButtonText: '撤销', cancelButtonText: '取消' })
  } catch { return }
  emit('revoke', item)
}

// ---- 划选补充脱敏 ----
const pick = ref<TextSelection | null>(null)
const pickPos = ref({ left: 0, top: 0 })
const pickNotice = ref('')
const pickCategory = ref<UserCategory>('ORG')
function clearPick() { pick.value = null; pickNotice.value = '' }
watch(activeId, id => { clearPick(); closePop(); void loadFile(id) }, { immediate: true })
// 结果页“追问这段”（V31-U3 3.3）：预填“关于“{选中文字}”（{可读位置}）：”
const ask = ref<{ loc: string; text: string } | null>(null)
function readableLoc(loc: string): string {
  const file = sortedFiles.value.find(f => loc.startsWith(`F${f.seq}-`))
  const node = active.value?.nodes.find(n => n.loc === loc)
  const where = node?.clause_no ? `第 ${node.clause_no} 条` : node?.page != null ? `第 ${node.page} 页` : node?.cell_ref ? `${node.sheet ?? ''} ${node.cell_ref}`.trim() : ''
  return [file?.original_name, where].filter(Boolean).join(' ')
}
function onAskMouseUp() {
  ask.value = null
  if (view.value !== 'orig' || !props.askEnabled || !wrap.value) return
  const s = readAskSelection()
  if (!s) return
  const outer = wrap.value.getBoundingClientRect()
  ask.value = { loc: s.loc, text: s.text }
  pickPos.value = { left: Math.max(8, Math.min(s.rect.left - outer.left, outer.width - 160)), top: Math.max(4, s.rect.top - outer.top - 44) }
}
function submitAsk() {
  const a = ask.value
  if (!a) return
  const chars = Array.from(a.text)
  const quoted = chars.length > 60 ? `${chars.slice(0, 60).join('')}…` : a.text
  const where = readableLoc(a.loc)
  ask.value = null
  window.getSelection()?.removeAllRanges()
  emit('ask', `关于“${quoted}”${where ? `（${where}）` : ''}：`)
}
function onPaperMouseUp() {
  if (isResult.value) { onAskMouseUp(); return }
  if (view.value !== 'orig') return
  const result = readTextSelection(itemsByLoc.value)
  clearPick()
  if (!result || !wrap.value) return
  const outer = wrap.value.getBoundingClientRect()
  if (result.notice) { pickNotice.value = result.notice; pickPos.value = { left: 12, top: 8 }; return }
  const s = result.selection!
  pick.value = s
  pickPos.value = { left: Math.max(8, Math.min(s.rect.left - outer.left, outer.width - 420)), top: Math.max(4, s.rect.top - outer.top - 48) }
}
function submitPick() {
  const s = pick.value
  if (!s || props.locked) return
  window.getSelection()?.removeAllRanges()
  clearPick()
  emit('manual', { loc: s.loc, char_start: s.start, char_end: s.end, category: pickCategory.value })
}

// ---- 定位（4.4） ----
async function locate(loc: string, text?: string, findingId?: string) {
  const file = loc === 'NOTE' ? sortedFiles.value[sortedFiles.value.length - 1] : sortedFiles.value.find(f => loc.startsWith(`F${f.seq}-`))
  if (!file) return
  // 原件视图下只切页签，由原件视图按 bbox 高亮
  if (view.value === 'raw' && rawAvailable(file)) { activeId.value = file.id; return }
  if (view.value !== 'orig') view.value = 'orig'
  activeId.value = file.id
  await loadFile(file.id)
  unfolded.value = new Set(unfolded.value).add(file.id)
  await nextTick()
  const box = scroller.value
  const node = box?.querySelector<HTMLElement>(`[data-node="${CSS.escape(loc)}"]`)
  if (!box || !node) return
  box.scrollTop += node.getBoundingClientRect().top - box.getBoundingClientRect().top - 80
  const marked = findingId ? node.querySelector<HTMLElement>(`[data-fid="${CSS.escape(findingId)}"]:not(.ann-tag)`) : null
  const candidates = Array.from(node.querySelectorAll<HTMLElement>('[data-seg]'))
  const target = marked ?? (findingId ? node : undefined) ?? (text ? candidates.find(el => el.textContent === text) : undefined) ?? candidates[0] ?? node
  target.classList.add('flash')
  setTimeout(() => target.classList.remove('flash'), 1600)
}
function switchFile(delta: 1 | -1) {
  const list = sortedFiles.value
  const index = list.findIndex(f => f.id === activeId.value)
  if (list.length > 1 && index >= 0) activeId.value = list[(index + delta + list.length) % list.length]!.id
}
function toggleView() { view.value = view.value === 'orig' ? 'ai' : 'orig' }
function cancel() { closePop(); clearPick(); ask.value = null; window.getSelection()?.removeAllRanges() }

// ---- 光谱条（结果页）：刻度位置 = 块序号 ÷ 当前渲染的块数 ----
const ticks = computed<SpectrumTick[]>(() => {
  if (!isResult.value) return []
  const list = blocks.value
  const out: SpectrumTick[] = []
  list.forEach((b, i) => {
    const pos = (i + 0.5) / list.length
    const views = b.t === 'node' ? [b.nv] : b.t === 'para' ? b.nvs : b.t === 'table' ? b.rows.flatMap(r => r.cells) : []
    let cap = false
    for (const nv of views) {
      for (const a of nv.tags ?? []) out.push({ pos, kind: (['high', 'medium', 'low'].includes(a.severity) ? a.severity : 'low') as SpectrumTick['kind'], loc: nv.node.loc, fid: a.id })
      if (nv.local) out.push({ pos, kind: 'local', loc: nv.node.loc })
      if (!cap && nv.changed) { cap = true; out.push({ pos, kind: 'cap', loc: nv.node.loc }) }
    }
  })
  return out
})
function onTick(t: SpectrumTick) {
  void locate(t.loc, undefined, t.fid)
  if (t.fid) emit('select', t.fid)
}
function onSeek(ratio: number) {
  const box = scroller.value
  if (box) box.scrollTop = ratio * box.scrollHeight - box.clientHeight / 2
}
function onStageScroll() { closePop(); ask.value = null; tip.value = null }

// ---- 悬停胶囊（V31-U5F-03 问题 8）：AI 视野悬停代号显示“原文：…”，原文视图悬停显示代号；只在本机浏览器显示 ----
interface Tip { left: number; top: number; label: string; text: string; ai: boolean }
const tip = ref<Tip | null>(null)
function onPaperOver(event: MouseEvent) {
  const el = (event.target as HTMLElement).closest<HTMLElement>('[data-item]')
  if (!el || !wrap.value || pop.value) { tip.value = null; return }
  const [loc, s, e] = el.dataset.item!.split('|')
  const item = props.items.find(it => it.loc === loc && it.char_start === Number(s) && it.char_end === Number(e))
  if (!item) { tip.value = null; return }
  const box = el.getBoundingClientRect()
  const outer = wrap.value.getBoundingClientRect()
  const ai = view.value === 'ai'
  const above = box.top - outer.top - 34
  tip.value = {
    left: Math.max(8, Math.min(box.left - outer.left, outer.width - 320)),
    top: above >= 0 ? above : box.bottom - outer.top + 6,
    ai,
    label: ai ? '原文' : item.detected_by === 'uncertain' ? '不确定是否敏感，已默认发送为' : `${REDACTION_CATEGORY[item.category] ?? item.category} · 发送为`,
    text: ai ? (el.dataset.orig ?? '') : item.placeholder,
  }
}
defineExpose({ locate, switchFile, toggleView, cancel })
</script>

<template>
  <div ref="wrap" class="cv">
    <div class="cv-bar">
      <div v-if="sortedFiles.length > 1" class="tabs" role="tablist">
        <button v-for="f in sortedFiles" :key="f.id" type="button" role="tab" class="tab" :class="{ on: f.id === activeId }" :aria-selected="f.id === activeId" :title="f.original_name" @click="activeId = f.id">
          <span class="tab-name">{{ f.original_name }}</span><span class="tab-n">{{ replaceCount(f) }}</span>
        </button>
      </div>
      <span v-else class="spacer" />
      <SiSegmented v-model="view" :options="viewOptions" size="small" ariaLabel="原文或 AI 视野" />
      <div v-if="$slots.bar" class="cv-extra"><slot name="bar" /></div>
    </div>

    <DocPreview v-if="view === 'raw' && activeFile" :task-id="taskId" :file="activeFile" :highlight="rawHighlight ?? []" />
    <div v-else class="stage-wrap">
    <div ref="scroller" v-loading="active?.loading" class="stage si-stage" :class="{ 'with-spectrum': isResult }" @scroll.passive="onStageScroll">
      <el-alert v-if="active?.error" type="error" show-icon :closable="false" :title="active.error" />
      <el-empty v-else-if="active && !active.loading && !active.available" description="原文已按保留期清理" />
      <el-empty v-else-if="active && !active.loading && !blocks.length" description="没有可显示的正文" />
      <div v-else class="si-paper paper" :class="[{ flipping }, `fx-${fx}`, { ai: view === 'ai' }]" :key="view === 'ai' && fx === 'lite' ? 'a' : 'o'" @click="onPaperClick" @mouseup="onPaperMouseUp" @mouseover="onPaperOver" @mouseleave="tip = null">
        <template v-for="(b, bi) in blocks" :key="bi">
          <div v-if="b.t === 'node'" class="node" :class="[`kind-${b.nv.node.kind}`, { head: b.nv.node.kind === 'heading', note: b.nv.node.loc === 'NOTE', side: b.nv.node.part !== 'body' }, ...nodeClass(b.nv)]" :data-node="b.nv.node.loc">
            <div v-if="b.nv.node.kind === 'utterance'" class="utterance-meta"><span :class="{ code: view === 'ai' && b.nv.node.speaker_code?.startsWith('[[') }">{{ view === 'ai' ? b.nv.node.speaker_code : utteranceLabel(b.nv.node) }}</span> · {{ stamp(b.nv.node.t_start_ms ?? 0) }}</div>
            <button v-if="b.nv.tags" type="button" class="ann-tag" :data-fid="b.nv.tags[0]!.id">{{ tagText(b.nv.tags) }}</button>
            <div v-if="b.nv.node.loc === 'NOTE'" class="side-label">本次补充说明</div>
            <span v-else-if="b.nv.node.part !== 'body'" class="side-label">{{ b.nv.node.part }}</span>
            <span v-if="b.nv.node.clause_no && b.nv.node.part === 'body'" class="clause">{{ b.nv.node.clause_no }}</span>
            <NodeText :nv="b.nv" :ai="view === 'ai'" :sel-id="selectedSuspectId" :sel-ph="selectedPlaceholder" :sel-fid="selectedFindingId" />
          </div>
          <div v-else-if="b.t === 'para'" class="node kind-utterance">
            <div class="utterance-meta"><span :class="{ code: view === 'ai' && b.nvs[0]!.node.speaker_code?.startsWith('[[') }">{{ view === 'ai' ? b.nvs[0]!.node.speaker_code : utteranceLabel(b.nvs[0]!.node) }}</span> · {{ stamp(b.nvs[0]!.node.t_start_ms ?? 0) }}</div>
            <span v-for="nv in b.nvs" :key="nv.node.loc" class="sent" :data-node="nv.node.loc" :class="nodeClass(nv)"><NodeText :nv="nv" :ai="view === 'ai'" :sel-id="selectedSuspectId" :sel-ph="selectedPlaceholder" :sel-fid="selectedFindingId" /></span>
          </div>
          <table v-else-if="b.t === 'table'" class="tbl">
            <tbody><tr v-for="row in b.rows" :key="row.r"><td v-for="c in row.cells" :key="c.node.loc" :data-node="c.node.loc" :class="nodeClass(c)" :data-fid="c.bar ? c.tags?.[0]?.id : undefined"><NodeText :nv="c" :ai="view === 'ai'" :sel-id="selectedSuspectId" :sel-ph="selectedPlaceholder" :sel-fid="selectedFindingId" /></td></tr></tbody>
          </table>
          <button v-else type="button" class="fold" @click="unfolded = new Set(unfolded).add(activeId)">{{ b.n }} 段{{ isResult ? '无标注' : '未改动' }} · 展开</button>
        </template>
      </div>
    </div>
    <DocSpectrum v-if="isResult && blocks.length" :ticks="ticks" :scroller="scroller" @pick="onTick" @seek="onSeek" />
    </div>

    <p v-if="!isResult" class="cv-foot hint">{{ view === 'orig' ? `${protectedCount} 处已在本机保护 · 划选文字可补充` : '这是模型将看到的样子 · 代号在本机还原' }}</p>
    <p v-else-if="view !== 'raw'" class="cv-foot hint">{{ view === 'orig' ? `${protectedCount} 处已在本机保护${askEnabled ? ' · 划选文字可追问' : ''}` : '这是模型看到的样子 · 结论已在本机还原' }}</p>

    <div v-if="pop" class="pop" :style="{ left: `${pop.left}px`, top: `${pop.top}px` }" role="dialog" @click.stop>
      <template v-if="isResult">
        <p v-if="pop.seg === 'kept'" class="pop-text">{{ pop.suspect?.text }} · 保留原文，未替换</p>
        <p v-else-if="pop.item" class="pop-text">{{ REDACTION_CATEGORY[pop.item.category] ?? pop.item.category }} · <span class="code-tag">{{ pop.item.placeholder }}</span></p>
      </template>
      <template v-else-if="pop.suspect">
        <p class="pop-text">{{ pop.suspect.text }}</p>
        <template v-if="pop.seg === 'kept'">
          <el-button size="small" :disabled="locked" @click="popRestore">{{ pop.suspect.kept_by === 'user' || pop.suspect.decision === 'ignore' ? '改回替换' : '替换' }}</el-button>
        </template>
        <template v-else>
          <el-button size="small" :disabled="locked" @click="popKeep">保留原文</el-button>
          <el-dropdown trigger="click" :disabled="locked" @command="popReplace">
            <el-button size="small" :disabled="locked">指定类别…</el-button>
            <template #dropdown><el-dropdown-menu><el-dropdown-item v-for="o in categoryOptions" :key="o.value" :command="o.value">{{ o.label }}</el-dropdown-item></el-dropdown-menu></template>
          </el-dropdown>
        </template>
      </template>
      <template v-else-if="pop.item">
        <p class="pop-text">{{ REDACTION_CATEGORY[pop.item.category] ?? pop.item.category }} · <span class="code-tag">{{ pop.item.placeholder }}</span></p>
        <el-button v-if="pop.item.can_revoke" size="small" :disabled="locked" @click="popRevoke">撤销（不是敏感信息）</el-button>
      </template>
    </div>

    <div v-if="tip && !pop" class="tip" :style="{ left: `${tip.left}px`, top: `${tip.top}px` }" aria-hidden="true">
      <span class="tip-label">{{ tip.label }}{{ tip.ai ? '：' : '' }}</span><span :class="tip.ai ? 'tip-orig' : 'code-tag'">{{ tip.text }}</span>
    </div>
    <div v-if="ask" class="pickbar" :style="{ left: `${pickPos.left}px`, top: `${pickPos.top}px` }">
      <el-button type="primary" size="small" @mousedown.prevent @click="submitAsk">追问这段</el-button>
    </div>
    <div v-if="pick || pickNotice" class="pickbar" :style="{ left: `${pickPos.left}px`, top: `${pickPos.top}px` }">
      <template v-if="pick">
        <span class="pick-label">补充脱敏</span>
        <el-select v-model="pickCategory" size="small" class="pick-cat" aria-label="类别"><el-option v-for="o in categoryOptions" :key="o.value" :label="o.label" :value="o.value" /></el-select>
        <el-button type="primary" size="small" :disabled="locked" @click="submitPick">设为敏感信息</el-button>
        <span v-if="locked" class="pick-note">正在重新计算，请稍候</span>
      </template>
      <span v-if="pickNotice" class="pick-note">{{ pickNotice }}</span>
    </div>
  </div>
</template>

<style scoped>
.utterance-meta { color: var(--si-paper-ink-2); font: var(--si-font-meta); margin-bottom: 4px; }
.kind-utterance { content-visibility: auto; contain-intrinsic-size: auto 80px; }
.cv { position: relative; flex: 1; min-height: 0; display: flex; flex-direction: column; gap: 8px; }
.cv-bar { flex: none; height: 32px; display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.cv-extra { flex: none; display: flex; align-items: center; gap: 12px; }
.spacer { flex: 1; }
/* 文件页签（V31-U5F-03 问题 6）：选中为主色文字 + 加粗 + 下划线，未选中为次要色；确认页与结果页同款 */
.tabs { flex: 1; min-width: 0; display: flex; gap: 4px; overflow-x: auto; scrollbar-width: none; }
.tab { flex: none; max-width: 220px; display: flex; align-items: center; gap: 6px; height: 32px; padding: 0 10px; border: 0; border-bottom: 2px solid transparent; border-radius: 0; background: transparent; color: var(--si-ink-3); font: var(--si-font-label); cursor: pointer; transition: color var(--si-dur-1) var(--si-ease), border-color var(--si-dur-1) var(--si-ease); }
.tab:hover { color: var(--si-ink-1); }
.tab.on { border-bottom-color: var(--el-color-primary); color: var(--el-color-primary); font-weight: 600; }
.tab:focus-visible { outline: 2px solid var(--si-focus); outline-offset: 1px; }
.tab-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tab-n { flex: none; padding: 0 6px; border-radius: 999px; background: var(--si-cap-bg); color: var(--si-cap-ink); font: var(--si-font-label-sm); }
.stage-wrap { position: relative; flex: 1; min-height: 0; display: flex; }
.stage { flex: 1; min-height: 0; overflow-y: auto; padding: 20px; border-radius: var(--si-radius-card); }
.stage.with-spectrum { padding-right: 24px; }
.paper { max-width: 760px; margin: 0 auto; padding: 40px 48px; border-radius: var(--si-radius-paper); box-shadow: var(--si-paper-shadow); font: var(--si-font-reading); }
.paper.fx-lite.ai { animation: cv-fade 160ms var(--si-ease); }
.node { margin: 0 0 10px; white-space: pre-wrap; overflow-wrap: anywhere; content-visibility: auto; contain-intrinsic-size: auto 48px; }
.node.head { font-weight: 600; margin-top: 18px; }
.clause { margin-right: 8px; color: var(--si-ink-3); font-size: 13px; }
.side { padding-left: 12px; border-left: 2px solid var(--si-hair-strong); color: var(--si-ink-3); font-size: 13px; }
.note { margin-top: 28px; padding-top: 16px; border-top: 1px dashed var(--si-hair-strong); }
.side-label { margin-bottom: 4px; color: var(--si-ink-3); font-size: 12px; }
.tbl { width: 100%; margin: 10px 0; border-collapse: collapse; font-size: 14px; }
.tbl td { padding: 6px 8px; border: 1px solid var(--si-hair-strong); vertical-align: top; overflow-wrap: anywhere; white-space: pre-wrap; }
.fold { display: block; width: 100%; margin: 8px 0; padding: 6px; border: 1px dashed var(--si-hair-strong); border-radius: 8px; background: transparent; color: var(--si-ink-3); font: var(--si-font-label-sm); cursor: pointer; }
.fold:hover { color: var(--el-color-primary); border-color: var(--el-color-primary); }
.cv-foot { flex: none; margin: 0; }

/* 结果页问题标注（V31-U3 3.3）：片段用等级底色与底线，找不到片段时整段左侧竖条；本地校验位置为灰色竖条 */
.node { position: relative; }
.ann-bar, .local-bar { box-shadow: inset 3px 0 0 var(--bar); padding-left: 10px; margin-left: -13px; }
.ann-bar-high { --bar: var(--si-danger); }
.ann-bar-medium { --bar: var(--si-warn); }
.ann-bar-low { --bar: var(--si-info); }
.local-bar { --bar: var(--si-ink-3); }
td.ann-bar, td.local-bar { margin-left: 0; padding-left: 8px; cursor: pointer; }
.ann-tag { float: right; margin: 0 0 4px 8px; padding: 0 6px; border: 0; border-radius: var(--si-radius-tag); background: var(--si-surface-2); color: var(--si-ink-2); font: var(--si-font-label-sm); line-height: 20px; cursor: pointer; }
.ann-hit > .ann-tag { background: var(--el-color-primary); color: var(--si-white); }
:deep(.ann) { color: inherit; border-radius: 2px; cursor: pointer; }
:deep(.ann-high) { background: var(--si-hl-high); box-shadow: inset 0 -2px 0 var(--si-danger); }
:deep(.ann-medium) { background: var(--si-hl-medium); box-shadow: inset 0 -2px 0 var(--si-warn); }
:deep(.ann-low) { background: var(--si-hl-low); box-shadow: inset 0 -2px 0 var(--si-info); }
:deep(.ann-high.on) { background: color-mix(in srgb, var(--si-danger) 28%, var(--si-paper)); }
:deep(.ann-medium.on) { background: color-mix(in srgb, var(--si-warn) 30%, var(--si-paper)); }
:deep(.ann-low.on) { background: color-mix(in srgb, var(--si-info) 30%, var(--si-paper)); }

/* 胶囊：括号内的内容仍是原文本身，图标用伪元素，不进入文字内容 */
:deep(.cap), :deep(.unc) { padding: 0 4px; border-radius: 4px; background: var(--si-cap-bg); color: var(--si-cap-ink); cursor: pointer; }
:deep(.cap::before) { content: ''; display: inline-block; width: 8px; height: 10px; margin-right: 3px; background: currentColor; clip-path: polygon(50% 0, 100% 18%, 100% 55%, 50% 100%, 0 55%, 0 18%); }
:deep(.unc) { border: 1px dashed var(--si-cap-ink); }
:deep(.code) { padding: 0 4px; border-radius: 4px; background: var(--si-code-bg); color: var(--si-code-ink); font-family: var(--si-font-mono); font-size: .9em; cursor: pointer; }
:deep(.code.unc) { border: 1px dashed var(--si-code-ink); }
:deep(.kept) { text-decoration: underline dotted var(--si-ink-3); text-underline-offset: 3px; cursor: pointer; }
:deep(.sel) { outline: 2px solid var(--el-color-primary); outline-offset: 1px; }
:deep(.flash) { outline: 2px solid var(--si-focus); outline-offset: 2px; }
.fx-full.flipping :deep(.cap), .fx-full.flipping :deep(.unc), .fx-full.flipping :deep(.code) { display: inline-block; animation: cv-flip 320ms var(--si-spring) both; animation-delay: calc(var(--i, 0) * 12ms); }
@keyframes cv-flip { from { transform: rotateX(90deg); opacity: 0; } to { transform: none; opacity: 1; } }
@keyframes cv-fade { from { opacity: 0; } to { opacity: 1; } }

.pop, .pickbar { position: absolute; z-index: 20; display: flex; flex-wrap: wrap; align-items: center; gap: 8px; max-width: 420px; padding: 8px 10px; border-radius: 10px; background: var(--si-surface-pop); box-shadow: 0 0 0 1px var(--si-hair-strong), var(--si-shadow-2); }
.pop { width: 268px; }
.tip { position: absolute; z-index: 19; display: flex; align-items: center; gap: 6px; max-width: 312px; height: 28px; padding: 0 10px; border-radius: 999px; background: var(--si-surface-pop); box-shadow: 0 0 0 1px var(--si-hair-strong), var(--si-shadow-2); pointer-events: none; white-space: nowrap; }
.tip-label { flex: none; color: var(--si-ink-3); font: var(--si-font-label-sm); }
.tip-orig { min-width: 0; overflow: hidden; text-overflow: ellipsis; padding: 0 6px; border-radius: 4px; background: var(--si-cap-bg); color: var(--si-cap-ink); font: var(--si-font-label); }
.tip .code-tag { min-width: 0; overflow: hidden; text-overflow: ellipsis; }
.pop-text { flex: 1 0 100%; margin: 0; overflow-wrap: anywhere; font: var(--si-font-body); }
.code-tag { padding: 0 6px; border-radius: 4px; background: var(--si-code-bg); color: var(--si-code-ink); font-family: var(--si-font-mono); font-size: 12px; }
.pick-label { font: var(--si-font-label-sm); color: var(--si-ink-3); }
.pick-cat { width: 110px; }
.pick-note { color: var(--el-color-warning); font: var(--si-font-caption); }
</style>
