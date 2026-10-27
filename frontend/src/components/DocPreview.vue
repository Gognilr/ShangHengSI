<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import type { PDFDocumentLoadingTask, PDFDocumentProxy } from 'pdfjs-dist'
import pdfWorkerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'
import { ApiError } from '@/api/http'
import { previewApi, type PreviewNode } from '@/api/preview'
import type { TaskFile } from '@/api/tasks'

/**
 * 原文预览（设计 M3 8.2）：DOCX、XLSX 按节点渲染，PDF 用 pdfjs 逐页渲染，图片用 img。
 * 每个节点的元素带 data-loc；highlight 中的 loc 加 is-highlight 类，并在预览容器内滚动到第一个。
 * 原件一律先取 Blob（PDF 转 ArrayBuffer，图片用 object URL），不把接口地址交给 pdfjs 或 img。
 */
const props = defineProps<{ taskId: string; files: TaskFile[]; highlight: string[] }>()

interface PdfPage {
  num: number
  w: number
  h: number
}

interface FileState {
  loading: boolean
  error: string
  nodes: PreviewNode[]
  textAvailable: boolean
  rawDeleted: boolean
  rawError: string
  imageUrl: string
  imgW: number
  imgH: number
  pdfPages: PdfPage[]
}

const PART_LABEL: Record<string, string> = {
  header: '页眉',
  footer: '页脚',
  comment: '批注',
  textbox: '文本框',
}

const states = reactive<Record<string, FileState>>({})
const pdfDocs = new Map<string, PDFDocumentProxy>() // 不放进响应式状态
const pdfTasks: PDFDocumentLoadingTask[] = []
const rendered = new Set<string>()
const body = ref<HTMLElement | null>(null)
const viewWidth = ref(600)
const sheetKey = ref<Record<string, string>>({})

const sortedFiles = computed(() =>
  [...props.files].sort((a, b) => Number(b.is_main) - Number(a.is_main) || a.seq - b.seq),
)
const activeId = ref(sortedFiles.value[0]?.id ?? '')
const activeFile = computed(() => sortedFiles.value.find((f) => f.id === activeId.value))
const state = computed(() => states[activeId.value])
const kind = computed(() => {
  const ext = (activeFile.value?.ext ?? '').toLowerCase().replace(/^\./, '')
  if (ext === 'docx' || ext === 'xlsx' || ext === 'pdf') return ext
  if (ext === 'doc' || ext === 'wps' || ext === 'rtf') return 'docx' // 旧格式经转换后按 DOCX 节点渲染
  if (ext === 'xls' || ext === 'et') return 'xlsx'
  return ext === 'jpg' || ext === 'jpeg' || ext === 'png' ? 'image' : 'other'
})
const hl = computed(() => new Set(props.highlight))
const showFallback = computed(
  () => !!state.value && (kind.value === 'pdf' || kind.value === 'image') && !state.value.pdfPages.length && !state.value.imageUrl,
)

function errMessage(e: unknown): string {
  if (e instanceof ApiError) return e.status === 404 ? '任务不存在或已被删除' : e.message
  return '加载失败，请刷新页面'
}

async function loadFile(file: TaskFile) {
  if (states[file.id]) return
  const st = reactive<FileState>({
    loading: true,
    error: '',
    nodes: [],
    textAvailable: true,
    rawDeleted: false,
    rawError: '',
    imageUrl: '',
    imgW: 0,
    imgH: 0,
    pdfPages: [],
  })
  states[file.id] = st
  try {
    const res = await previewApi.nodes(props.taskId, file.id)
    st.nodes = res.items
    st.textAvailable = res.text_available
  } catch (e) {
    st.error = errMessage(e)
  }
  const ext = file.ext.toLowerCase().replace(/^\./, '')
  const isPdf = ext === 'pdf'
  if (isPdf || ext === 'jpg' || ext === 'jpeg' || ext === 'png') {
    try {
      const blob = await previewApi.raw(props.taskId, file.id)
      if (isPdf) await openPdf(file.id, st, await blob.arrayBuffer())
      else st.imageUrl = URL.createObjectURL(blob)
    } catch (e) {
      if (e instanceof ApiError && e.code === 'ORIGINAL_DELETED') st.rawDeleted = true
      else st.rawError = e instanceof ApiError ? e.message : 'PDF 无法显示，请刷新页面'
    }
  }
  st.loading = false
  await nextTick()
  afterLayout()
}

