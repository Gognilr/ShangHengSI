<script setup lang="ts">
import SiNotice from '@/components/ui/SiNotice.vue'
import { computed, h, nextTick, onBeforeUnmount, reactive, ref, watch, type FunctionalComponent, type VNode } from 'vue'
import { ApiError } from '@/api/http'
import { previewApi, type OcrCorrection, type PreviewNode, type PreviewNodes } from '@/api/preview'
import type { ManualInput, RedactionItemPos, Suspect, SuspectCategory, UserCategory } from '@/api/redaction'
import type { TaskFile } from '@/api/tasks'
import type { DiffChange, DiffResult } from '@/api/assistant'
import DocPreview from '@/components/DocPreview.vue'
import DocSpectrum, { type SpectrumTick } from '@/components/result/DocSpectrum.vue'
import SiSegmented from '@/components/ui/SiSegmented.vue'
import { readAskSelection, readTextSelection, type TextSelection } from '@/composables/useTextSelection'
import { ArrowLeftRight, ChevronRight, Ellipsis, Eye, FileText, Layers, Sparkle, Tags, TextCursorInput } from '@lucide/vue'
import EmptyState from '@/components/EmptyState.vue'
import { MANUAL_CATEGORY_OPTIONS, REDACTION_CATEGORY, REDACTION_KINDS, SEVERITY_ORDER, redactionKind, toOptions } from '@/utils/labels'
import { excerptRange, overlayRange } from '@/utils/marks'
import { useFx } from '@/utils/motion'
import { sameParagraph } from '@/utils/transcript'

/**
 * 单画布“原文 / AI 视野”（V31-U2 第四节）：一张纸面显示当前文件的原文，替换处为胶囊；
 * AI 视野显示模型将看到的代号。所有原文与代号都按纯文本渲染。
 * mode=result（V31-U3 3.3）：只读；纸面上标出问题与本地校验位置，右缘光谱条，划选只能“追问这段”，可切到“原件”。
 */
/** 纸面上的一条问题标注：挂在第一个 loc 的节点上。 */
export interface CanvasAnnotation { id: string; no: string; severity: string; loc: string; excerpt: string | null; title?: string }
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
  rawLoader?: (taskId: string, fileId: string) => Promise<Blob>
  mode?: 'confirm' | 'result'
  annotations?: CanvasAnnotation[]
  localLocs?: string[]
  selectedFindingId?: string
  aiView?: boolean
  askEnabled?: boolean
  rawHighlight?: string[]
  correctOcr?: (fileId: string, loc: string, body: OcrCorrection) => Promise<void>
  ocrVersion?: number | null
  ocrBusy?: boolean
  /** 工作台会话（V35-D-02）：有上一稿的文件多一个“对比上一稿”视图，只读 */
  diffLoader?: (fileId: string, prevId: string) => Promise<DiffResult>
  /** 七类分段条选中的一类（二版 6.7）：纸面其余类别降到 25%，这一类加描边 */
  kindFilter?: string | null
  /** 结果页（二版第五节“还原扫描”）：这一轮的 id。第一次打开时青色扫描线掠过纸面一次，代号翻成真名；同一轮再次打开不再播放 */
  restoreKey?: string
}>()
const isResult = computed(() => props.mode === 'result')
// 手动补充与撤销只发事件，由确认工作台乐观更新并放进保存队列（V31-U5F-03）
const emit = defineEmits<{
  manual: [input: ManualInput]
  revoke: [item: RedactionItemPos]
  keep: [s: Suspect]
  restore: [s: Suspect]
  replace: [s: Suspect, category?: SuspectCategory]
  /** 胶囊菜单：改类别、扩大或缩小范围（V35-F-04） */
  adjust: [item: RedactionItemPos, start: number, end: number, category?: UserCategory]
  select: [findingId: string]
  ask: [prefill: string]
  'ocr-editing': [open: boolean]
}>()

const fx = useFx()
const categoryOptions = toOptions(MANUAL_CATEGORY_OPTIONS)
const FOLD_LIMIT = 800

// ---- 文件页签与节点 ----
interface FileState { loading: boolean; error: string; available: boolean; nodes: PreviewNode[] }
const states = reactive<Record<string, FileState>>({})
// 已被下一稿取代的文件排在最后，默认打开最新一稿（V35-D-02）
const supersededIds = computed(() => new Set(props.files.map(f => f.prev_file_id).filter(Boolean)))
const sortedFiles = computed(() => [...props.files].sort((a, b) => Number(supersededIds.value.has(a.id)) - Number(supersededIds.value.has(b.id))
  || Number(b.is_main) - Number(a.is_main) || a.seq - b.seq))
const activeId = ref('')
const active = computed(() => states[activeId.value])
const lastFileId = computed(() => sortedFiles.value[sortedFiles.value.length - 1]?.id ?? '')
// reveal：揭幕滑块（二版 6.7），确认页的默认视图，取代“原文 / 模型将看到的”二选一；结果页仍是原文 / 模型看到的
type View = 'orig' | 'ai' | 'raw' | 'diff' | 'reveal'
const revealable = computed(() => props.mode !== 'result' && props.aiView !== false)
const view = ref<View>(props.mode !== 'result' && props.aiView !== false ? 'reveal' : 'orig')
/** 纸面显示原文文字（可点胶囊、划选、校对）：原文视图或揭幕滑块 */
const textView = computed(() => view.value === 'orig' || view.value === 'reveal')
const textViewName = (): View => revealable.value ? 'reveal' : 'orig'
function utteranceLabel(node: PreviewNode) {
  const file = activeFile.value as (TaskFile & { speakers?: Record<string, string | null> }) | undefined
  return file?.speakers?.[String(node.speaker)] || '发言人 ' + node.speaker
}
function stamp(ms = 0) { const s = Math.floor(ms/1000); return [Math.floor(s/3600), Math.floor(s/60)%60,s%60].map(v => String(v).padStart(2,'0')).join(':') }
const activeFile = computed(() => sortedFiles.value.find(f => f.id === activeId.value))
// 原件视图：PDF 或图片且原件未删除
const rawAvailable = (f: TaskFile | undefined) => !!f && !f.original_deleted_at && ['pdf', 'jpg', 'jpeg', 'png'].includes(f.ext.toLowerCase().replace(/^\./, ''))
const pickedOcr = ref('')
const ocrDraft = ref<{ file: TaskFile; node: PreviewNode; version: number; text: string } | null>(null)
const ocrError = ref(''), ocrNotice = ref(''), savingOcr = ref(false)
const editorInput = ref<{ focus: () => void }>()
const scan = computed(() => !!props.correctOcr && !isResult.value && activeFile.value?.ext === 'pdf' && rawAvailable(activeFile.value))
function canPickOcr(node: PreviewNode) {
  return scan.value && textView.value && node.ocr_conf != null && node.page != null && !!node.bbox?.length
}
const selectedOcr = computed(() => active.value?.nodes.find(node => node.loc === pickedOcr.value && canPickOcr(node)))
const hasOcr = computed(() => scan.value && active.value?.nodes.some(node => node.ocr_conf != null))
function pickOcr(node: PreviewNode) { if (canPickOcr(node)) pickedOcr.value = node.loc }
/** 点选扫描块后就地浮出“校对识别文字”（V351 后续：取代页签旁的按钮） */
const ocrBar = ref<{ left: number; top: number } | null>(null)
const ocrBarReady = computed(() => !props.ocrBusy && props.ocrVersion != null)
function showOcrBar(nodeEl: HTMLElement, clientX: number) {
  if (!wrap.value || !selectedOcr.value?.ocr_editable) { ocrBar.value = null; return }
  const r = nodeEl.getBoundingClientRect(), outer = wrap.value.getBoundingClientRect()
  const above = r.top - outer.top - 42
  ocrBar.value = { left: Math.max(8, Math.min(clientX - outer.left - 70, outer.width - 170)), top: above >= 36 ? above : r.bottom - outer.top + 6 }
}
function openOcr(node: PreviewNode) {
  if (!canPickOcr(node) || !node.ocr_editable) return
  pickOcr(node)
  void editOcr()
}
function onPaperDblClick(event: MouseEvent) {
  if (!hasOcr.value || !textView.value) return
  const loc = (event.target as HTMLElement).closest<HTMLElement>('[data-node]')?.dataset.node
  const node = active.value?.nodes.find(n => n.loc === loc)
  if (node) openOcr(node)
}
// 局部放大：原件读入后把该块连同四周画到右侧，叠一个定位框
const ocrPreview = ref<InstanceType<typeof DocPreview>>()
const cropCanvas = ref<HTMLCanvasElement>(), cropWrap = ref<HTMLElement>()
const cropBox = ref<{ left: number; top: number; width: number; height: number } | null>(null)
async function drawOcrCrop() {
  const draft = ocrDraft.value
  await nextTick()
  if (!draft || !ocrPreview.value || !cropCanvas.value) return
  try { cropBox.value = await ocrPreview.value.drawCrop(cropCanvas.value, draft.node.loc, (cropWrap.value?.clientWidth ?? 420) - 24) }
  catch { cropBox.value = null }
}
function endOcr() {
  ocrDraft.value = null
  ocrError.value = ''
  cropBox.value = null
  emit('ocr-editing', false)
}
watch([activeId, view], ([file, nextView], [previousFile]) => {
  if (file !== previousFile || nextView !== 'raw') pickedOcr.value = ''
  ocrBar.value = null
  ocrNotice.value = ''
  if (ocrDraft.value && !savingOcr.value) endOcr()
})
async function editOcr() {
  const node = selectedOcr.value, file = activeFile.value, version = props.ocrVersion
  if (!node?.ocr_editable || !file || version == null || props.ocrBusy) return
  cancel()
  ocrNotice.value = ''
  ocrDraft.value = { file, node, version, text: node.text ?? '' }
  emit('ocr-editing', true)
  await nextTick()
  editorInput.value?.focus()
}
async function commitOcr() {
  const draft = ocrDraft.value
  if (!draft || !props.correctOcr || savingOcr.value || props.ocrBusy || !draft.text.trim()) return
  savingOcr.value = true
  ocrError.value = ''
  try {
    await props.correctOcr(draft.file.id, draft.node.loc, { text: draft.text, expected_material_version: draft.version })
    endOcr()
    await reloadActive()
    if (activeId.value === draft.file.id && !active.value?.error) {
      await locate(draft.node.loc)
      pickedOcr.value = draft.node.loc
      ocrNotice.value = '已保存校订，待发预览已更新，请重新核对'
    }
  } catch (e) {
    if (e instanceof ApiError && ['MATERIAL_CHANGED', 'TASK_STATE_CONFLICT'].includes(e.code)) {
      endOcr()
      await reloadActive()
      ocrNotice.value = '材料已变化，请重新选中并核对识别文字'
    } else ocrError.value = e instanceof ApiError || e instanceof Error ? e.message : '保存校订失败，请重试'
  } finally { savingOcr.value = false }
}
const hasPrev = computed(() => !!props.diffLoader && !!activeFile.value?.prev_file_id)
const viewOptions = computed<{ value: View; label: string }[]>(() => [
  ...(revealable.value ? [{ value: 'reveal' as const, label: '逐字对照' }] : [{ value: 'orig' as const, label: '原文' }, ...(props.aiView === false ? [] : [{ value: 'ai' as const, label: '模型看到的' }])]),
  ...(hasPrev.value ? [{ value: 'diff' as const, label: '对比上一稿' }] : []),
  ...((isResult.value || scan.value) && rawAvailable(activeFile.value) ? [{ value: 'raw' as const, label: '原件' }] : []),
])
watch(viewOptions, opts => { if (!opts.some(o => o.value === view.value)) view.value = opts[0]?.value ?? 'orig' })

