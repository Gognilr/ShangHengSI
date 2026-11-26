<script setup lang="ts">
import SiNotice from '@/components/ui/SiNotice.vue'
// 合同对比（V35-D-07；澄明二版 6.9）：侧栏“文件审查”下的独立页面 /compare（V351-E-01），文件和结果只在本页，不留历史。
// 入口是并排的两张纸；结果是两稿纸面并排，变化的段落之间用缎带缝起来，右栏是差异清单。
import { computed, nextTick, onBeforeUnmount, ref, shallowRef, watch } from 'vue'
import { ArrowLeft, Download, FilePlus, FileText, GitCompareArrows, RotateCcw } from '@lucide/vue'
import { ElMessage } from 'element-plus'
import { compareContracts, exportContractComparison, type CompareNode, type ContractChange, type ContractComparison } from '@/api/contractCompare'
import { ApiError } from '@/api/http'
import EmptyState from '@/components/EmptyState.vue'
import { useAuthStore } from '@/stores/auth'
import { contractChangeLabel } from '@/utils/labels'
import { useFx } from '@/utils/motion'
import { saveBlob } from '@/utils/save'

type Side = 'baseline' | 'revised'
const fx = useFx()
const auth = useAuthStore()
const baselinePicker = ref<HTMLInputElement>()
const revisedPicker = ref<HTMLInputElement>()
const pickers = { baseline: baselinePicker, revised: revisedPicker }
const files = { baseline: shallowRef<File | null>(null), revised: shallowRef<File | null>(null) }
const baseline = files.baseline
const revised = files.revised
const dragSide = ref<Side | null>(null)
const names = ref({ baseline: '', revised: '' })
const result = shallowRef<ContractComparison | null>(null)
const error = ref('')
const busy = ref(false)
const exporting = ref(false)
const firstSheet = ref<HTMLElement>()
let request: AbortController | undefined
onBeforeUnmount(() => request?.abort())

const LABEL: Record<Side, string> = { baseline: '基准稿', revised: '修改稿' }
function setFile(side: Side, file: File | undefined) {
  if (!file) return
  if (!/\.docx?$/i.test(file.name)) { files[side].value = null; error.value = `${LABEL[side]}只支持 DOCX 和 DOC 文件，请重新选择。`; return }
  files[side].value = file
  error.value = ''
}
function pick(event: Event, side: Side) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  setFile(side, file)
}
function drop(event: DragEvent, side: Side) { dragSide.value = null; if (!busy.value) setFile(side, event.dataTransfer?.files?.[0]) }
const ready = computed(() => !!baseline.value && !!revised.value)

async function start() {
  const old = baseline.value, current = revised.value
  if (!old || !current || busy.value) return
  const controller = new AbortController()
  request = controller
  busy.value = true
  error.value = ''
  try {
    const output = await compareContracts(old, current, controller.signal)
    if (controller.signal.aborted) return
    names.value = { baseline: old.name, revised: current.name }
    // 仅在本页内存中保留 File，以便导出时重新提交原件。
    selected.value = output.changes[0]?.id ?? ''
    result.value = output
  } catch (e) {
    if (!controller.signal.aborted) error.value = e instanceof ApiError ? e.message : '对比失败，请重新选择文件后重试。'
  } finally {
    request = undefined
    busy.value = false
  }
}
async function restart() {
  request?.abort()
  result.value = null
  baseline.value = null
  revised.value = null
  names.value = { baseline: '', revised: '' }
  error.value = ''
  await nextTick()
  firstSheet.value?.focus()
}

async function exportTable() {
  if (!baseline.value || !revised.value || exporting.value) return
  const controller = new AbortController()
  request = controller
  exporting.value = true
  error.value = ''
  try {
    const blob = await exportContractComparison(baseline.value, revised.value, controller.signal)
    if (!controller.signal.aborted && await saveBlob(blob, '合同对比表.docx') === 'downloaded') {
      ElMessage.success('对照表已下载')
    }
  } catch (e) {
    if (!controller.signal.aborted) error.value = e instanceof ApiError ? e.message : '导出失败，请稍后重试。'
  } finally {
    if (request === controller) request = undefined
    exporting.value = false
  }
}