async function openPdf(fileId: string, st: FileState, data: ArrayBuffer) {
  const pdfjs = await import('pdfjs-dist')
  pdfjs.GlobalWorkerOptions.workerSrc = pdfWorkerUrl
  // pdfjs-dist 6 已不使用 eval，没有 isEvalSupported 选项
  const task = pdfjs.getDocument({ data })
  pdfTasks.push(task)
  const doc = await task.promise
  pdfDocs.set(fileId, doc)
  const pages: PdfPage[] = []
  for (let n = 1; n <= doc.numPages; n++) {
    const vp = (await doc.getPage(n)).getViewport({ scale: 1 }) // 页面自身旋转后的显示方向，与 bbox 一致
    pages.push({ num: n, w: vp.width, h: vp.height })
  }
  st.pdfPages = pages
}

// ---- PDF：只渲染进入可视区域的页 ----
let observer: IntersectionObserver | null = null

function observePages() {
  observer?.disconnect()
  observer = null
  rendered.clear()
  const root = body.value
  if (!root || kind.value !== 'pdf') return
  observer = new IntersectionObserver(
    (entries) => {
      for (const en of entries) {
        if (en.isIntersecting) void renderPage(activeId.value, Number((en.target as HTMLElement).dataset.page))
      }
    },
    { root, rootMargin: '200px 0px' },
  )
  root.querySelectorAll('.pdf-page').forEach((el) => observer?.observe(el))
}

async function renderPage(fileId: string, n: number) {
  const key = `${fileId}:${n}`
  if (rendered.has(key)) return
  rendered.add(key)
  try {
    const doc = pdfDocs.get(fileId)
    const canvas = body.value?.querySelector<HTMLCanvasElement>(`canvas[data-page="${n}"]`)
    if (!doc || !canvas) throw new Error('canvas missing')
    const page = await doc.getPage(n)
    const base = page.getViewport({ scale: 1 })
    const scale = (Math.max(viewWidth.value, 300) / base.width) * Math.min(window.devicePixelRatio || 1, 2)
    const viewport = page.getViewport({ scale })
    canvas.width = Math.floor(viewport.width)
    canvas.height = Math.floor(viewport.height)
    await page.render({ canvas, viewport }).promise
  } catch {
    rendered.delete(key) // 切换文件或卸载时取消；再次进入可视区会重试
  }
}

// ---- 高亮与滚动 ----
let needScroll = false

function tryScroll() {
  const root = body.value
  if (!needScroll || !root) return
  const el = root.querySelector<HTMLElement>('.is-highlight')
  if (!el) return
  needScroll = false
  const r = root.getBoundingClientRect()
  const e = el.getBoundingClientRect()
  root.scrollTo({ top: root.scrollTop + (e.top - r.top) - (r.height - e.height) / 2, behavior: 'smooth' })
}

function afterLayout() {
  if (kind.value === 'pdf') observePages()
  tryScroll()
}

function applyHighlight() {
  const first = props.highlight[0]
  needScroll = !!first
  if (!first) return
  const m = /^F(\d+)-/.exec(first)
  const target = m ? sortedFiles.value.find((f) => f.seq === Number(m[1])) : undefined
  if (target && target.id !== activeId.value) {
    activeId.value = target.id // 切换文件后，加载完成时继续滚动
    return
  }
  const node = state.value?.nodes.find((n) => n.loc === first)
  if (node?.sheet != null || /-S\d+-/.test(first)) {
    const s = /-S(\d+)-/.exec(first)
    if (s) sheetKey.value = { ...sheetKey.value, [activeId.value]: s[1] }
  }
  void nextTick(tryScroll)
}

watch(() => props.highlight, applyHighlight)
watch(
  activeId,
  async () => {
    const f = activeFile.value
    if (!f) return
    await loadFile(f)
    await nextTick()
    applyHighlight()
    afterLayout()
  },
  { immediate: true },
)
watch(
  () => props.files,
  () => {
    if (!activeFile.value) activeId.value = sortedFiles.value[0]?.id ?? ''
  },
)

// ---- DOCX ----
type DocItem =
  | { type: 'p'; node: PreviewNode }
  | { type: 'table'; key: string; rows: number[]; cols: number[]; cells: Map<string, PreviewNode> }

const CELL_RE = /^(.*-T\d+)-R(\d+)-C(\d+)$/