// ---- 对比上一稿（V35-D-02，设计 diff.md 3.4）：纸面显示本稿，新增浅底下划线、删除划线、整段删除单列，改过的段左侧竖线 ----
interface DiffState { loading: boolean; error: string; data: DiffResult | null }
const diffs = reactive<Record<string, DiffState>>({})
const activeDiff = computed(() => diffs[`${activeId.value}:${activeFile.value?.prev_file_id ?? ''}`])
async function loadDiff() {
  const file = activeFile.value
  if (!props.diffLoader || !file?.prev_file_id) return
  const key = `${file.id}:${file.prev_file_id}`
  if (diffs[key] && !diffs[key]!.error) return
  const st = reactive<DiffState>({ loading: true, error: '', data: null })
  diffs[key] = st
  try { st.data = await props.diffLoader(file.id, file.prev_file_id) }
  catch (e) { st.error = e instanceof ApiError ? e.message : '对比失败，请重试' }
  finally { st.loading = false }
}
watch([view, activeId, () => activeFile.value?.prev_file_id], () => { if (view.value === 'diff') void loadDiff() })
const diffMaps = computed(() => {
  const changes = view.value === 'diff' ? activeDiff.value?.data?.changes ?? [] : []
  const byLoc = new Map<string, DiffChange>(), before = new Map<string, DiffChange[]>(), after = new Map<string, DiffChange[]>()
  for (const c of changes) {
    if (c.type !== 'del') { if (c.loc) byLoc.set(c.loc, c); continue }
    const map = c.after ? after : before
    const key = c.anchor ?? ''
    map.set(key, [...(map.get(key) ?? []), c])
  }
  return { byLoc, before, after }
})
const DiffText: FunctionalComponent<{ c: DiffChange }> = ({ c }) =>
  h('span', { class: 'txt', 'data-loc': c.loc ?? '' }, c.segs.map(s => s.t === 'eq' ? s.s : h(s.t, { class: [`d-${s.t}`, { 'd-u': s.u }] }, s.s)))
DiffText.props = ['c']
const diffTag = (c: DiffChange) => c.type === 'move' ? `位置调整${c.prev_clause ? `（原${c.prev_clause}）` : ''}` : c.type === 'add' ? '本稿新增' : ''
const diffHint = computed(() => {
  const d = activeDiff.value?.data
  if (!d) return activeDiff.value?.loading ? '正在本机对比…' : ''
  const notes = [`对照第 ${d.prev_no} 稿《${d.prev_name}》`, '本机逐字对比，未发送给模型']
  if (d.trace === 'flag') notes.push('本稿解析较早，只能按段落判断是否留痕')
  if (d.ocr) notes.push('扫描件识字可能有误差，单字差异请对照原件')
  return notes.join(' · ')
})

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
async function reloadActive() {
  const fileId = activeId.value
  cancel()
  pickedOcr.value = ''
  if (!fileId) return
  delete states[fileId]
  await loadFile(fileId)
}
// 文件增删或主文件变化后重新读取节点
watch(() => sortedFiles.value.map(f => f.id).join(','), () => {
  for (const key of Object.keys(states)) delete states[key]
  if (!sortedFiles.value.some(f => f.id === activeId.value)) activeId.value = sortedFiles.value[0]?.id ?? ''
  void loadFile(activeId.value)
}, { immediate: true })

const replaceCount = (f: TaskFile) => props.items.filter(it => it.loc.startsWith(`F${f.seq}-`)).length

// ---- 胶囊按类别着色（澄明 4.2）：单位、人名、联系方式、证件、地址、金额、其他 ----
const kindOf = (category: string) => `k-${redactionKind(category)}`
const LEGEND = REDACTION_KINDS
const presentKinds = computed(() => new Set(props.items.map(it => redactionKind(it.category))))

