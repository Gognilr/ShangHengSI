<script setup lang="ts">
import { ElMessageBox } from 'element-plus'
import { computed, h, nextTick, reactive, ref, watch, type FunctionalComponent, type VNode } from 'vue'
import { ApiError } from '@/api/http'
import { previewApi, type PreviewNode, type PreviewNodes } from '@/api/preview'
import { REDACTION_ERROR_MESSAGES, redactionApi, type RedactionInfo, type RedactionItemPos, type Suspect, type SuspectCategory, type UserCategory } from '@/api/redaction'
import type { TaskFile } from '@/api/tasks'
import SiSegmented from '@/components/ui/SiSegmented.vue'
import { readTextSelection, type TextSelection } from '@/composables/useTextSelection'
import { MANUAL_CATEGORY_OPTIONS, REDACTION_CATEGORY, toOptions } from '@/utils/labels'
import { useFx } from '@/utils/motion'

/**
 * 单画布“原文 / AI 视野”（V31-U2 第四节）：一张纸面显示当前文件的原文，替换处为胶囊；
 * AI 视野显示模型将看到的代号。所有原文与代号都按纯文本渲染。
 */
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
}>()
const emit = defineEmits<{
  done: [info: RedactionInfo, action: 'add' | 'revoke']
  busy: [value: boolean]
  stale: []
  keep: [s: Suspect]
  restore: [s: Suspect]
  replace: [s: Suspect, category?: SuspectCategory]
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
const view = ref<'orig' | 'ai'>('orig')
const viewOptions = [{ value: 'orig', label: '原文' }, { value: 'ai', label: 'AI 视野' }]

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
interface Seg { text: string; k: Kind; item?: RedactionItemPos; sid?: number; i: number }
interface NodeView { node: PreviewNode; segs: Seg[]; changed: boolean }
type Block =
  | { t: 'node'; nv: NodeView }
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
    const nv: NodeView = { node, segs, changed: segs.some(s => s.k !== 'plain') }
    const pos = cellPos(node)
    const last = flat[flat.length - 1]
    if (pos) {
      if (last?.t === 'table' && last.key === pos.key) {
        const row = last.rows.find(r => r.r === pos.r)
        if (row) row.cells.push(nv)
        else last.rows.push({ r: pos.r, cells: [nv] })
      } else flat.push({ t: 'table', key: pos.key, rows: [{ r: pos.r, cells: [nv] }] })
    } else flat.push({ t: 'node', nv })
  }
  if (nodes.length <= FOLD_LIMIT || unfolded.value.has(activeId.value)) return flat
  // 长文档：只显示有替换或保留文字的块，其余折叠
  const changed = (b: Block) => b.t === 'node' ? b.nv.changed : b.t === 'table' ? b.rows.some(r => r.cells.some(c => c.changed)) : false
  const out: Block[] = []
  let run = 0
  let runId = ''
  const close = () => { if (run) { out.push({ t: 'fold', id: runId, n: run }); run = 0 } }
  for (const b of flat) {
    if (changed(b)) { close(); out.push(b) } else {
      if (!run) runId = b.t === 'node' ? b.nv.node.loc : b.t === 'table' ? b.key : ''
      run += b.t === 'table' ? b.rows.reduce((s, r) => s + r.cells.length, 0) : 1
    }
  }
  close()
  return out
})

// ---- 渲染：原文 / AI 视野 ----
const selectedPlaceholder = computed(() => props.suspects.find(s => s.id === props.selectedSuspectId)?.placeholder ?? null)
const NodeText: FunctionalComponent<{ nv: NodeView; ai: boolean; selId: number | null; selPh: string | null }> = ({ nv, ai, selId, selPh }) => {
  const kids: Array<VNode | string> = nv.segs.map((g) => {
    if (g.k === 'plain') return g.text
    const selected = g.k === 'kept' ? g.sid === selId : !!selPh && g.item?.placeholder === selPh
    const attrs: Record<string, unknown> = { 'data-seg': g.k, style: { '--i': Math.min(g.i, 40) }, class: [g.k, { sel: selected }] }
    if (g.item) attrs['data-item'] = `${g.item.loc}|${g.item.char_start}|${g.item.char_end}`
    if (g.sid !== undefined) attrs['data-sid'] = g.sid
    if (g.k === 'kept') return h('span', attrs, g.text)
    if (ai) { attrs.class = [g.k === 'unc' ? 'code unc' : 'code', { sel: selected }]; return h('span', attrs, g.item!.placeholder) }
    attrs.title = g.k === 'unc' ? `不确定是否敏感，已默认替换为 ${g.item!.placeholder}` : `${REDACTION_CATEGORY[g.item!.category] ?? g.item!.category} · 发送为 ${g.item!.placeholder}`
    return h('span', attrs, g.text)
  })
  return h('span', { class: ai ? 'ai-txt' : 'txt', 'data-loc': nv.node.loc }, kids)
}
NodeText.props = ['nv', 'ai', 'selId', 'selPh']

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
  emit('busy', true)
  try {
    emit('done', await redactionApi.revoke(props.taskId, { loc: item.loc, char_start: item.char_start, char_end: item.char_end }), 'revoke')
  } catch (e) {
    pickError.value = e instanceof ApiError ? REDACTION_ERROR_MESSAGES[e.code] ?? e.message : '撤销失败，请重试'
    if (e instanceof ApiError && e.code === 'TASK_STATE_CONFLICT') emit('stale')
  } finally { emit('busy', false) }
}