const docxItems = computed<DocItem[]>(() => {
  const items: DocItem[] = []
  const tables = new Map<string, Extract<DocItem, { type: 'table' }>>()
  for (const n of state.value?.nodes ?? []) {
    if (n.part !== 'body') continue
    const m = n.kind === 'table_cell' ? CELL_RE.exec(n.loc) : null
    if (!m) {
      items.push({ type: 'p', node: n })
      continue
    }
    let t = tables.get(m[1])
    if (!t) {
      t = { type: 'table', key: m[1], rows: [], cols: [], cells: new Map() }
      tables.set(m[1], t)
      items.push(t)
    }
    const r = Number(m[2])
    const c = Number(m[3])
    if (!t.rows.includes(r)) t.rows.push(r)
    if (!t.cols.includes(c)) t.cols.push(c)
    t.cells.set(`${r},${c}`, n)
  }
  for (const t of tables.values()) {
    const maxR = Math.max(...t.rows)
    const maxC = Math.max(...t.cols)
    t.rows = Array.from({ length: maxR }, (_, i) => i + 1)
    t.cols = Array.from({ length: maxC }, (_, i) => i + 1)
  }
  return items
})

const extraNodes = computed(() => (state.value?.nodes ?? []).filter((n) => n.part !== 'body' && n.part !== 'hidden_sheet'))

// ---- XLSX ----
interface Sheet {
  key: string
  label: string
  hidden: boolean
  cols: number[]
  rows: { row: number; cells: Map<number, PreviewNode> }[]
}

function colToNum(letters: string): number {
  let n = 0
  for (const ch of letters) n = n * 26 + (ch.charCodeAt(0) - 64)
  return n
}

function numToCol(num: number): string {
  let s = ''
  for (let n = num; n > 0; n = Math.floor((n - 1) / 26)) s = String.fromCharCode(65 + ((n - 1) % 26)) + s
  return s
}

const sheets = computed<Sheet[]>(() => {
  const map = new Map<string, Sheet & { rowMap: Map<number, Map<number, PreviewNode>> }>()
  for (const n of state.value?.nodes ?? []) {
    const k = /-S(\d+)-/.exec(n.loc)?.[1]
    const ref = /^([A-Z]+)(\d+)$/.exec(n.cell_ref ?? '')
    if (!k || !ref) continue
    let s = map.get(k)
    if (!s) {
      s = { key: k, label: n.sheet ?? `工作表 ${k}`, hidden: false, cols: [], rows: [], rowMap: new Map() }
      map.set(k, s)
    }
    if (n.part === 'hidden_sheet') s.hidden = true
    const row = Number(ref[2])
    const c = colToNum(ref[1])
    if (!s.rowMap.has(row)) s.rowMap.set(row, new Map())
    s.rowMap.get(row)!.set(c, n)
  }
  return [...map.values()].map((s) => {
    const used = [...s.rowMap.values()].flatMap((r) => [...r.keys()])
    const lo = Math.min(...used)
    const hi = Math.max(...used)
    return {
      key: s.key,
      label: s.hidden ? `${s.label}（隐藏）` : s.label,
      hidden: s.hidden,
      cols: Array.from({ length: hi - lo + 1 }, (_, i) => lo + i),
      rows: [...s.rowMap.entries()].sort((a, b) => a[0] - b[0]).map(([row, cells]) => ({ row, cells })),
    }
  })
})
const currentSheetKey = computed({
  get: () => sheetKey.value[activeId.value] ?? sheets.value[0]?.key ?? '',
  set: (v: string) => {
    sheetKey.value = { ...sheetKey.value, [activeId.value]: v }
  },
})
const currentSheet = computed(() => sheets.value.find((s) => s.key === currentSheetKey.value))

// ---- PDF / 图片的高亮框 ----
function pct(v: number, total: number): string {
  return `${(v / total) * 100}%`
}

function boxStyle(bbox: number[], w: number, h: number) {
  const [x0, top, x1, bottom] = bbox as [number, number, number, number]
  return { left: pct(x0, w), top: pct(top, h), width: pct(x1 - x0, w), height: pct(bottom - top, h) }
}

const hlNodes = computed(() => (state.value?.nodes ?? []).filter((n) => hl.value.has(n.loc) && n.bbox))

function nodeLabel(n: PreviewNode): string {
  return n.page != null ? `第 ${n.page} 页` : n.cell_ref ? `${n.sheet ?? ''} ${n.cell_ref}`.trim() : ''
}

function onImgLoad(e: Event) {
  const img = e.target as HTMLImageElement
  const st = state.value
  if (st) {
    st.imgW = img.naturalWidth
    st.imgH = img.naturalHeight
  }
  void nextTick(tryScroll)
}