// ---- 分段：替换项、不确定项、保留文字 ----
type Kind = 'plain' | 'cap' | 'unc' | 'kept'
interface Hl { fid: string; sev: string }
interface Seg { text: string; k: Kind; item?: RedactionItemPos; sid?: number; i: number; hl?: Hl }
/** bar：没能标出片段的问题按最高等级画左侧竖条；tags：挂在本节点的问题。 */
interface NodeView { node: PreviewNode; segs: Seg[]; changed: boolean; bar?: string; local?: boolean; tags?: CanvasAnnotation[]; diff?: DiffChange }
type Block =
  | { t: 'node'; nv: NodeView }
  | { t: 'para'; nvs: NodeView[] }
  | { t: 'table'; key: string; rows: { r: number; cells: NodeView[] }[] }
  | { t: 'fold'; id: string; n: number }
  | { t: 'gone'; changes: DiffChange[] }

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
  const diffing = view.value === 'diff'
  const { byLoc, before, after } = diffMaps.value
  let stash: DiffChange[] = []  // 表格中间的整段删除：等表格结束再单列，不把表格拆开
  const flush = () => { if (stash.length) { flat.push({ t: 'gone', changes: stash }); stash = [] } }
  for (const node of nodes) {
    const segs = buildSegs(node.loc, node.text ?? '', counter)
    const base: NodeView = { node, segs, changed: segs.some(s => s.k !== 'plain') }
    const nv = diffing ? { ...base, changed: false, diff: byLoc.get(node.loc) } : isResult.value ? annotate(base) : base
    const pos = cellPos(node)
    const last = flat[flat.length - 1]
    const inTable = !!pos && last?.t === 'table' && last.key === pos.key
    if (!inTable) flush()
    const gone = diffing ? before.get(node.loc) : undefined
    if (gone) { if (inTable) stash.push(...gone); else flat.push({ t: 'gone', changes: gone }) }
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
    const tail = diffing ? after.get(node.loc) : undefined
    if (tail) stash.push(...tail)
  }
  flush()
  if (nodes.length <= FOLD_LIMIT || unfolded.value.has(activeId.value)) return flat
  // 长文档：只显示有替换或保留文字的块，其余折叠
  // 结果页另外保留有问题标注或本地校验位置的块
  const keep = (nv: NodeView) => nv.changed || !!nv.tags || !!nv.local || !!nv.diff
  const changed = (b: Block) => b.t === 'node' ? keep(b.nv) : b.t === 'para' ? b.nvs.some(keep) : b.t === 'table' ? b.rows.some(r => r.cells.some(keep)) : b.t === 'gone'
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
/** layer：揭幕滑块的两层（r 原文、c 模型将看到的）。每个遮住处是同一个网格单元，原文与代号叠放，宽度取较宽者，每层只显示自己那一个，两层换行完全一致。 */
const NodeText: FunctionalComponent<{ nv: NodeView; ai: boolean; selId: number | null; selPh: string | null; selFid?: string; layer?: 'r' | 'c'; kf?: string | null }> = ({ nv, ai, selId, selPh, selFid, layer, kf }) => {
  const kids: Array<VNode | string> = nv.segs.map((g) => {
    const ann = g.hl && !ai ? ['ann', `ann-${g.hl.sev}`, { on: g.hl.fid === selFid }] : []
    if (g.k === 'plain') return ann.length ? h('mark', { class: ann, 'data-fid': g.hl!.fid }, g.text) : g.text
    const selected = g.k === 'kept' ? g.sid === selId : !!selPh && g.item?.placeholder === selPh
    const kind = g.item ? kindOf(g.item.category) : ''
    const kon = !!kf && (g.item ? kind === `k-${kf}` : false)
    const attrs: Record<string, unknown> = { 'data-seg': g.k, style: { '--i': Math.min(g.i, 40) }, class: [g.k, kind, { sel: selected, kon }, ...ann] }
    if (ann.length) attrs['data-fid'] = g.hl!.fid
    if (g.item) attrs['data-item'] = `${g.item.loc}|${g.item.char_start}|${g.item.char_end}`
    if (g.sid !== undefined) attrs['data-sid'] = g.sid
    if (g.k === 'kept') return h('span', attrs, g.text)
    if (layer) {
      attrs.class = ['rx', kind, { sel: selected, kon }]
      attrs['data-orig'] = g.text
      const unc = g.k === 'unc' ? ' unc' : ''
      return h('span', attrs, [h('span', { class: `rx-r ${g.k}` }, g.text), h('span', { class: `rx-c code${unc}` }, g.item!.placeholder)])
    }
    if (ai) { attrs.class = [g.k === 'unc' ? 'code unc' : 'code', kind, { sel: selected, kon }]; attrs['data-orig'] = g.text; return h('span', attrs, g.item!.placeholder) }
    if (restoring.value && !ann.length) { attrs.class = ['rs', kind]; return h('span', attrs, [h('span', { class: 'rs-c' }, g.item!.placeholder), h('span', { class: `rs-r ${g.k}` }, g.text)]) }
    return h('span', attrs, g.text)
  })
  return h('span', { class: ai ? 'ai-txt' : 'txt', 'data-loc': layer === 'c' ? undefined : nv.node.loc }, kids)
}
NodeText.props = ['nv', 'ai', 'selId', 'selPh', 'selFid', 'layer', 'kf']
const nodeClass = (nv: NodeView) => nv.diff ? ['d-chg', `d-${nv.diff.type}`, { 'd-u': nv.diff.untracked }] : [nv.bar ? `ann-bar ann-bar-${nv.bar}` : '', { 'local-bar': nv.local && !nv.bar, 'ann-hit': nv.tags?.some(a => a.id === props.selectedFindingId) }]
/** 揭幕滑块的两层；其他视图只有一层 n */
const layers = computed(() => view.value === 'reveal' ? ['r', 'c'] as const : ['n'] as const)
const lyr = (layer: 'r' | 'c' | 'n') => layer === 'n' ? undefined : layer
/** 定位、划选只认原文层：代号层不带 data-node */
const dn = (layer: 'r' | 'c' | 'n', loc: string) => layer === 'c' ? undefined : loc
/** 发言人：原文层写名字，模型视图写代号；揭幕滑块两层叠在同一格，换行一致 */
const SpeakerName: FunctionalComponent<{ node: PreviewNode; layer: 'r' | 'c' | 'n' }> = ({ node, layer }) => {
  const code = node.speaker_code, coded = !!code?.startsWith('[[')
  if (layer === 'n') return view.value === 'ai' ? h('span', { class: { code: coded } }, code ?? '') : h('span', utteranceLabel(node))
  return h('span', { class: 'rx rx-meta' }, [h('span', { class: 'rx-r' }, utteranceLabel(node)), h('span', { class: ['rx-c', { code: coded }] }, code ?? '')])
}
SpeakerName.props = ['node', 'layer']
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
interface Pop { left: number; top: number; wordHeight: number; seg: 'cap' | 'unc' | 'kept'; item?: RedactionItemPos; suspect?: Suspect; text?: string }
const wrap = ref<HTMLElement>()
const scroller = ref<HTMLElement>()
const pop = ref<Pop | null>(null)
const closePop = () => { pop.value = null; popMode.value = 'menu' }
// 菜单的二级：改类别、调整范围（V35-F-04）
const popMode = ref<'menu' | 'category' | 'range'>('menu')
const range = ref({ start: 0, end: 0 })
/** 已读入的节点原文（按位置），用于菜单里的同名计数与范围预览 */
const nodeText = computed(() => {
  const m = new Map<string, string>()
  for (const st of Object.values(states)) for (const n of st.nodes) m.set(n.loc, n.text ?? '')
  if (props.note) m.set('NOTE', props.note)
  return m
})
const cut = (loc: string, a: number, b: number) => Array.from(nodeText.value.get(loc) ?? '').slice(a, b).join('')
/** 同名：同一代号、原文写法相同的替换项（未读入的文件按长度相同估计） */
const sameName = computed(() => {
  const it = pop.value?.item
  if (!it) return 0
  const surface = cut(it.loc, it.char_start, it.char_end)
  return props.items.filter(x => x.placeholder === it.placeholder && (nodeText.value.has(x.loc)
    ? cut(x.loc, x.char_start, x.char_end) === surface : x.char_end - x.char_start === it.char_end - it.char_start)).length
})
const popCategory = computed(() => pop.value?.item ? REDACTION_CATEGORY[pop.value.item.category] ?? pop.value.item.category : '')
const popSource = computed(() => {
  const it = pop.value?.item
  if (!it) return ''
  return it.detected_by === 'uncertain' ? '不确定，默认遮住' : it.detected_by === 'manual' || it.detected_by === 'user' ? '你加的' : it.detected_by === 'party' ? '签约方' : '自动识别'
})
function openRange() {
  const it = pop.value?.item
  if (!it) return
  range.value = { start: it.char_start, end: it.char_end }
  popMode.value = 'range'
}
function nudgeRange(edge: 'start' | 'end', delta: number) {
  const it = pop.value?.item
  if (!it) return
  const len = Array.from(nodeText.value.get(it.loc) ?? '').length
  const next = { ...range.value }
  next[edge] = Math.max(0, Math.min(len, next[edge] + delta))
  if (next.end - next.start >= 2 && next.end - next.start <= 100) range.value = next
}
const rangeView = computed(() => {
  const it = pop.value?.item
  if (!it) return { before: '', mid: '', after: '' }
  const { start, end } = range.value
  return { before: cut(it.loc, Math.max(0, start - 12), start), mid: cut(it.loc, start, end), after: cut(it.loc, end, end + 12) }
})
function applyRange() {
  const it = pop.value?.item
  const { start, end } = range.value
  closePop()
  if (it && (start !== it.char_start || end !== it.char_end)) emit('adjust', it, start, end)
}
function popCategoryPick(c: UserCategory) {
  const p = pop.value
  closePop()
  if (p?.suspect && p.seg !== 'kept' && p.item?.detected_by === 'uncertain') emit('replace', p.suspect, c)
  else if (p?.item) emit('adjust', p.item, p.item.char_start, p.item.char_end, c)
}
function onPaperClick(event: MouseEvent) {
  if (window.getSelection() && !window.getSelection()!.isCollapsed) return
  const fidEl = isResult.value ? (event.target as HTMLElement).closest<HTMLElement>('[data-fid]') : null
  if (fidEl && !(event.target as HTMLElement).closest('[data-seg]')) { closePop(); emit('select', fidEl.dataset.fid!); return }
  const tgt = event.target as HTMLElement
  ocrBar.value = null
  const el = tgt.closest<HTMLElement>('[data-seg]')
  if (hasOcr.value && textView.value) {
    const nodeEl = tgt.closest<HTMLElement>('[data-node]')
    const node = active.value?.nodes.find(n => n.loc === nodeEl?.dataset.node)
    if (node) pickOcr(node)
    if (node && nodeEl && !el) showOcrBar(nodeEl, event.clientX)
  }
  if (!el || !wrap.value) { closePop(); return }
  const seg = el.dataset.seg as Pop['seg']
  const box = el.getBoundingClientRect()
  const outer = wrap.value.getBoundingClientRect()
  const base = { left: Math.max(8, Math.min(box.left - outer.left, outer.width - 300)), top: box.bottom - outer.top + 6, wordHeight: box.height, seg }
  if (seg === 'kept') {
    pop.value = { ...base, suspect: props.suspects.find(s => s.id === Number(el.dataset.sid)) }
    return
  }
  const [loc, s, e] = (el.dataset.item ?? '').split('|')
  const item = props.items.find(it => it.loc === loc && it.char_start === Number(s) && it.char_end === Number(e))
  popMode.value = 'menu'
  pop.value = { ...base, item, text: el.dataset.orig ?? el.textContent ?? '', suspect: item ? suspectByPlaceholder.value.get(item.placeholder) : undefined }
}
// 就地反馈（V321-E-01）：保留后在该词上方显示“已保留 · 撤销”，5 秒后消失
interface KeptNote { left: number; top: number; suspect: Suspect }
const keptNote = ref<KeptNote | null>(null)
let keptTimer: ReturnType<typeof setTimeout> | undefined
function clearKeptNote() { clearTimeout(keptTimer); keptNote.value = null }
function popKeep() {
  const p = pop.value
  closePop()
  if (!p?.suspect) return
  emit('keep', p.suspect)
  keptNote.value = { left: p.left, top: Math.max(4, p.top - 4 - p.wordHeight - 34), suspect: p.suspect }
  clearTimeout(keptTimer)
  keptTimer = setTimeout(() => { keptNote.value = null }, 5000)
}
function undoKept() {
  const s = keptNote.value?.suspect
  clearKeptNote()
  if (s) emit('restore', s)
}
function popRestore() { const s = pop.value?.suspect; closePop(); if (s) emit('restore', s) }
function popReplace(c: UserCategory) { const s = pop.value?.suspect; closePop(); if (s) emit('replace', s, c) }
function popRevoke() {
  const item = pop.value?.item
  closePop()
  if (!item) return
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
  if (!textView.value || !props.askEnabled || !wrap.value) return
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
  if (!textView.value) return
  const result = readTextSelection(itemsByLoc.value)
  clearPick()
  if (!result || !wrap.value) return
  const outer = wrap.value.getBoundingClientRect()
  if (result.notice) { pickNotice.value = result.notice; pickPos.value = { left: 12, top: 8 }; return }
  const s = result.selection!
  pick.value = s
  pickPos.value = { left: Math.max(8, Math.min(s.rect.left - outer.left, outer.width - 380)), top: Math.max(4, s.rect.top - outer.top - 50) }
}
function submitPick(category: UserCategory = pickCategory.value) {
  const s = pick.value
  if (!s || props.locked) return
  window.getSelection()?.removeAllRanges()
  clearPick()
  pickCategory.value = category
  emit('manual', { loc: s.loc, char_start: s.start, char_end: s.end, category })
}
const QUICK_PICK: UserCategory[] = ['PERSON', 'ORG', 'IM', 'ADDR']
const QUICK_KIND: Record<string, string> = { PERSON: 'k-person', ORG: 'k-org', IM: 'k-contact', ADDR: 'k-addr' }