/* ================= 结果：缝线 ================= */
type Kind = 'cm' | 'ca' | 'cd'
/** 缎带颜色是差异类型：琥珀改动（含位置调整、标点）、青新增、红删除 */
const kindOf = (c: ContractChange): Kind => c.type === 'add' ? 'ca' : c.type === 'del' ? 'cd' : 'cm'
const changes = computed(() => result.value?.changes ?? [])
const counts = computed(() => ({ cm: changes.value.filter(c => kindOf(c) === 'cm').length, ca: changes.value.filter(c => kindOf(c) === 'ca').length, cd: changes.value.filter(c => kindOf(c) === 'cd').length }))
const label = (c: ContractChange) => c.type === 'move' && c.segs.some(s => s.t !== 'eq')
  ? '位置与文字改动' : contractChangeLabel(c.detail_type ?? c.type)
const regions = { body: '', header: '页眉', footer: '页脚', textbox: '文本框' }
const omitted = computed(() => (['baseline', 'revised'] as const).flatMap(side => {
  const items = result.value?.[`${side}_omitted`]
  return items?.length ? [`${LABEL[side]}含${items.join('、')}，未对比`] : []
}))
const paperChanges = computed(() => changes.value.flatMap(c => [c, ...c.members.map(member => ({ ...c, ...member }))]))
const selected = ref('')

function traceMarks(c: ContractChange) {
  const pieces = [c, ...c.members].flatMap(item => item.segs)
  const style = (u: boolean) => c.visibility_segs.filter(s => s.u === u).map(s => `可见性「${s.s}」`)
  const hint = (u: boolean) => {
    const text = pieces.filter(s => s.u === u && (c.detail_type !== 'visibility' || s.t === 'ins'))
      .map(s => `${c.detail_type === 'visibility' ? '可见性' : s.t === 'del' ? '删' : '增'}「${s.s}」`)
    return [...text, ...style(u)].join('、')
  }
  return [
    ...(c.untracked === false || c.mixed ? [{ label: '已留修订痕迹', detail: hint(false), pending: false }] : []),
    ...(c.untracked === true ? [{ label: '未留痕', detail: hint(true), pending: true }] : []),
  ]
}
const segmentTrace = (s: ContractChange['segs'][number]) => s.u === undefined ? undefined : s.u ? '未留痕' : '已留修订痕迹'

interface Cell { node: CompareNode; change?: ContractChange }
type Block = { t: 'p'; cell: Cell } | { t: 'table'; key: string; rows: { r: number; cells: Cell[] }[] }
const TABLE_RE = /^(.*-T\d+)-R(\d+)-C(\d+)$/
function blocksOf(nodes: CompareNode[], side: Side): Block[] {
  const by = new Map(paperChanges.value.map(c => [side === 'baseline' ? c.baseline_loc : c.revised_loc, c] as const).filter(([loc]) => !!loc))
  const out: Block[] = []
  for (const node of nodes) {
    const cell: Cell = { node, change: by.get(node.loc) }
    const m = TABLE_RE.exec(node.loc), last = out[out.length - 1]
    if (!m) { out.push({ t: 'p', cell }); continue }
    if (last?.t === 'table' && last.key === m[1]) {
      const r = Number(m[2]), row = last.rows.find(x => x.r === r)
      if (row) row.cells.push(cell)
      else last.rows.push({ r, cells: [cell] })
    } else out.push({ t: 'table', key: m[1]!, rows: [{ r: Number(m[2]), cells: [cell] }] })
  }
  return out
}
const blocksA = computed(() => blocksOf(result.value?.baseline ?? [], 'baseline'))
const blocksB = computed(() => blocksOf(result.value?.revised ?? [], 'revised'))
/** 一侧的文字：基准稿不显示插入，修改稿不显示删除 */
const segsOf = (c: ContractChange, side: Side) => {
  const segments = c.segs.filter(s => side === 'baseline' ? s.t !== 'ins' : s.t !== 'del')
  return c.detail_type === 'visibility' ? segments.map(s => ({ ...s, t: 'eq' as const })) : segments
}