// ---- 容器尺寸 ----
let resizeObs: ResizeObserver | null = null
onMounted(() => {
  if (body.value) {
    resizeObs = new ResizeObserver(() => {
      viewWidth.value = body.value?.clientWidth ?? viewWidth.value
    })
    resizeObs.observe(body.value)
  }
})

onBeforeUnmount(() => {
  resizeObs?.disconnect()
  observer?.disconnect()
  for (const st of Object.values(states)) if (st.imageUrl) URL.revokeObjectURL(st.imageUrl)
  for (const task of pdfTasks) void task.destroy()
  pdfTasks.length = 0
  pdfDocs.clear()
})
</script>

<template>
  <div class="doc-preview">
    <div class="doc-preview__toolbar">
    <el-tabs v-if="sortedFiles.length > 1" v-model="activeId" class="doc-preview__files">
      <el-tab-pane v-for="f in sortedFiles" :key="f.id" :label="f.original_name" :name="f.id" />
    </el-tabs>
    <span v-else class="doc-preview__name" :title="activeFile?.original_name">{{ activeFile?.original_name }}</span>
    <span class="hint">点击左侧问题可定位原文</span>
    </div>

    <div ref="body" v-loading="!state || state.loading" class="doc-preview__body">
      <template v-if="state && !state.loading">
        <el-alert v-if="state.error" type="error" :closable="false" show-icon :title="state.error" />
        <template v-else>
          <el-alert
            v-if="!state.textAvailable"
            type="warning"
            :closable="false"
            show-icon
            title="原文已按保留期限清理，仅保留引用快照"
          />
          <el-alert
            v-if="state.rawDeleted"
            type="warning"
            :closable="false"
            show-icon
            title="原件已按保留期限删除，改为显示解析出的文字"
          />
          <el-alert v-else-if="state.rawError" type="error" :closable="false" show-icon :title="state.rawError" />

          <el-empty v-if="!state.nodes.length && !state.pdfPages.length && !state.imageUrl" description="没有可显示的内容" />

          <!-- DOCX -->
          <div v-else-if="kind === 'docx'" class="docx">
            <template v-for="(it, i) in docxItems" :key="i">
              <p
                v-if="it.type === 'p'"
                :data-loc="it.node.loc"
                class="docx__p"
                :class="{ 'is-heading': it.node.kind === 'heading', 'is-highlight': hl.has(it.node.loc) }"
              >
                <!-- 与后端 with_clause_no 一致：手写编号已在文字开头时不再重复显示 -->
                <span
                  v-if="it.node.clause_no && !it.node.text?.startsWith(it.node.clause_no)"
                  class="docx__no"
                >{{ it.node.clause_no }}</span>{{ it.node.text }}
              </p>
              <table v-else class="grid">
                <tbody>
                  <tr v-for="r in it.rows" :key="r">
                    <td
                      v-for="c in it.cols"
                      :key="c"
                      :data-loc="it.cells.get(`${r},${c}`)?.loc"
                      :class="{ 'is-highlight': hl.has(it.cells.get(`${r},${c}`)?.loc ?? '') }"
                    >
                      {{ it.cells.get(`${r},${c}`)?.text }}
                    </td>
                  </tr>
                </tbody>
              </table>
            </template>
            <el-collapse v-if="extraNodes.length" class="gap">
              <el-collapse-item title="未参与 AI 审核的内容" name="extra">
                <p v-for="n in extraNodes" :key="n.loc" :data-loc="n.loc" class="docx__p" :class="{ 'is-highlight': hl.has(n.loc) }">
                  <span class="docx__no">{{ PART_LABEL[n.part] ?? n.part }}</span>{{ n.text }}
                </p>
              </el-collapse-item>
            </el-collapse>
          </div>

          <!-- XLSX -->
          <div v-else-if="kind === 'xlsx'">
            <el-tabs v-if="sheets.length" v-model="currentSheetKey">
              <el-tab-pane v-for="s in sheets" :key="s.key" :label="s.label" :name="s.key" />
            </el-tabs>
            <div v-if="currentSheet" class="sheet-wrap">
              <table class="grid">
                <thead>
                  <tr>
                    <th />
                    <th v-for="c in currentSheet.cols" :key="c">{{ numToCol(c) }}</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="r in currentSheet.rows" :key="r.row">
                    <th>{{ r.row }}</th>
                    <td v-for="c in currentSheet.cols" :key="c" :data-loc="r.cells.get(c)?.loc" :class="{ 'is-highlight': hl.has(r.cells.get(c)?.loc ?? '') }">
                      {{ r.cells.get(c)?.text }}
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>

          <!-- PDF（按页渲染） -->
          <div v-else-if="kind === 'pdf' && state.pdfPages.length" class="pdf">
            <div
              v-for="p in state.pdfPages"
              :key="p.num"
              class="pdf-page"
              :data-page="p.num"
              :style="{ aspectRatio: `${p.w} / ${p.h}` }"
            >
              <canvas :data-page="p.num" class="pdf-page__canvas" />
              <div class="hl-layer">
                <div
                  v-for="n in hlNodes.filter((x) => x.page === p.num)"
                  :key="n.loc"
                  :data-loc="n.loc"
                  class="hl-box is-highlight"
                  :style="boxStyle(n.bbox!, p.w, p.h)"
                />
              </div>
            </div>
          </div>

          <!-- 图片 -->
          <div v-else-if="kind === 'image' && state.imageUrl" class="img-wrap">
            <img
              :src="state.imageUrl"
              class="img-wrap__img"
              alt="原件图片"
              @load="onImgLoad"
            />
            <div v-if="state.imgW" class="hl-layer">
              <div
                v-for="n in hlNodes"
                :key="n.loc"
                :data-loc="n.loc"
                class="hl-box is-highlight"
                :style="boxStyle(n.bbox!, state.imgW, state.imgH)"
              />
            </div>
          </div>

          <!-- 原件不可用时：按节点文字显示 -->
          <div v-else-if="showFallback || kind === 'other'" class="docx">
            <p v-for="n in state.nodes" :key="n.loc" :data-loc="n.loc" class="docx__p" :class="{ 'is-highlight': hl.has(n.loc) }">
              <span class="docx__no">{{ nodeLabel(n) }}</span>{{ n.text }}
            </p>
          </div>
        </template>
      </template>
    </div>
  </div>