// ---- 划选补充脱敏 ----
const pick = ref<TextSelection | null>(null)
const pickPos = ref({ left: 0, top: 0 })
const pickNotice = ref('')
const pickError = ref('')
const pickCategory = ref<UserCategory>('ORG')
const pickSaving = ref(false)
function clearPick() { pick.value = null; pickNotice.value = ''; pickError.value = '' }
watch(activeId, id => { clearPick(); closePop(); void loadFile(id) }, { immediate: true })
function onPaperMouseUp() {
  if (view.value !== 'orig' || pickSaving.value) return
  const result = readTextSelection(itemsByLoc.value)
  clearPick()
  if (!result || !wrap.value) return
  const outer = wrap.value.getBoundingClientRect()
  if (result.notice) { pickNotice.value = result.notice; pickPos.value = { left: 12, top: 8 }; return }
  const s = result.selection!
  pick.value = s
  pickPos.value = { left: Math.max(8, Math.min(s.rect.left - outer.left, outer.width - 420)), top: Math.max(4, s.rect.top - outer.top - 48) }
}
async function submitPick() {
  const s = pick.value
  if (!s || props.locked) return
  pickSaving.value = true
  pickError.value = ''
  emit('busy', true)
  try {
    const info = await redactionApi.manual(props.taskId, { loc: s.loc, char_start: s.start, char_end: s.end, category: pickCategory.value })
    window.getSelection()?.removeAllRanges()
    clearPick()
    emit('done', info, 'add')
  } catch (e) {
    pickError.value = e instanceof ApiError ? REDACTION_ERROR_MESSAGES[e.code] ?? e.message : '操作失败，请重试'
    if (e instanceof ApiError && e.code === 'TASK_STATE_CONFLICT') emit('stale')
  } finally { pickSaving.value = false; emit('busy', false) }
}