// ---- 缎带：随滚动重绘，requestAnimationFrame 节流，只画可见的部分（二版第五节） ----
const colA = ref<HTMLElement>(), colB = ref<HTMLElement>(), gut = ref<HTMLElement>()
const ribbons = ref<{ id: string; key: string; kind: Kind; d: string }[]>([])
const ribbonHidden = ref(false)
const reversePairs = computed(() => new Map(Object.entries(result.value?.pairs ?? {}).map(([a, b]) => [b, a])))
const nodeEl = (col: HTMLElement | undefined, loc: string | null) => loc ? col?.querySelector<HTMLElement>(`[data-loc="${CSS.escape(loc)}"]`) ?? null : null
/** 一侧的上下沿（相对缝线）；这一侧没有对应文字时收成一点 */
function span(c: ContractChange, side: Side, top: number): [number, number] | null {
  const col = side === 'baseline' ? colA.value : colB.value
  const own = nodeEl(col, side === 'baseline' ? c.baseline_loc : c.revised_loc)
  if (own) { const r = own.getBoundingClientRect(); return [r.top - top, r.bottom - top] }
  if (side === 'revised') {
    // 删除：挂在修改稿锚点段之前（文末时之后）
    const a = nodeEl(col, c.anchor)
    if (!a) return null
    const r = a.getBoundingClientRect(), y = (c.after ? r.bottom + 4 : r.top - 4) - top
    return [y, y]
  }
  // 新增：落在修改稿里前一个有对应的段落在基准稿中的位置之后
  const list = result.value?.revised ?? []
  for (let j = list.findIndex(n => n.loc === c.revised_loc) - 1; j >= 0; j--) {
    const el = nodeEl(col, reversePairs.value.get(list[j]!.loc) ?? null)
    if (el) { const y = el.getBoundingClientRect().bottom + 4 - top; return [y, y] }
  }
  const first = col?.querySelector<HTMLElement>('[data-loc]')
  if (!first) return null
  const y = first.getBoundingClientRect().top - 4 - top
  return [y, y]
}
function draw() {
  const g = gut.value
  if (!g || fx.value === 'off') { ribbons.value = []; return }
  const box = g.getBoundingClientRect(), W = box.width, H = box.height, mid = W / 2
  const out: typeof ribbons.value = []
  for (const c of paperChanges.value) {
    const a = span(c, 'baseline', box.top), b = span(c, 'revised', box.top)
    if (!a || !b) continue
    if (Math.max(a[1], b[1]) < -40 || Math.min(a[0], b[0]) > H + 40) continue
    out.push({ id: c.id, key: c.baseline_loc ?? c.revised_loc ?? c.id, kind: kindOf(c), d: `M0 ${a[0]}C${mid} ${a[0]} ${mid} ${b[0]} ${W} ${b[0]}L${W} ${b[1]}C${mid} ${b[1]} ${mid} ${a[1]} 0 ${a[1]}Z` })
  }
  ribbons.value = out
}
let raf = 0, idle: ReturnType<typeof setTimeout> | undefined
function onScroll() {
  if (fx.value === 'off') return
  if (fx.value === 'lite') {
    // 精简档：滚动时隐去，停止后再重绘
    ribbonHidden.value = true
    clearTimeout(idle)
    idle = setTimeout(() => { draw(); ribbonHidden.value = false }, 140)
    return
  }
  if (!raf) raf = requestAnimationFrame(() => { raf = 0; draw() })
}
let resize: ResizeObserver | undefined
watch([result, colA, colB], async () => {
  resize?.disconnect()
  await nextTick()
  if (!colA.value || !colB.value) return
  resize = new ResizeObserver(() => onScroll())
  resize.observe(colA.value); resize.observe(colB.value)
  draw()
})
watch(fx, () => draw())
onBeforeUnmount(() => { resize?.disconnect(); cancelAnimationFrame(raf); clearTimeout(idle) })

/** 点一条：两边纸面同时定位到这一处（没有对应文字的一侧定位到落点） */
function locate(c: ContractChange) {
  selected.value = c.id
  const behavior: ScrollBehavior = fx.value === 'off' ? 'auto' : 'smooth'
  for (const side of ['baseline', 'revised'] as const) {
    const col = side === 'baseline' ? colA.value : colB.value
    if (!col) continue
    const s = span(c, side, col.getBoundingClientRect().top)
    if (s) col.scrollTo({ top: col.scrollTop + s[0] - col.clientHeight / 3, behavior })
  }
}
const list = ref<HTMLElement>()
function step(delta: number) {
  const i = changes.value.findIndex(c => c.id === selected.value)
  const next = changes.value[Math.max(0, Math.min(changes.value.length - 1, i + delta))]
  if (!next) return
  locate(next)
  void nextTick(() => list.value?.querySelector<HTMLElement>(`[data-c="${next.id}"]`)?.focus())
}
</script>