// ---- 定位（4.4） ----
async function locate(loc: string, text?: string, findingId?: string) {
  const file = loc === 'NOTE' ? sortedFiles.value[sortedFiles.value.length - 1] : sortedFiles.value.find(f => loc.startsWith(`F${f.seq}-`))
  if (!file) return
  // 原件视图下只切页签，由原件视图按 bbox 高亮
  if (view.value === 'raw' && rawAvailable(file)) { activeId.value = file.id; return }
  if (!textView.value) view.value = textViewName()
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
  const target = marked ?? (findingId ? node : undefined) ?? (text ? candidates.find(el => (el.dataset.orig ?? el.textContent) === text) : undefined) ?? candidates[0] ?? node
  target.classList.add('flash')
  setTimeout(() => target.classList.remove('flash'), 1600)
}
/** 右栏“变化”点了一条：切到对比视图、对应文件，定位并闪烁（V35-D-02） */
async function showDiff(fileId: string, change: DiffChange) {
  const file = sortedFiles.value.find(f => f.id === fileId)
  if (!file?.prev_file_id || !props.diffLoader) return
  activeId.value = file.id
  view.value = 'diff'
  await loadFile(file.id)
  await loadDiff()
  unfolded.value = new Set(unfolded.value).add(file.id)
  await focusDiff(change.type === 'del' ? { gone: change.id } : { loc: change.loc ?? '' })
}
async function focusDiff(target: { loc?: string; gone?: string }) {
  await nextTick()
  const box = scroller.value
  const el = target.gone ? box?.querySelector<HTMLElement>(`[data-gone="${CSS.escape(target.gone)}"]`)
    : box?.querySelector<HTMLElement>(`[data-node="${CSS.escape(target.loc ?? '')}"]`)
  if (!box || !el) return
  box.scrollTop += el.getBoundingClientRect().top - box.getBoundingClientRect().top - 80
  el.classList.add('flash')
  setTimeout(() => el.classList.remove('flash'), 1600)
}
function switchFile(delta: 1 | -1) {
  const list = sortedFiles.value
  const index = list.findIndex(f => f.id === activeId.value)
  if (list.length > 1 && index >= 0) activeId.value = list[(index + delta + list.length) % list.length]!.id
}
function toggleView() {
  if (view.value === 'reveal') { setReveal(reveal.value > 0 ? 0 : 100); return }
  if (revealable.value) { view.value = 'reveal'; return }
  view.value = view.value === 'orig' ? 'ai' : 'orig'
}

// ---- 揭幕滑块（二版 6.7）：紫线左边是原文，右边是模型将看到的；两层逐字对齐，右层用 clip-path 只露出紫线右边 ----
const reveal = ref(50)
const paperEl = ref<HTMLElement>()
function setReveal(x: number) { reveal.value = Math.round(Math.max(0, Math.min(100, x)) * 10) / 10 }
const revealText = computed(() => reveal.value <= 0 ? '全部为模型将看到的' : reveal.value >= 100 ? '全部为原文' : `左边 ${Math.round(reveal.value)}% 为原文`)
let dragId: number | null = null
function onHandleDown(e: PointerEvent) {
  if (e.button !== 0) return
  e.preventDefault()
  dragId = e.pointerId
  ;(e.currentTarget as HTMLElement).setPointerCapture(e.pointerId)
  closePop(); tip.value = null
  onHandleMove(e)
}
function onHandleMove(e: PointerEvent) {
  if (dragId !== e.pointerId || !paperEl.value) return
  const b = paperEl.value.getBoundingClientRect()
  setReveal((e.clientX - b.left) / b.width * 100)   // 跟手，无缓动
}
function onHandleUp(e: PointerEvent) { if (dragId === e.pointerId) dragId = null }  // 松手不回弹
function onKnobKey(e: KeyboardEvent) {
  const step: Record<string, number> = { ArrowLeft: -5, ArrowRight: 5 }
  if (e.key in step) setReveal(reveal.value + step[e.key]!)
  else if (e.key === 'Home') setReveal(0)
  else if (e.key === 'End') setReveal(100)
  else return
  e.preventDefault()
}
function cancel() { closePop(); clearPick(); ask.value = null; ocrBar.value = null; window.getSelection()?.removeAllRanges() }

// ---- 光谱条（结果页）：刻度位置 = 块序号 ÷ 当前渲染的块数 ----
const ticks = computed<SpectrumTick[]>(() => {
  const list = blocks.value
  const out: SpectrumTick[] = []
  if (view.value === 'diff') {
    list.forEach((b, i) => {
      const pos = (i + 0.5) / list.length
      const views = b.t === 'node' ? [b.nv] : b.t === 'para' ? b.nvs : b.t === 'table' ? b.rows.flatMap(r => r.cells) : []
      for (const nv of views) if (nv.diff && nv.diff.type !== 'format') out.push({ pos, kind: nv.diff.untracked ? 'chg-u' : 'chg', loc: nv.node.loc })
      if (b.t === 'gone') for (const c of b.changes) out.push({ pos, kind: c.untracked ? 'chg-u' : 'chg', loc: `gone:${c.id}` })
    })
    return out
  }
  if (!isResult.value) return []
  list.forEach((b, i) => {
    const pos = (i + 0.5) / list.length
    const views = b.t === 'node' ? [b.nv] : b.t === 'para' ? b.nvs : b.t === 'table' ? b.rows.flatMap(r => r.cells) : []
    let cap = false
    for (const nv of views) {
      for (const a of nv.tags ?? []) out.push({ pos, kind: (['high', 'medium', 'low'].includes(a.severity) ? a.severity : 'low') as SpectrumTick['kind'], loc: nv.node.loc, fid: a.id, title: `${a.no} ${a.title ?? ''}`.trim() })
      if (nv.local) out.push({ pos, kind: 'local', loc: nv.node.loc })
      if (!cap && nv.changed) { cap = true; out.push({ pos, kind: 'cap', loc: nv.node.loc }) }
    }
  })
  return out
})
function onTick(t: SpectrumTick) {
  if (view.value === 'diff') { void focusDiff(t.loc.startsWith('gone:') ? { gone: t.loc.slice(5) } : { loc: t.loc }); return }
  void locate(t.loc, undefined, t.fid)
  if (t.fid) emit('select', t.fid)
}
function onSeek(ratio: number) {
  const box = scroller.value
  if (box) box.scrollTop = ratio * box.scrollHeight - box.clientHeight / 2
}
// ---- 还原扫描（二版第五节）：扫描线自上而下掠过可见纸面约 1.7 秒，经过处的代号沿横轴翻成真名；翻完后代号格收起，只在结束时重排一次 ----
const RESTORED_KEY = 'si.restored'
const SWEEP_MS = 1700
const restoring = ref(false)
const sweepH = ref(0)
function playedBefore(key: string): boolean {
  let list: string[] = []
  try { list = JSON.parse(localStorage.getItem(RESTORED_KEY) ?? '[]') as string[] } catch { /* 坏值按没播过 */ }
  if (list.includes(key)) return true
  try { localStorage.setItem(RESTORED_KEY, JSON.stringify([...list, key].slice(-300))) } catch { /* 存不下就每次都播 */ }
  return false
}
let restoreTimer: ReturnType<typeof setTimeout> | undefined
watch(() => [props.restoreKey, isResult.value && view.value === 'orig' && !active.value?.loading && blocks.value.length > 0 && props.items.length > 0] as const, async ([key, ready]) => {
  if (!key || !ready || restoring.value || playedBefore(key) || fx.value !== 'full') return
  restoring.value = true
  await nextTick()
  const box = scroller.value
  if (!box) { restoring.value = false; return }
  const top = box.getBoundingClientRect().top, h = box.clientHeight
  sweepH.value = h
  for (const el of Array.from(box.querySelectorAll<HTMLElement>('.rs'))) {
    const y = el.getBoundingClientRect().top - top
    el.style.setProperty('--d', `${Math.round(Math.max(0, Math.min(1, y / h)) * SWEEP_MS)}ms`)
  }
  clearTimeout(restoreTimer)
  restoreTimer = setTimeout(() => { restoring.value = false }, SWEEP_MS + 520)
})
onBeforeUnmount(() => clearTimeout(restoreTimer))
function onStageScroll() { closePop(); ask.value = null; tip.value = null; ocrBar.value = null; clearKeptNote() }

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
  const pb = paperEl.value?.getBoundingClientRect()
  const ai = view.value === 'ai' || (view.value === 'reveal' && !!pb && event.clientX > pb.left + pb.width * reveal.value / 100)
  const above = box.top - outer.top - 34
  tip.value = {
    left: Math.max(8, Math.min(box.left - outer.left, outer.width - 320)),
    top: above >= 0 ? above : box.bottom - outer.top + 6,
    ai,
    label: ai ? '原文' : item.detected_by === 'uncertain' ? '不确定是否敏感，已默认发送为' : `${REDACTION_CATEGORY[item.category] ?? item.category} · 发送为`,
    text: ai ? (el.dataset.orig ?? '') : item.placeholder,
  }
}
defineExpose({ locate, switchFile, toggleView, cancel, showDiff, reloadActive })
</script>