</template>

<style scoped>
.doc-preview {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 240px;
}
.doc-preview__toolbar { flex: none; display: flex; align-items: center; flex-wrap: wrap; gap: 8px; padding: 12px 16px; border-bottom: 1px solid var(--el-border-color-light); }
.doc-preview__files { flex: 1; min-width: 0; }
.doc-preview__files :deep(.el-tabs__header) { margin: 0; }
.doc-preview__toolbar > .hint { margin-left: auto; }
.doc-preview__name { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 13px; }
.docx { background: var(--el-bg-color); padding: 24px; border: 1px solid var(--el-border-color-light); min-height: 100%; box-sizing: border-box; }

.doc-preview__body {
  flex: 1;
  min-height: 0;
  overflow: auto;
  position: relative;
  padding: 20px;
  box-sizing: border-box;
  background: var(--el-fill-color-light);
}

.docx__p {
  margin: 0 0 8px;
  line-height: 1.7;
  white-space: pre-wrap;
  border-left: 3px solid transparent;
  padding-left: 8px;
}

.docx__p.is-heading {
  font-weight: 600;
}

.docx__no {
  margin-right: 8px;
  color: var(--el-text-color-secondary);
}

.grid {
  border-collapse: collapse;
  margin-bottom: 12px;
  font-size: 13px;
}

.grid td,
.grid th {
  border: 1px solid var(--el-border-color);
  padding: 4px 8px;
  vertical-align: top;
  white-space: pre-wrap;
}

.grid th {
  background: var(--el-fill-color-light);
  color: var(--el-text-color-secondary);
  font-weight: normal;
  text-align: center;
}

.sheet-wrap {
  overflow-x: auto;
  background: var(--el-bg-color);
  padding: 16px;
  border: 1px solid var(--el-border-color-light);
}

.pdf-page {
  box-sizing: border-box;
  position: relative;
  width: 100%;
  margin-bottom: 8px;
  border: 1px solid var(--el-border-color);
  background: var(--el-bg-color);
}

.pdf-page__canvas,
.img-wrap__img {
  display: block;
  width: 100%;
  height: 100%;
}

.img-wrap__img {
  height: auto;
}

.img-wrap {
  position: relative;
}

.hl-layer {
  position: absolute;
  inset: 0;
  pointer-events: none;
}

.hl-box {
  position: absolute;
  box-sizing: border-box;
}

.is-highlight {
  background: var(--el-color-warning-light-9);
  border-bottom: 2px solid var(--el-color-warning);
}

.hl-box.is-highlight {
  border: 1px solid var(--el-color-warning);
  border-bottom-width: 2px;
  background: var(--el-color-warning-light-9);
  opacity: 0.6;
}
</style>