<template>
  <!-- 入口：并排两张纸，两张都放好后中间亮起唯一主按钮 -->
  <section v-if="!result" class="compare-entry si-stagger">
    <header class="head">
      <h1 class="entry-title">合同对比</h1>
      <p class="entry-lede">把两稿并排放好，逐段找出改动、新增和删除。全程在本机比对，不留记录，离开即清。</p>
    </header>
    <SiNotice v-if="error" class="error" type="error" :title="error" :closable="false" />
    <div class="two-sheets" :class="{ ready }">
      <template v-for="side in (['baseline', 'revised'] as const)" :key="side">
        <div :ref="side === 'baseline' ? (el => { firstSheet = el as HTMLElement }) : undefined" class="sheet si-hov" :class="[side, { full: !!files[side].value, drag: dragSide === side }]"
          role="button" tabindex="0" :aria-label="`选择${LABEL[side]}`" :aria-disabled="busy"
          @click="!busy && pickers[side].value?.click()" @keydown.enter.self="!busy && pickers[side].value?.click()"
          @dragenter.prevent="dragSide = side" @dragover.prevent @dragleave.self="dragSide = null" @drop.prevent="drop($event, side)">
          <span class="sp"><component :is="files[side].value ? FileText : FilePlus" :size="26" :stroke-width="1.5" /></span>
          <b>{{ LABEL[side] }}</b>
          <span v-if="files[side].value" class="fname" :title="files[side].value!.name">{{ files[side].value!.name }}</span>
          <span v-else class="hint">{{ dragSide === side ? '松开即可放好' : side === 'baseline' ? '拖入或点击选择，通常是最初的版本' : '拖入或点击选择，通常是对方改过的版本' }}</span>
        </div>
        <div v-if="side === 'baseline'" class="mid">
          <span class="stitch" aria-hidden="true" />
          <el-button type="primary" size="large" :disabled="!ready || busy" :loading="busy" @click="start"><GitCompareArrows v-if="!busy" :size="16" :stroke-width="1.75" />{{ busy ? '正在本机对比' : '开始对比' }}</el-button>
          <span class="stitch" aria-hidden="true" />
        </div>
      </template>
    </div>
    <input ref="baselinePicker" type="file" accept=".docx,.doc" hidden @change="pick($event, 'baseline')">
    <input ref="revisedPicker" type="file" accept=".docx,.doc" hidden @change="pick($event, 'revised')">
    <p class="foot">支持 DOCX 和 DOC，对比正文、表格、页眉页脚和文本框。</p>
  </section>

  <!-- 结果：缝线 -->
  <section v-else class="compare-seam" aria-label="合同对比结果">
    <header class="wbar">
      <button type="button" class="ibtn" aria-label="重新对比" title="重新对比" @click="restart"><ArrowLeft :size="18" :stroke-width="1.75" /></button>
      <div class="ttl">
        <b :title="names.revised">{{ names.revised }}</b>
        <span>对照《{{ names.baseline }}》 · 不留记录，离开即清<template v-if="result.converted"> · DOC 已在本机转换，位置以转换后为准</template></span>
      </div>
      <div v-if="changes.length" class="legend" aria-label="缎带颜色说明">
        <span><i class="lg cm" />改动 <b class="num">{{ counts.cm }}</b></span>
        <span><i class="lg ca" />新增 <b class="num">{{ counts.ca }}</b></span>
        <span><i class="lg cd" />删除 <b class="num">{{ counts.cd }}</b></span>
        <span class="lg-note">缎带颜色是差异类型，不是严重程度</span>
      </div>
      <div class="actions">
        <el-button v-if="auth.can('use.export')" :loading="exporting" @click="exportTable"><Download v-if="!exporting" :size="15" :stroke-width="1.75" />{{ exporting ? '正在生成' : '导出对比表' }}</el-button>
        <el-button @click="restart"><RotateCcw :size="15" :stroke-width="1.75" />重新对比</el-button>
      </div>
    </header>
    <SiNotice v-if="error" class="result-notice" type="error" :title="error" :closable="false" />
    <div v-if="omitted.length" class="omitted" aria-label="未对比内容">
      <span v-for="line in omitted" :key="line">{{ line }}</span>
    </div>

    <div v-if="changes.length" class="seam">
      <div ref="colA" class="col" @scroll.passive="onScroll">
        <article class="si-paper paper">
          <p class="paper-h"><span>基准稿</span>{{ names.baseline }}</p>
          <template v-for="(b, i) in blocksA" :key="i">
            <p v-if="b.t === 'p'" class="node" :class="b.cell.change && ['chg', kindOf(b.cell.change), { on: b.cell.change.id === selected }]" :data-loc="b.cell.node.loc">
              <span v-if="regions[b.cell.node.part]" class="region">{{ regions[b.cell.node.part] }}</span>
              <template v-if="b.cell.change"><template v-for="(s, k) in segsOf(b.cell.change, 'baseline')" :key="k"><del v-if="s.t === 'del'" :title="segmentTrace(s)">{{ s.s }}</del><template v-else>{{ s.s }}</template></template></template>
              <template v-else>{{ b.cell.node.text }}</template>
            </p>
            <table v-else class="tbl"><tbody><tr v-for="row in b.rows" :key="row.r">
              <td v-for="c in row.cells" :key="c.node.loc" :data-loc="c.node.loc" :class="c.change && ['chg', kindOf(c.change), { on: c.change.id === selected }]">
                <template v-if="c.change"><template v-for="(s, k) in segsOf(c.change, 'baseline')" :key="k"><del v-if="s.t === 'del'" :title="segmentTrace(s)">{{ s.s }}</del><template v-else>{{ s.s }}</template></template></template>
                <template v-else>{{ c.node.text }}</template>
              </td>
            </tr></tbody></table>
          </template>
        </article>
      </div>
      <div ref="gut" class="gut" aria-hidden="true">
        <svg class="rib" :class="{ hide: ribbonHidden }"><path v-for="r in ribbons" :key="r.key" :class="[r.kind, { on: r.id === selected }]" :d="r.d" /></svg>
      </div>
      <div ref="colB" class="col" @scroll.passive="onScroll">
        <article class="si-paper paper">
          <p class="paper-h"><span>修改稿</span>{{ names.revised }}</p>
          <template v-for="(b, i) in blocksB" :key="i">
            <p v-if="b.t === 'p'" class="node" :class="b.cell.change && ['chg', kindOf(b.cell.change), { on: b.cell.change.id === selected }]" :data-loc="b.cell.node.loc">
              <span v-if="regions[b.cell.node.part]" class="region">{{ regions[b.cell.node.part] }}</span>
              <template v-if="b.cell.change"><template v-for="(s, k) in segsOf(b.cell.change, 'revised')" :key="k"><ins v-if="s.t === 'ins'" :title="segmentTrace(s)">{{ s.s }}</ins><template v-else>{{ s.s }}</template></template></template>
              <template v-else>{{ b.cell.node.text }}</template>
            </p>
            <table v-else class="tbl"><tbody><tr v-for="row in b.rows" :key="row.r">
              <td v-for="c in row.cells" :key="c.node.loc" :data-loc="c.node.loc" :class="c.change && ['chg', kindOf(c.change), { on: c.change.id === selected }]">
                <template v-if="c.change"><template v-for="(s, k) in segsOf(c.change, 'revised')" :key="k"><ins v-if="s.t === 'ins'" :title="segmentTrace(s)">{{ s.s }}</ins><template v-else>{{ s.s }}</template></template></template>
                <template v-else>{{ c.node.text }}</template>
              </td>
            </tr></tbody></table>
          </template>
        </article>
      </div>
      <aside class="dlist" aria-label="差异清单">
        <p class="dl-h"><b class="num">{{ changes.length }}</b> 处差异 · 点一条两边同时定位</p>
        <ol ref="list" @keydown.down.prevent="step(1)" @keydown.up.prevent="step(-1)">
          <li v-for="c in changes" :key="c.id">
            <button type="button" class="dli" :class="[kindOf(c), { on: c.id === selected }]" :data-c="c.id" :aria-pressed="c.id === selected" @click="locate(c)">
              <span class="bar" aria-hidden="true" />
              <span class="dl-b">
                <b>{{ c.revised_location || c.baseline_location }}</b>
                <span class="dl-meta">
                  <span class="dl-k">{{ label(c) }}</span>
                  <span v-for="mark in traceMarks(c)" :key="mark.label" class="trace" :class="{ pending: mark.pending }" :title="mark.detail || undefined">{{ mark.label }}</span>
                </span>
                <span v-if="c.brief.text" class="ex">{{ c.brief.text }}</span>
                <span v-else class="ex">{{ c.brief.ctx }}<del v-if="c.brief.old">{{ c.brief.old }}</del><span v-if="c.brief.old && c.brief.new" class="arrow"> → </span><ins v-if="c.brief.new">{{ c.brief.new }}</ins></span>
                <span v-if="c.visibility && c.detail_type !== 'visibility'" class="dl-k">{{ c.visibility }}</span>
                <span v-if="c.mixed && c.id === selected" class="trace-pieces">
                  <span v-for="mark in traceMarks(c)" :key="mark.label" :class="{ pending: mark.pending }">{{ mark.label }}<template v-if="mark.detail"> · {{ mark.detail }}</template></span>
                </span>
              </span>
            </button>
          </li>
        </ol>
        <p class="dl-f">按接受修订后的文字对比 · ↑ ↓ 切换</p>
      </aside>
    </div>
    <div v-else class="same">
      <EmptyState illus="pair" title="未发现差异" description="两份稿件在对比范围内一致。">
        <el-button @click="restart">重新对比</el-button>
      </EmptyState>
    </div>
  </section>