<template>
  <div ref="wrap" class="cv">
    <div class="cv-bar">
      <div v-if="sortedFiles.length > 1" class="tabs" role="tablist">
        <button v-for="f in sortedFiles" :key="f.id" type="button" role="tab" class="tab" :class="{ on: f.id === activeId }" :aria-selected="f.id === activeId" :disabled="!!ocrDraft" :title="f.original_name" @click="activeId = f.id">
          <span class="tab-name">{{ f.original_name }}</span><span class="tab-n">{{ replaceCount(f) }}</span>
        </button>
      </div>
      <SiSegmented v-if="!ocrDraft && viewOptions.length > 1" v-model="view" :options="viewOptions" size="small" ariaLabel="原文、模型将看到的或对比上一稿" />
      <p v-if="ocrDraft" class="ocr-title">校对识别文字 · {{ ocrDraft.file.original_name }} 第 {{ ocrDraft.node.page }} 页</p>
      <div v-if="!isResult && sortedFiles.length <= 1 && !ocrDraft" class="legend" aria-label="颜色说明">
        <span v-for="[k, label] in LEGEND" v-show="presentKinds.has(k)" :key="k" :class="`k-${k}`"><i class="kdot" />{{ label }}</span>
      </div>
      <span v-else-if="sortedFiles.length <= 1" class="spacer" />
      <div v-if="$slots.bar" class="cv-extra"><slot name="bar" /></div>
    </div>

    <section v-if="ocrDraft" class="ocr-editor" aria-label="校对 PDF 扫描文字">
      <div class="ocr-original">
        <DocPreview ref="ocrPreview" :task-id="taskId" :file="ocrDraft.file" :highlight="[ocrDraft.node.loc]" :focus-page="ocrDraft.node.page ?? undefined" auto-zoom :node-loader="nodeLoader" :raw-loader="rawLoader" @ready="drawOcrCrop" />
      </div>
      <div class="ocr-form">
        <p class="ocr-label">原件局部</p>
        <div ref="cropWrap" class="ocr-crop si-paper" :class="{ ready: !!cropBox }">
          <div class="crop-inner">
            <canvas ref="cropCanvas" />
            <div v-if="cropBox" class="crop-hl" :style="{ left: `${cropBox.left * 100}%`, top: `${cropBox.top * 100}%`, width: `${cropBox.width * 100}%`, height: `${cropBox.height * 100}%` }" />
          </div>
          <span v-if="!cropBox" class="crop-wait">正在放大原件…</span>
        </div>
        <label class="ocr-label" for="ocr-correction">识别文字</label>
        <el-input id="ocr-correction" ref="editorInput" v-model="ocrDraft.text" type="textarea" :autosize="{ minRows: 2, maxRows: 8 }"
          maxlength="20000" :disabled="savingOcr" @keydown.esc.stop.prevent="!savingOcr && endOcr()" />
        <p class="hint">对照原件改清晰可辨的文字。签字、盖章看不清时，请核验原件并保留提示。</p>
        <SiNotice v-if="ocrError" :title="ocrError" type="error" :closable="false" />
        <div class="ocr-actions">
          <el-button :disabled="savingOcr" @click="endOcr">取消</el-button>
          <el-button type="primary" :loading="savingOcr" :disabled="ocrBusy || !ocrDraft.text.trim() || ocrDraft.text === ocrDraft.node.text"
            @click="commitOcr">保存校订</el-button>
        </div>
      </div>
    </section>
    <DocPreview v-if="!ocrDraft && view === 'raw' && activeFile" :task-id="taskId" :file="activeFile" :highlight="rawHighlight ?? (pickedOcr ? [pickedOcr] : [])" :node-loader="nodeLoader" :raw-loader="rawLoader" />
    <div v-else v-show="!ocrDraft" class="stage-wrap">
    <span v-if="restoring" class="sweep" :style="{ '--h': `${sweepH}px` }" aria-hidden="true" />
    <div ref="scroller" v-loading="active?.loading || (view === 'diff' && activeDiff?.loading)" class="stage si-stage" :class="{ 'with-spectrum': isResult || view === 'diff' }" @scroll.passive="onStageScroll">
      <SiNotice v-if="active?.error" type="error" :closable="false" :title="active.error" />
      <EmptyState v-else-if="active && !active.loading && !active.available" illus="stack" tone="neutral" title="原文已按保留期清理" description="原件已按保留期限删除，这里不再显示原文。" />
      <SiNotice v-else-if="view === 'diff' && activeDiff?.error" type="error" :closable="false" :title="activeDiff.error" />
      <EmptyState v-else-if="active && !active.loading && !blocks.length" illus="stack" tone="neutral" title="没有可显示的正文" description="这份文件没有读出文字；扫描件请等识别完成后再看。" />
      <div v-else ref="paperEl" class="si-paper paper" :class="[{ flipping }, `fx-${fx}`, { ai: view === 'ai', res: isResult, diffing: view === 'diff', reveal: view === 'reveal', kfilter: !!kindFilter }]" :style="view === 'reveal' ? { '--x': `${reveal}%` } : undefined" :key="view === 'ai' && fx === 'lite' ? 'a' : 'o'" @click="onPaperClick" @mouseup="onPaperMouseUp" @mouseover="onPaperOver" @mouseleave="tip = null" @dblclick="onPaperDblClick">
        <div v-if="view === 'reveal'" class="rv-tags" aria-hidden="true"><span class="rv-tag l"><FileText :size="12" :stroke-width="2" />原文</span><span class="rv-tag r"><Sparkle :size="12" :stroke-width="2" />模型将看到的</span></div>
        <div v-for="layer in layers" :key="layer" class="ly" :class="`ly-${layer}`" :aria-hidden="layer === 'c' ? 'true' : undefined">
        <template v-for="(b, bi) in blocks" :key="bi">
          <div v-if="b.t === 'node'" class="node" :class="[`kind-${b.nv.node.kind}`, { head: b.nv.node.kind === 'heading', note: b.nv.node.loc === 'NOTE', side: b.nv.node.part !== 'body' }, ...nodeClass(b.nv), { 'ocr-picked': pickedOcr === b.nv.node.loc, 'ocr-pickable': canPickOcr(b.nv.node) && b.nv.node.ocr_editable }]" :data-node="dn(layer, b.nv.node.loc)"
            :tabindex="layer !== 'c' && canPickOcr(b.nv.node) ? 0 : undefined" @keydown.enter.self.prevent="openOcr(b.nv.node)">
            <div v-if="b.nv.node.kind === 'utterance'" class="utterance-meta"><SpeakerName :node="b.nv.node" :layer="layer" /> · {{ stamp(b.nv.node.t_start_ms ?? 0) }}</div>
            <button v-if="b.nv.tags" type="button" class="ann-tag" :data-fid="b.nv.tags[0]!.id">{{ tagText(b.nv.tags) }}</button>
            <span v-if="b.nv.diff && (b.nv.diff.untracked || diffTag(b.nv.diff))" class="d-tag" :class="{ 'd-tag-u': b.nv.diff.untracked && b.nv.diff.type !== 'format' }">{{ b.nv.diff.untracked && b.nv.diff.type !== 'format' ? (b.nv.diff.mixed ? '部分未留痕' : '未留痕') : diffTag(b.nv.diff) }}</span>
            <div v-if="b.nv.node.loc === 'NOTE'" class="side-label">本次补充说明</div>
            <span v-else-if="b.nv.node.part !== 'body'" class="side-label">{{ b.nv.node.part }}</span>
            <span v-if="b.nv.node.clause_no && b.nv.node.part === 'body' && !(b.nv.node.text ?? '').trimStart().startsWith(b.nv.node.clause_no)" class="clause">{{ b.nv.node.clause_no }}</span>
            <DiffText v-if="b.nv.diff" :c="b.nv.diff" />
            <NodeText v-else :nv="b.nv" :ai="view === 'ai'" :sel-id="selectedSuspectId" :sel-ph="selectedPlaceholder" :sel-fid="selectedFindingId" :layer="lyr(layer)" :kf="kindFilter" />
          </div>
          <div v-else-if="b.t === 'gone'" class="gone-wrap">
            <div v-for="c in b.changes" :key="c.id" class="node gone d-chg" :class="{ 'd-u': c.untracked }" :data-gone="c.id">
              <span class="d-tag" :class="{ 'd-tag-u': c.untracked }">本稿已删除{{ c.untracked ? ' · 未留痕' : '' }}</span>
              <span class="gone-clause">{{ c.clause }}</span><del class="d-del">{{ c.segs[0]?.s }}</del>
            </div>
          </div>
          <div v-else-if="b.t === 'para'" class="node kind-utterance">
            <div class="utterance-meta"><SpeakerName :node="b.nvs[0]!.node" :layer="layer" /> · {{ stamp(b.nvs[0]!.node.t_start_ms ?? 0) }}</div>
            <span v-for="nv in b.nvs" :key="nv.node.loc" class="sent" :data-node="dn(layer, nv.node.loc)" :class="nodeClass(nv)"><NodeText :nv="nv" :ai="view === 'ai'" :sel-id="selectedSuspectId" :sel-ph="selectedPlaceholder" :sel-fid="selectedFindingId" :layer="lyr(layer)" :kf="kindFilter" /></span>
          </div>
          <table v-else-if="b.t === 'table'" class="tbl">
            <tbody><tr v-for="row in b.rows" :key="row.r"><td v-for="c in row.cells" :key="c.node.loc" :data-node="dn(layer, c.node.loc)" :class="[nodeClass(c), { 'ocr-picked': pickedOcr === c.node.loc, 'ocr-pickable': canPickOcr(c.node) && c.node.ocr_editable }]"
              :tabindex="layer !== 'c' && canPickOcr(c.node) ? 0 : undefined" :data-fid="c.bar ? c.tags?.[0]?.id : undefined"
              @keydown.enter.self.prevent="openOcr(c.node)"><DiffText v-if="c.diff" :c="c.diff" /><NodeText v-else :nv="c" :ai="view === 'ai'" :sel-id="selectedSuspectId" :sel-ph="selectedPlaceholder" :sel-fid="selectedFindingId" :layer="lyr(layer)" :kf="kindFilter" /></td></tr></tbody>
          </table>
          <button v-else type="button" class="fold" :tabindex="layer === 'c' ? -1 : undefined" @click="unfolded = new Set(unfolded).add(activeId)">{{ b.n }} 段{{ isResult ? '无标注' : '未改动' }} · 展开</button>
        </template>
        </div>
        <div v-if="view === 'reveal'" class="rv-handle" @pointerdown="onHandleDown" @pointermove="onHandleMove" @pointerup="onHandleUp" @pointercancel="onHandleUp" @click.stop>
          <span class="rv-knob" tabindex="0" role="slider" aria-label="对照位置：左边原文，右边模型将看到的" aria-valuemin="0" aria-valuemax="100"
            :aria-valuenow="Math.round(reveal)" :aria-valuetext="revealText" @keydown="onKnobKey"><ArrowLeftRight :size="18" :stroke-width="2" /></span>
        </div>
      </div>
    </div>
    <DocSpectrum v-if="(isResult || view === 'diff') && blocks.length" :ticks="ticks" :scroller="scroller" @pick="onTick" @seek="onSeek" />
    </div>

    <p v-if="ocrNotice" class="cv-foot hint" role="status">{{ ocrNotice }}</p>
    <p v-else-if="hasOcr && textView && !ocrDraft" class="cv-foot hint">点扫描文字块或单元格可校对识别文字（双击直接打开）。签章不清楚或没有识别文字的地方需看原件。</p>

    <div v-if="ocrBar && !ocrDraft && !pop" class="pickbar" :style="{ left: `${ocrBar.left}px`, top: `${ocrBar.top}px` }">
      <el-button type="primary" size="small" :disabled="!ocrBarReady" :title="ocrBarReady ? '' : '正在处理，请稍候'" @click.stop="ocrBar = null; editOcr()">校对识别文字</el-button>
    </div>
    <p v-else-if="scan && view === 'raw'" class="cv-foot hint">签章不清楚或没有识别文字的地方，请核验原件。</p>
    <p v-else-if="view === 'diff'" class="cv-foot hint">{{ diffHint }}</p>
    <p v-else-if="!isResult && !ocrDraft" class="cv-foot hint">{{ view === 'reveal' ? `${protectedCount} 处已在本机遮住 · 拖动紫线逐字对照（← → 每次 5%，V 全部切换）· 点胶囊可修改 · 划选文字可补充` : view === 'orig' ? `${protectedCount} 处已在本机遮住 · 点胶囊可修改 · 划选文字可补充` : '这是模型将看到的样子 · 代号在本机还原' }}</p>
    <p v-else-if="isResult && view !== 'raw'" class="cv-foot hint">{{ view === 'orig' ? `${protectedCount} 处已在本机保护${askEnabled ? ' · 划选文字可追问' : ''}` : '这是模型看到的样子 · 结论已在本机还原' }}</p>

    <div v-if="pop && !isResult && pop.item" class="pop menu" :style="{ left: `${pop.left}px`, top: `${pop.top}px` }" role="menu" @click.stop>
      <div class="pm-h"><i class="kdot" :class="kindOf(pop.item.category)" /><b class="pm-text">{{ pop.text }}</b><span>{{ popCategory }} · {{ popSource }}</span></div>
      <template v-if="popMode === 'menu'">
        <template v-if="pop.item.category === 'AMOUNT'">
          <p class="pm-note">金额不能取消或改范围：发送前复检对金额不做豁免。</p>
        </template>
        <template v-else>
          <button v-if="pop.suspect && pop.item.detected_by === 'uncertain'" type="button" class="mi" :disabled="locked" @click="popKeep"><Eye :size="15" :stroke-width="1.75" />不遮住，保留原文</button>
          <button type="button" class="mi" :disabled="locked" @click="popMode = 'category'"><Tags :size="15" :stroke-width="1.75" />改类别<ChevronRight class="mi-end" :size="14" :stroke-width="1.75" /></button>
          <template v-if="pop.item.can_revoke && pop.item.detected_by !== 'uncertain'">
            <button type="button" class="mi" :disabled="locked || sameName > 1" :title="sameName > 1 ? `同名的其他 ${sameName - 1} 处仍会遮住，这一处露出会被发送前复检拦下` : ''" @click="popRevoke"><Eye :size="15" :stroke-width="1.75" />只取消这一处</button>
            <p v-if="sameName > 1" class="pm-note">同名还有 {{ sameName - 1 }} 处，只露出这一处会被发送前复检拦下</p>
            <button v-if="sameName > 1" type="button" class="mi" :disabled="locked" @click="popRevoke"><Layers :size="15" :stroke-width="1.75" />取消全部同名（{{ sameName }} 处）</button>
          </template>
          <button type="button" class="mi" :disabled="locked" @click="openRange"><TextCursorInput :size="15" :stroke-width="1.75" />扩大或缩小范围</button>
        </template>
      </template>
      <template v-else-if="popMode === 'category'">
        <div class="pm-cats">
          <button v-for="o in categoryOptions.filter(c => c.value !== 'AMOUNT')" :key="o.value" type="button" class="pm-cat" :class="kindOf(o.value)" :disabled="locked" @click="popCategoryPick(o.value as UserCategory)">{{ o.label }}</button>
        </div>
        <button type="button" class="mi back" @click="popMode = 'menu'">返回</button>
      </template>
      <template v-else>
        <p class="pm-range"><span class="ctx">{{ rangeView.before }}</span><mark :class="kindOf(pop.item.category)">{{ rangeView.mid }}</mark><span class="ctx">{{ rangeView.after }}</span></p>
        <div class="pm-nudge">
          <span>左边</span><el-button size="small" @click="nudgeRange('start', -1)">扩一字</el-button><el-button size="small" @click="nudgeRange('start', 1)">收一字</el-button>
          <span>右边</span><el-button size="small" @click="nudgeRange('end', -1)">收一字</el-button><el-button size="small" @click="nudgeRange('end', 1)">扩一字</el-button>
        </div>
        <p class="pm-note">同一写法在全文一起调整；碰到旁边已遮住的内容时并成一项。</p>
        <div class="pm-foot"><el-button size="small" text @click="popMode = 'menu'">返回</el-button><el-button size="small" type="primary" :disabled="locked" @click="applyRange">改为这个范围</el-button></div>
      </template>
    </div>
    <div v-else-if="pop" class="pop" :style="{ left: `${pop.left}px`, top: `${pop.top}px` }" role="dialog" @click.stop>
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
        <el-button v-if="pop.item.can_revoke" size="small" :disabled="locked" @click="popRevoke">{{ pop.item.detected_by === 'recheck' ? '不敏感，可以发送' : '撤销（不是敏感信息）' }}</el-button>
      </template>
    </div>

    <div v-if="keptNote" class="tip kept-note" :style="{ left: `${keptNote.left}px`, top: `${keptNote.top}px` }" role="status">
      <span class="tip-label">已保留“{{ Array.from(keptNote.suspect.text).slice(0, 8).join('') }}”</span>
      <el-button link type="primary" size="small" @click="undoKept">撤销</el-button>
    </div>
    <div v-if="tip && !pop" class="tip" :style="{ left: `${tip.left}px`, top: `${tip.top}px` }" aria-hidden="true">
      <span class="tip-label">{{ tip.label }}{{ tip.ai ? '：' : '' }}</span><span :class="tip.ai ? 'tip-orig' : 'code-tag'">{{ tip.text }}</span>
    </div>
    <div v-if="ask" class="pickbar" :style="{ left: `${pickPos.left}px`, top: `${pickPos.top}px` }">
      <el-button type="primary" size="small" @mousedown.prevent @click="submitAsk">追问这段</el-button>
    </div>
    <div v-if="pick || pickNotice" class="pickbar" :style="{ left: `${pickPos.left}px`, top: `${pickPos.top}px` }">
      <template v-if="pick">
        <span class="pick-label">遮住为</span>
        <button v-for="c in QUICK_PICK" :key="c" type="button" class="pm-cat" :class="QUICK_KIND[c]" :disabled="locked" @mousedown.prevent @click="submitPick(c)">{{ MANUAL_CATEGORY_OPTIONS[c] }}</button>
        <el-dropdown trigger="click" :disabled="locked" @command="(c: UserCategory) => submitPick(c)">
          <button type="button" class="pm-more" aria-label="其他类别" @mousedown.prevent><Ellipsis :size="15" :stroke-width="1.75" /></button>
          <template #dropdown><el-dropdown-menu><el-dropdown-item v-for="o in categoryOptions.filter(c => !QUICK_PICK.includes(c.value as UserCategory))" :key="o.value" :command="o.value">{{ o.label }}</el-dropdown-item></el-dropdown-menu></template>
        </el-dropdown>
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
.cv-extra { flex: 0 1 auto; min-width: 0; display: flex; align-items: center; gap: 12px; overflow: hidden; }
.spacer { flex: 1; }
/* 文件页签（V31-U5F-03 问题 6）：选中为主色文字 + 加粗 + 下划线，未选中为次要色；确认页与结果页同款 */
.tabs { flex: 1; min-width: 112px; display: flex; gap: 4px; overflow-x: auto; scrollbar-width: none; }
.tab { flex: none; max-width: 220px; display: flex; align-items: center; gap: 6px; height: 32px; padding: 0 10px; border: 0; border-bottom: 2px solid transparent; border-radius: 0; background: transparent; color: var(--si-ink-3); font: var(--si-font-label); cursor: pointer; transition: color var(--si-dur-1) var(--si-ease), border-color var(--si-dur-1) var(--si-ease); }
.tab:hover { color: var(--si-ink-1); }
.tab.on { border-bottom-color: var(--el-color-primary); color: var(--el-color-primary); font-weight: 600; }
.tab:focus-visible { outline: 2px solid var(--si-focus); outline-offset: 1px; }
.tab-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tab-n { flex: none; padding: 0 6px; border-radius: var(--si-radius-badge); background: var(--si-cap-bg); color: var(--si-cap-ink); font: var(--si-font-label-sm); }
.stage-wrap { position: relative; flex: 1; min-height: 0; display: flex; }
.stage { flex: 1; min-height: 0; overflow-y: auto; padding: 20px; border-radius: var(--si-radius-card); }
.stage.with-spectrum { padding-right: 36px; }
.paper { max-width: 760px; margin: 0 auto; padding: 40px 48px; border-radius: var(--si-radius-paper); box-shadow: var(--si-paper-shadow); font: var(--si-font-reading); }
.paper.fx-lite.ai { animation: cv-fade 160ms var(--si-ease); }
.node { margin: 0 0 10px; white-space: pre-wrap; overflow-wrap: anywhere; content-visibility: auto; contain-intrinsic-size: auto 48px; }
.node.head { font-weight: 600; margin-top: 18px; }
.clause { margin-right: 8px; color: var(--si-ink-3); font-size: calc(13px * var(--si-fs)); }
.side { padding-left: 12px; border-left: 2px solid var(--si-hair-strong); color: var(--si-ink-3); font-size: calc(13px * var(--si-fs)); }
.note { margin-top: 28px; padding-top: 16px; border-top: 1px dashed var(--si-hair-strong); }
.side-label { margin-bottom: 4px; color: var(--si-ink-3); font-size: calc(13px * var(--si-fs)); }
.tbl { width: 100%; margin: 10px 0; border-collapse: collapse; font-size: calc(14px * var(--si-fs)); }
.tbl td { padding: 6px 8px; border: 1px solid var(--si-hair-strong); vertical-align: top; overflow-wrap: anywhere; white-space: pre-wrap; }
.fold { display: block; width: 100%; margin: 8px 0; padding: 6px; border: 1px dashed var(--si-hair-strong); border-radius: var(--si-radius-sm); background: transparent; color: var(--si-ink-3); font: var(--si-font-label-sm); cursor: pointer; }
.fold:hover { color: var(--el-color-primary); border-color: var(--el-color-primary); }
.cv-foot { flex: none; margin: 0; }
.ocr-pickable { cursor: text; }
.ocr-picked { outline: 1px solid var(--si-brand-2); outline-offset: -1px; background: var(--si-select-bg); }
/* 校对编辑器：左原件（可缩放，打开即放大到该块），右“原件局部”放大图在输入框正上方，眼睛只在两者间来回 */
.ocr-editor { flex: 1; min-height: 0; display: grid; grid-template-columns: minmax(0, 3fr) minmax(320px, 2fr); gap: 16px; overflow: auto; }
.ocr-original, .ocr-form { display: flex; flex-direction: column; min-width: 0; min-height: 0; gap: 10px; }
.ocr-form { overflow-y: auto; }
.ocr-original > .doc-preview { min-height: 260px; }
.ocr-editor p { margin: 0; }
.ocr-title { flex: 1; min-width: 0; margin: 0; overflow: hidden; color: var(--si-ink-2); font: var(--si-font-label); text-overflow: ellipsis; white-space: nowrap; }
.ocr-label { margin: 0; font: var(--si-font-label); }
.ocr-crop { position: relative; flex: none; min-height: 72px; max-height: 300px; overflow: auto; padding: 12px; border-radius: var(--si-radius-paper); box-shadow: var(--si-paper-shadow); }
.crop-inner { position: relative; width: max-content; margin: 0 auto; line-height: 0; visibility: hidden; }
.ocr-crop.ready .crop-inner { visibility: visible; }
.crop-inner canvas { display: block; max-width: none; }
.crop-hl { position: absolute; box-sizing: border-box; border: 1.5px solid var(--si-warn); border-radius: 2px; pointer-events: none; }
.crop-wait { position: absolute; inset: 0; display: grid; place-items: center; color: var(--si-ink-3); font: var(--si-font-caption); }
.ocr-actions { display: flex; justify-content: flex-end; }
@media (max-width: 1040px) {
  .ocr-editor { grid-template-columns: minmax(0, 1fr); }
  .ocr-original { min-height: 320px; }
}

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