// ---- 定位（4.4） ----
async function locate(loc: string, text?: string) {
  const file = loc === 'NOTE' ? sortedFiles.value[sortedFiles.value.length - 1] : sortedFiles.value.find(f => loc.startsWith(`F${f.seq}-`))
  if (!file) return
  if (view.value !== 'orig') view.value = 'orig'
  activeId.value = file.id
  await loadFile(file.id)
  unfolded.value = new Set(unfolded.value).add(file.id)
  await nextTick()
  const box = scroller.value
  const node = box?.querySelector<HTMLElement>(`[data-node="${CSS.escape(loc)}"]`)
  if (!box || !node) return
  box.scrollTop += node.getBoundingClientRect().top - box.getBoundingClientRect().top - 80
  const candidates = Array.from(node.querySelectorAll<HTMLElement>('[data-seg]'))
  const target = (text ? candidates.find(el => el.textContent === text) : undefined) ?? candidates[0] ?? node
  target.classList.add('flash')
  setTimeout(() => target.classList.remove('flash'), 1600)
}
function switchFile(delta: 1 | -1) {
  const list = sortedFiles.value
  const index = list.findIndex(f => f.id === activeId.value)
  if (list.length > 1 && index >= 0) activeId.value = list[(index + delta + list.length) % list.length]!.id
}
function toggleView() { view.value = view.value === 'orig' ? 'ai' : 'orig' }
function cancel() { closePop(); clearPick(); window.getSelection()?.removeAllRanges() }
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
    </div>

    <div ref="scroller" v-loading="active?.loading" class="stage si-stage" @scroll.passive="closePop">
      <el-alert v-if="active?.error" type="error" show-icon :closable="false" :title="active.error" />
      <el-empty v-else-if="active && !active.loading && !active.available" description="原文已按保留期清理" />
      <el-empty v-else-if="active && !active.loading && !blocks.length" description="没有可显示的正文" />
      <div v-else class="si-paper paper" :class="[{ flipping }, `fx-${fx}`, { ai: view === 'ai' }]" :key="view === 'ai' && fx === 'lite' ? 'a' : 'o'" @click="onPaperClick" @mouseup="onPaperMouseUp">
        <template v-for="(b, bi) in blocks" :key="bi">
          <div v-if="b.t === 'node'" class="node" :class="[`kind-${b.nv.node.kind}`, { head: b.nv.node.kind === 'heading', note: b.nv.node.loc === 'NOTE', side: b.nv.node.part !== 'body' }]" :data-node="b.nv.node.loc">
            <div v-if="b.nv.node.loc === 'NOTE'" class="side-label">本次补充说明</div>
            <span v-else-if="b.nv.node.part !== 'body'" class="side-label">{{ b.nv.node.part }}</span>
            <span v-if="b.nv.node.clause_no && b.nv.node.part === 'body'" class="clause">{{ b.nv.node.clause_no }}</span>
            <NodeText :nv="b.nv" :ai="view === 'ai'" :sel-id="selectedSuspectId" :sel-ph="selectedPlaceholder" />
          </div>
          <table v-else-if="b.t === 'table'" class="tbl">
            <tbody><tr v-for="row in b.rows" :key="row.r"><td v-for="c in row.cells" :key="c.node.loc" :data-node="c.node.loc"><NodeText :nv="c" :ai="view === 'ai'" :sel-id="selectedSuspectId" :sel-ph="selectedPlaceholder" /></td></tr></tbody>
          </table>
          <button v-else type="button" class="fold" @click="unfolded = new Set(unfolded).add(activeId)">{{ b.n }} 段未改动 · 展开</button>
        </template>
      </div>
    </div>

    <p class="cv-foot hint">{{ view === 'orig' ? `${protectedCount} 处已在本机保护 · 划选文字可补充` : '这是模型将看到的样子 · 代号在本机还原' }}</p>

    <div v-if="pop" class="pop" :style="{ left: `${pop.left}px`, top: `${pop.top}px` }" role="dialog" @click.stop>
      <template v-if="pop.suspect">
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

    <div v-if="pick || pickNotice || pickError" class="pickbar" :style="{ left: `${pickPos.left}px`, top: `${pickPos.top}px` }">
      <template v-if="pick">
        <span class="pick-label">补充脱敏</span>
        <el-select v-model="pickCategory" size="small" class="pick-cat" aria-label="类别"><el-option v-for="o in categoryOptions" :key="o.value" :label="o.label" :value="o.value" /></el-select>
        <el-button type="primary" size="small" :loading="pickSaving" :disabled="locked" @click="submitPick">设为敏感信息</el-button>
        <span v-if="locked" class="pick-note">正在重新计算，请稍候</span>
      </template>
      <span v-if="pickNotice" class="pick-note">{{ pickNotice }}</span>
      <span v-if="pickError" class="pick-err">{{ pickError }}</span>
    </div>
  </div>
</template>

<style scoped>
.cv { position: relative; flex: 1; min-height: 0; display: flex; flex-direction: column; gap: 8px; }
.cv-bar { flex: none; display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.spacer { flex: 1; }
.tabs { flex: 1; min-width: 0; display: flex; gap: 4px; overflow-x: auto; }
.tab { flex: none; max-width: 220px; display: flex; align-items: center; gap: 6px; height: 28px; padding: 0 10px; border: 0; border-radius: 8px; background: transparent; color: var(--si-ink-3); font: var(--si-font-label); cursor: pointer; }
.tab:hover { color: var(--si-ink-1); }
.tab.on { background: var(--si-surface-2); color: var(--si-ink-1); }
.tab:focus-visible { outline: 2px solid var(--si-focus); outline-offset: 1px; }
.tab-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tab-n { flex: none; padding: 0 6px; border-radius: 999px; background: var(--si-cap-bg); color: var(--si-cap-ink); font: var(--si-font-label-sm); }
.stage { flex: 1; min-height: 0; overflow-y: auto; padding: 20px; border-radius: var(--si-radius-card); }
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

.pop, .pickbar { position: absolute; z-index: 20; display: flex; flex-wrap: wrap; align-items: center; gap: 8px; max-width: 420px; padding: 8px 10px; border-radius: 10px; background: var(--si-surface-pop); box-shadow: 0 0 0 1px var(--si-hair-strong), var(--si-shadow-2, 0 8px 24px rgba(0, 0, 0, .18)); }
.pop { width: 268px; }
.pop-text { flex: 1 0 100%; margin: 0; overflow-wrap: anywhere; font: var(--si-font-body); }
.code-tag { padding: 0 6px; border-radius: 4px; background: var(--si-code-bg); color: var(--si-code-ink); font-family: var(--si-font-mono); font-size: 12px; }
.pick-label { font: var(--si-font-label-sm); color: var(--si-ink-3); }
.pick-cat { width: 110px; }
.pick-note { color: var(--el-color-warning); font: var(--si-font-caption); }
.pick-err { color: var(--el-color-danger); font: var(--si-font-caption); }
</style>