</template>

<style scoped>
/* ---- 入口：两张纸（内容宽 1040） ---- */
.compare-entry { max-width: 1040px; margin: 0 auto; padding: 48px 24px 64px; box-sizing: border-box; }
.head { margin-bottom: 8px; }
.entry-lede { margin-bottom: 0; }
.error { margin-top: 16px; }
.two-sheets { display: grid; grid-template-columns: minmax(0, 1fr) 150px minmax(0, 1fr); align-items: center; margin-top: 28px; }
.sheet { position: relative; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 10px; height: 300px; padding: 0 24px; border: 1px solid var(--si-hair); border-radius: var(--si-radius-sheet); background: var(--si-surface-1); box-shadow: var(--si-shadow-1); cursor: pointer; transition: transform var(--si-d3) var(--si-ease-out), box-shadow var(--si-d3) var(--si-ease-out), border-color var(--si-d3) var(--si-ease-out); }
.sheet:hover { transform: translateY(-3px); box-shadow: var(--si-shadow-2); }
.sheet:focus-visible { outline: 3px solid var(--si-ring); outline-offset: 2px; }
.sheet.drag { border-color: var(--si-brand-line); box-shadow: 0 0 0 4px var(--si-blue-tint), var(--si-shadow-2); }
.sp { display: grid; place-items: center; width: 120px; height: 150px; margin-bottom: 6px; box-sizing: border-box; border: 1.5px dashed var(--si-hair-strong); border-radius: var(--si-radius-paper); background: var(--si-card-2); color: var(--si-ink-4); transition: border-color var(--si-d3) var(--si-ease-out), background-color var(--si-d3) var(--si-ease-out), color var(--si-d3) var(--si-ease-out); }
.sheet.full .sp { border: 1.5px solid var(--si-m-review); background: var(--si-paper); color: var(--si-m-review); box-shadow: var(--si-paper-shadow); }
html[data-fx="full"] .sheet.full .sp { animation: sh-in var(--si-d3) var(--si-ease-spring); }
@keyframes sh-in { from { transform: translateY(-10px) rotate(-2deg); opacity: .4; } }
.sheet b { color: var(--si-ink-1); font: var(--si-font-h3); }
.sheet .hint { max-width: 240px; color: var(--si-ink-3); font: var(--si-font-meta); text-align: center; }
.fname { max-width: 100%; overflow: hidden; color: var(--si-ink-2); font: var(--si-font-label); text-overflow: ellipsis; white-space: nowrap; }
.mid { display: flex; flex-direction: column; align-items: center; gap: 12px; }
.mid :deep(.el-button > span) { gap: 6px; }
.stitch { width: 96px; height: 2px; background: repeating-linear-gradient(90deg, var(--si-hair-strong) 0 6px, transparent 6px 11px); transition: background var(--si-d3) var(--si-ease-out); }
.ready .stitch { background: repeating-linear-gradient(90deg, var(--si-brand) 0 6px, transparent 6px 11px); }
.foot { margin: 20px 0 0; color: var(--si-ink-3); font: var(--si-font-meta); text-align: center; }