/* 对比上一稿（V35-D-02）：新增主色浅底下划线，删除次要文字色划线，改过的段左侧主色竖线；未留痕为警示色竖线与小标 */
.d-chg { box-shadow: inset 3px 0 0 var(--d-bar, var(--el-color-primary)); padding-left: 10px; margin-left: -13px; }
td.d-chg { margin-left: 0; padding-left: 8px; }
.d-chg.d-u { --d-bar: var(--si-warn); }
.d-chg.d-format { --d-bar: var(--si-hair-strong); }
:deep(ins.d-ins) { padding: 0 1px; border-radius: 2px; background: var(--si-blue-tint); color: inherit; text-decoration: underline 1.5px var(--el-color-primary); text-underline-offset: 3px; }
:deep(ins.d-ins.d-u) { text-decoration-color: var(--si-warn); }
:deep(del.d-del), del.d-del { color: var(--si-ink-3); text-decoration: line-through 1px; }
.d-format :deep(ins.d-ins) { background: none; text-decoration-style: dotted; }
.d-tag { float: right; margin: 0 0 4px 8px; padding: 0 6px; border-radius: var(--si-radius-tag); background: var(--si-surface-2); color: var(--si-ink-2); font: var(--si-font-label-sm); line-height: 20px; }
.d-tag-u { background: var(--si-warn-soft); color: var(--si-warn-ink); }
.gone { color: var(--si-ink-3); }
.gone-clause { margin-right: 8px; font-size: calc(13px * var(--si-fs)); }