/* ---- 结果：缝线（两稿 | 缝 64 | 差异清单 340） ---- */
.compare-seam { display: flex; flex-direction: column; height: 100%; min-height: 0; }
.wbar { flex: none; display: flex; align-items: center; gap: 14px; height: 64px; padding: 0 20px 0 16px; border-bottom: 1px solid var(--si-hair); background: var(--si-surface-1); }
.ibtn { display: grid; place-items: center; width: 34px; height: 34px; border: 0; border-radius: var(--si-radius-sm); background: transparent; color: var(--si-ink-2); cursor: pointer; }
.ibtn:hover { background: var(--si-hover-bg); color: var(--si-ink-1); }
.ttl { flex: 1; min-width: 0; display: flex; flex-direction: column; }
.ttl b { overflow: hidden; color: var(--si-ink-1); font: var(--si-font-h4); text-overflow: ellipsis; white-space: nowrap; }
.ttl span { overflow: hidden; color: var(--si-ink-3); font: var(--si-font-caption); text-overflow: ellipsis; white-space: nowrap; }
.wbar :deep(.el-button > span) { gap: 6px; }
.actions { display: flex; flex: none; }
.result-notice { flex: none; margin: 8px 16px; width: auto; }
.omitted { display: flex; flex: none; flex-wrap: wrap; gap: 4px 20px; padding: 8px 18px; border-bottom: 1px solid var(--si-hair); background: var(--si-warn-soft); color: var(--si-warn-ink); font: var(--si-font-caption); }
.legend { flex: none; display: flex; align-items: center; gap: 14px; color: var(--si-ink-3); font: var(--si-font-meta); }
.legend b { color: var(--si-ink-1); font-weight: 600; }
.lg { display: inline-block; width: 14px; height: 8px; margin-right: 6px; border-radius: 2px; vertical-align: middle; }
.lg.cm { background: color-mix(in srgb, var(--si-warn) 55%, transparent); }
.lg.ca { background: color-mix(in srgb, var(--si-brand-2) 55%, transparent); }
.lg.cd { background: color-mix(in srgb, var(--si-danger) 50%, transparent); }
.lg-note { color: var(--si-ink-4); }
@media (max-width: 1440px) { .lg-note { display: none; } }
.seam { flex: 1; min-height: 0; display: grid; grid-template-columns: minmax(0, 1fr) 64px minmax(0, 1fr) 340px; }
.col { min-width: 0; overflow-y: auto; padding: 24px 16px 48px; background: var(--si-sunken); }
/* 缎带要贴着纸边：靠缝线一侧不留白，滚动条放在外侧 */
.col:first-child { padding-right: 0; direction: rtl; }
.col:first-child > .paper { direction: ltr; }
.col:nth-child(3) { padding-left: 0; }
.paper { max-width: none; padding: 32px 36px 44px; border-radius: var(--si-radius-paper); box-shadow: var(--si-paper-shadow); font: var(--si-font-reading); }
.paper-h { display: flex; align-items: center; gap: 8px; margin: 0 0 18px; padding-bottom: 12px; overflow: hidden; border-bottom: 1px solid var(--si-hair-faint); color: var(--si-paper-ink-2); font: var(--si-font-caption); text-overflow: ellipsis; white-space: nowrap; }
.paper-h span { flex: none; padding: 0 8px; border-radius: var(--si-radius-badge); background: var(--si-hover-bg); color: var(--si-ink-2); font: var(--si-font-badge); line-height: 22px; }
.node { margin: 0 0 10px; white-space: pre-wrap; overflow-wrap: anywhere; }
.region { display: inline-block; margin-inline-end: 8px; padding: 0 6px; border: 1px solid var(--si-hair); border-radius: var(--si-radius-tag); font: var(--si-font-caption); color: var(--si-paper-ink-2); }
.tbl { width: 100%; margin: 10px 0; border-collapse: collapse; font-size: calc(14px * var(--si-fs)); }
.tbl td { padding: 6px 8px; border: 1px solid var(--si-hair-strong); vertical-align: top; white-space: pre-wrap; overflow-wrap: anywhere; }
/* 变化的段落：浅底；段内的删除划线、新增下划线 */
.chg { --cc: var(--si-warn); border-radius: var(--si-radius-tag); background: color-mix(in srgb, var(--cc) 12%, transparent); box-shadow: -6px 0 0 color-mix(in srgb, var(--cc) 12%, transparent), 6px 0 0 color-mix(in srgb, var(--cc) 12%, transparent); transition: box-shadow var(--si-d2) var(--si-ease-out); }
.chg.ca { --cc: var(--si-brand-2); }
.chg.cd { --cc: var(--si-danger); }
.chg.on { box-shadow: -6px 0 0 color-mix(in srgb, var(--cc) 12%, transparent), 6px 0 0 color-mix(in srgb, var(--cc) 12%, transparent), 0 0 0 2px var(--cc); }
td.chg { box-shadow: inset 0 0 0 999px color-mix(in srgb, var(--cc) 12%, transparent); background: none; }
td.chg.on { box-shadow: inset 0 0 0 999px color-mix(in srgb, var(--cc) 12%, transparent), inset 0 0 0 2px var(--cc); }
.paper del, .ex del { background: var(--si-danger-soft); color: var(--si-danger-ink); text-decoration: line-through; text-decoration-color: color-mix(in srgb, var(--si-danger) 60%, transparent); }
.paper ins, .ex ins { background: var(--si-blue-tint); color: var(--si-blue-ink); text-decoration: underline; text-underline-offset: 3px; }
.chg.cd del { background: none; }
.chg.ca ins { background: none; }
.gut { position: relative; background: var(--si-sunken); }
.rib { position: absolute; inset: 0; width: 100%; height: 100%; overflow: visible; pointer-events: none; transition: opacity var(--si-d2) var(--si-ease-out); }
.rib.hide { opacity: 0; }
.rib path { opacity: .32; transition: opacity var(--si-d2) var(--si-ease-out); }
.rib path.cm { fill: var(--si-warn); }
.rib path.ca { fill: var(--si-brand-2); }
.rib path.cd { fill: var(--si-danger); }
.rib path.on { opacity: .7; }
.dlist { display: flex; flex-direction: column; min-height: 0; border-left: 1px solid var(--si-hair); background: var(--si-surface-1); }
.dl-h { flex: none; margin: 0; padding: 14px 18px 10px; color: var(--si-ink-3); font: var(--si-font-meta); }
.dl-h b { color: var(--si-ink-1); font-weight: 600; }
.dlist ol { flex: 1; min-height: 0; margin: 0; padding: 0 10px 10px; overflow-y: auto; list-style: none; }
.dli { display: flex; gap: 10px; width: 100%; padding: 10px 10px; border: 0; border-radius: var(--si-radius-md); background: transparent; color: inherit; text-align: left; cursor: pointer; }
.dli:hover { background: var(--si-hover-bg); }
.dli.on { background: var(--si-select-bg); }
.dli:focus-visible { outline: 2px solid var(--si-focus); outline-offset: -2px; }
.bar { flex: none; width: 4px; border-radius: 2px; background: var(--si-warn); }
.dli.ca .bar { background: var(--si-brand-2); }
.dli.cd .bar { background: var(--si-danger); }
.dl-b { min-width: 0; display: flex; flex-direction: column; gap: 2px; }
.dl-b b { color: var(--si-ink-1); font: var(--si-font-label); font-weight: 600; }
.dl-k { color: var(--si-ink-3); font: var(--si-font-caption); }
.dl-meta { display: flex; align-items: center; flex-wrap: wrap; gap: 4px 7px; }
.trace { padding: 0 6px; border-radius: var(--si-radius-badge); background: var(--si-surface-2); color: var(--si-ink-2); font: var(--si-font-caption); }
.trace.pending { background: var(--si-warn-soft); color: var(--si-warn-ink); }
.trace-pieces { display: grid; gap: 2px; color: var(--si-ink-2); font: var(--si-font-caption); overflow-wrap: anywhere; }
.trace-pieces .pending { color: var(--si-warn-ink); }
.ex { display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 2; overflow: hidden; overflow-wrap: anywhere; color: var(--si-ink-2); font: var(--si-font-meta); }
.arrow { color: var(--si-ink-3); }
.dl-f { flex: none; margin: 0; padding: 10px 18px 14px; border-top: 1px solid var(--si-hair-faint); color: var(--si-ink-4); font: var(--si-font-caption); }
.same { flex: 1; display: grid; place-items: center; }
@media (max-width: 1200px) { .seam { grid-template-columns: minmax(0, 1fr) 40px minmax(0, 1fr) 280px; } }
</style>