/* 胶囊（澄明 8.4）：按类别着色的全圆角浅底，文字仍是原文本身；不确定项为虚线边 */
.k-org { --k: var(--si-k-org); --k-bg: var(--si-k-org-bg); }
.k-person { --k: var(--si-k-person); --k-bg: var(--si-k-person-bg); }
.k-contact { --k: var(--si-k-contact); --k-bg: var(--si-k-contact-bg); }
.k-id { --k: var(--si-k-id); --k-bg: var(--si-k-id-bg); }
.k-addr { --k: var(--si-k-addr); --k-bg: var(--si-k-addr-bg); }
.k-amount { --k: var(--si-k-amount); --k-bg: var(--si-k-amount-bg); }
.k-other { --k: var(--si-k-other); --k-bg: var(--si-k-other-bg); }
:deep(.k-org) { --k: var(--si-k-org); --k-bg: var(--si-k-org-bg); }
:deep(.k-person) { --k: var(--si-k-person); --k-bg: var(--si-k-person-bg); }
:deep(.k-contact) { --k: var(--si-k-contact); --k-bg: var(--si-k-contact-bg); }
:deep(.k-id) { --k: var(--si-k-id); --k-bg: var(--si-k-id-bg); }
:deep(.k-addr) { --k: var(--si-k-addr); --k-bg: var(--si-k-addr-bg); }
:deep(.k-amount) { --k: var(--si-k-amount); --k-bg: var(--si-k-amount-bg); }
:deep(.k-other) { --k: var(--si-k-other); --k-bg: var(--si-k-other-bg); }
:deep(.cap), :deep(.unc) { padding: 1px 7px; border-radius: var(--si-radius-badge); background: var(--k-bg, var(--si-cap-bg)); color: var(--k, var(--si-cap-ink)); font-weight: 500; cursor: pointer; transition: box-shadow var(--si-d2) var(--si-ease-out); box-decoration-break: clone; -webkit-box-decoration-break: clone; }
:deep(.cap:hover), :deep(.unc:hover) { box-shadow: 0 0 0 1.5px var(--k, var(--si-cap-ink)); }
/* 结果页：遮住的名称退为正文色加类别色虚线，纸面上只有问题高亮醒目（对齐设计稿 8.4 结果页） */
.res :deep(.cap:not(.ann)), .res :deep(.unc:not(.ann)) { padding: 0; border-radius: 0; background: none; color: inherit; font-weight: inherit; text-decoration: underline dotted 1.5px var(--k, var(--si-cap-ink)); text-underline-offset: 4px; }
.res :deep(.cap:not(.ann):hover), .res :deep(.unc:not(.ann):hover) { box-shadow: none; background: var(--k-bg, var(--si-cap-bg)); }
:deep(.unc) { box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--k, var(--si-cap-ink)) 55%, transparent); background-image: none; outline: 1px dashed color-mix(in srgb, var(--k, var(--si-cap-ink)) 70%, transparent); outline-offset: -1px; }
:deep(.code) { padding: 1px 7px; border-radius: var(--si-radius-badge); background: var(--k-bg, var(--si-code-bg)); color: var(--k, var(--si-code-ink)); font-family: var(--si-font-mono); font-size: .88em; cursor: pointer; }
:deep(.code.unc) { outline: 1px dashed var(--k, var(--si-code-ink)); outline-offset: -1px; }
/* 保留过的词留浅色下划线，点它可改回替换（V321-E-01） */
:deep(.kept) { text-decoration: underline solid color-mix(in srgb, var(--si-ink-3) 55%, transparent); text-decoration-thickness: 2px; text-underline-offset: 3px; cursor: pointer; }
:deep(.sel) { outline: 2px solid var(--el-color-primary); outline-offset: 1px; }
:deep(.flash) { outline: 2px solid var(--si-focus); outline-offset: 2px; }
.fx-full.flipping :deep(.cap), .fx-full.flipping :deep(.unc), .fx-full.flipping :deep(.code) { display: inline-block; animation: cv-flip 320ms var(--si-spring) both; animation-delay: calc(var(--i, 0) * 12ms); }
@keyframes cv-flip { from { transform: rotateX(90deg); opacity: 0; } to { transform: none; opacity: 1; } }
@keyframes cv-fade { from { opacity: 0; } to { opacity: 1; } }

.pop, .pickbar { position: absolute; z-index: 20; display: flex; flex-wrap: wrap; align-items: center; gap: 8px; max-width: 420px; padding: 8px 10px; border-radius: var(--si-radius-md); background: var(--si-surface-pop); box-shadow: var(--si-shadow-pop); animation: cv-pop var(--si-d2) var(--si-ease-out); }
@keyframes cv-pop { from { opacity: 0; transform: translateY(-4px) scale(.98); } to { opacity: 1; transform: none; } }
.legend { flex: 1; min-width: 0; display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 4px 12px; color: var(--si-ink-3); font: var(--si-font-caption); }
.legend span { display: inline-flex; align-items: center; gap: 5px; }
.kdot { display: inline-block; flex: none; width: 8px; height: 8px; border-radius: 50%; background: var(--k, var(--si-ink-4)); }
.pop.menu { display: block; width: 292px; padding: 6px; }
.pm-h { display: flex; align-items: center; gap: 8px; padding: 6px 8px 8px; margin-bottom: 4px; border-bottom: 1px solid var(--si-hair-faint); color: var(--si-ink-3); font: var(--si-font-caption); }
.pm-h .pm-text { min-width: 0; max-width: 132px; overflow: hidden; color: var(--si-ink-1); font: var(--si-font-label); text-overflow: ellipsis; white-space: nowrap; }
.pm-h span { margin-left: auto; white-space: nowrap; }
.mi { display: flex; align-items: center; gap: 10px; width: 100%; height: 36px; padding: 0 10px; border: 0; border-radius: var(--si-radius-sm); background: transparent; color: var(--si-ink-1); font: var(--si-font-body); text-align: left; cursor: pointer; }
.mi svg { flex: none; color: var(--si-ink-3); }
.mi:hover:not(:disabled) { background: var(--si-hover-bg); }
.mi:disabled { color: var(--si-ink-4); cursor: not-allowed; }
.mi:focus-visible { outline: 2px solid var(--si-focus); outline-offset: -2px; }
.mi .mi-end { margin-left: auto; }
.mi.back { justify-content: center; height: 32px; color: var(--si-ink-3); font: var(--si-font-label-sm); }
.pm-note { margin: 0 10px 6px; color: var(--si-ink-3); font: var(--si-font-caption); }
.pm-cats { display: flex; flex-wrap: wrap; gap: 6px; padding: 6px 6px 8px; }
.pm-cat { height: 28px; padding: 0 12px; border: 0; border-radius: var(--si-radius-badge); background: var(--k-bg); color: var(--k); font: var(--si-font-label-sm); cursor: pointer; transition: transform var(--si-d1) var(--si-ease-out), box-shadow var(--si-d2) var(--si-ease-out); }
.pm-cat:hover:not(:disabled) { box-shadow: 0 0 0 1.5px var(--k); }
.pm-cat:active:not(:disabled) { transform: scale(.97); }
.pm-cat:disabled { opacity: .5; cursor: not-allowed; }
.pm-more { display: grid; place-items: center; width: 28px; height: 28px; border: 0; border-radius: var(--si-radius-sm); background: transparent; color: var(--si-ink-3); cursor: pointer; }
.pm-more:hover { background: var(--si-hover-bg); }
.pm-range { margin: 4px 8px 8px; padding: 8px 10px; border-radius: var(--si-radius-sm); background: var(--si-card-2); font: var(--si-font-body); overflow-wrap: anywhere; }
.pm-range .ctx { color: var(--si-ink-3); }
.pm-range mark { padding: 1px 4px; border-radius: var(--si-radius-tag); background: var(--k-bg); color: var(--k); }
.pm-nudge { display: grid; grid-template-columns: auto 1fr 1fr; align-items: center; gap: 6px 6px; margin: 0 8px 8px; color: var(--si-ink-3); font: var(--si-font-caption); }
.pm-nudge :deep(.el-button + .el-button) { margin-left: 0; }
.pm-foot { display: flex; justify-content: flex-end; gap: 6px; padding: 4px 6px 4px; }
.pop { width: 268px; }
.tip { position: absolute; z-index: 19; display: flex; align-items: center; gap: 6px; max-width: 312px; height: 28px; padding: 0 10px; border-radius: var(--si-radius-badge); background: var(--si-surface-pop); box-shadow: var(--si-shadow-3); pointer-events: none; white-space: nowrap; }
.tip.kept-note { pointer-events: auto; }
.tip-label { flex: none; color: var(--si-ink-3); font: var(--si-font-label-sm); }
.tip-orig { min-width: 0; overflow: hidden; text-overflow: ellipsis; padding: 0 6px; border-radius: 4px; background: var(--si-cap-bg); color: var(--si-cap-ink); font: var(--si-font-label); }
.tip .code-tag { min-width: 0; overflow: hidden; text-overflow: ellipsis; }
.pop-text { flex: 1 0 100%; margin: 0; overflow-wrap: anywhere; font: var(--si-font-body); }
.code-tag { padding: 0 6px; border-radius: 4px; background: var(--si-code-bg); color: var(--si-code-ink); font-family: var(--si-font-mono); font-size: calc(12px * var(--si-fs)); }
.pick-label { font: var(--si-font-caption); color: var(--si-ink-3); }
.pickbar { gap: 6px; padding: 6px 8px; }
.pick-cat { width: 110px; }
.pick-note { color: var(--el-color-warning); font: var(--si-font-caption); }
/* 揭幕滑块（二版 6.7）：右层（模型将看到的）叠在左层（原文）上，clip-path 只露出紫线右边，拖动时不重排 */
.paper.reveal { position: relative; }
/* 两层都是独立的块格式化上下文：首段的上边距不与外层折叠，两层逐像素同高 */
.reveal .ly { display: flow-root; }
.reveal .node, .reveal .kind-utterance { content-visibility: visible; }
.ly-c { position: absolute; inset: 0; padding: inherit; border-radius: inherit; background: var(--si-paper); clip-path: inset(0 0 0 var(--x)); pointer-events: none; user-select: none; }
/* 一个遮住处 = 一个网格单元：原文与代号叠放，宽度取较宽者，每层只显示自己那一个；整体不断行 */
:deep(.rx) { display: inline-grid; vertical-align: baseline; text-indent: 0; }
:deep(.rx > *) { grid-area: 1 / 1; display: inline-flex; align-items: center; justify-content: center; text-align: center; }
:deep(.rx-meta > *) { justify-content: flex-start; text-align: left; }
.ly-r :deep(.rx-c), .ly-c :deep(.rx-r) { visibility: hidden; }
.ly-r :deep(.rx-c) { user-select: none; }
.rv-handle { position: absolute; top: 0; bottom: 0; left: var(--x); z-index: 4; width: 24px; margin-left: -12px; cursor: ew-resize; touch-action: none; }
.rv-handle::before { content: ''; position: absolute; top: 0; bottom: 0; left: 11px; width: 2px; background: var(--si-ai); box-shadow: 0 0 0 3px color-mix(in srgb, var(--si-ai) 14%, transparent); }
.rv-knob { position: sticky; top: calc(50% - 20px); display: grid; place-items: center; width: 40px; height: 40px; margin: 120px 0 0 -8px; border-radius: 50%; background: var(--si-ai); color: var(--si-white); box-shadow: 0 6px 16px -4px color-mix(in srgb, var(--si-ai) 60%, transparent); outline: 0; cursor: ew-resize; }
.rv-knob:focus-visible { box-shadow: 0 0 0 4px var(--si-surface-1), 0 0 0 7px var(--si-ai); }
.rv-tags { position: sticky; top: 30px; z-index: 5; height: 0; margin: 0 -34px; pointer-events: none; }
.rv-tag { position: absolute; top: -26px; display: inline-flex; align-items: center; gap: 5px; height: 24px; padding: 0 9px; border-radius: var(--si-radius-badge); font: var(--si-font-badge); }
.rv-tag.l { left: 0; background: var(--si-hover-bg); color: var(--si-ink-3); }
.rv-tag.r { right: 0; background: var(--si-ai-soft); color: var(--si-ai-ink); }
/* 七类分段条点了一段：其余类别降到 25%，这一类加描边 */
.kfilter :deep([data-seg]:not(.kon)) { opacity: .25; transition: opacity var(--si-d2) var(--si-ease-out); }
.kfilter :deep(.cap.kon), .kfilter :deep(.unc.kon), .kfilter :deep(.code.kon), .kfilter :deep(.rx.kon > *) { box-shadow: 0 0 0 2px var(--k); }
/* 还原扫描（二版第五节）：翻转期间代号与真名叠在同一格，翻完后代号格收起 */
.sweep { position: absolute; top: 0; left: max(8px, calc(50% - 392px)); width: min(calc(100% - 16px), 784px); height: 3px; z-index: 3; border-radius: 2px; background: var(--si-trip-l); box-shadow: 0 0 0 6px color-mix(in srgb, var(--si-trip-l) 16%, transparent), 0 0 24px 6px color-mix(in srgb, var(--si-trip-l) 25%, transparent); pointer-events: none; animation: rs-sweep 1700ms linear both; }
@keyframes rs-sweep { 0% { transform: translateY(0); opacity: 0; } 6% { opacity: 1; } 94% { opacity: 1; } 100% { transform: translateY(var(--h)); opacity: 0; } }
:deep(.rs) { display: inline-grid; vertical-align: baseline; perspective: 400px; }
:deep(.rs > *) { grid-area: 1 / 1; backface-visibility: hidden; }
:deep(.rs-c) { align-self: center; padding: 0 6px; border-radius: var(--si-radius-tag); background: var(--si-cap-bg); color: var(--si-cap-ink); font-family: var(--si-font-mono); font-size: .86em; line-height: 1.6; animation: rs-out 420ms var(--si-ease-out) var(--d, 0ms) both; }
:deep(.rs-r) { animation: rs-in 420ms var(--si-ease-out) calc(var(--d, 0ms) + 50ms) both; }
@keyframes rs-out { to { transform: rotateX(90deg); opacity: 0; } }
@keyframes rs-in { from { transform: rotateX(-90deg); opacity: 0; background: color-mix(in srgb, var(--si-brand-soft) 60%, transparent); } }
</style>
