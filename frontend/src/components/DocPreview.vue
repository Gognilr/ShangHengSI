<script setup lang="ts">
import SiNotice from '@/components/ui/SiNotice.vue'
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import type { PDFDocumentLoadingTask, PDFDocumentProxy } from 'pdfjs-dist'
import pdfWorkerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'
import { ZoomIn, ZoomOut } from '@lucide/vue'
import { ApiError } from '@/api/http'
import { previewApi, type PreviewNode, type PreviewNodes } from '@/api/preview'
import type { TaskFile } from '@/api/tasks'

/**
 * 原件视图（V31-U3 3.3）：只显示 PDF 页面与图片，按 loc 的 bbox 高亮并滚动到第一个；文字视图由画布负责。
 * 原件一律先取 Blob（PDF 转 ArrayBuffer，图片用 object URL），不把接口地址交给 pdfjs 或 img。
 * 缩放：右上角工具条与 Ctrl+滚轮，50%～400%（相对适合宽度），PDF 按新尺寸重新渲染；autoZoom 时打开即放大到高亮处可读。
 */
/** nodeLoader / rawLoader：工作台会话改读 /assistant/sessions/{id}/files/…（V32-B-01） */
const props = defineProps<{ taskId: string; file: TaskFile; highlight: string[]; focusPage?: number; autoZoom?: boolean; nodeLoader?: (taskId: string, fileId: string) => Promise<PreviewNodes>; rawLoader?: (taskId: string, fileId: string) => Promise<Blob> }>()
const emit = defineEmits<{ ready: [] }>()

interface PdfPage { num: number; w: number; h: number }
interface FileState { loading: boolean; error: string; nodes: PreviewNode[]; imageUrl: string; imgW: number; imgH: number; pdfPages: PdfPage[] }

const states = reactive<Record<string, FileState>>({})
const pdfDocs = new Map<string, PDFDocumentProxy>() // 不放进响应式状态
const pdfTasks: PDFDocumentLoadingTask[] = []
const rendered = new Set<string>()
const body = ref<HTMLElement | null>(null)
const viewWidth = ref(600)
const state = computed(() => states[props.file.id])
const visiblePdfPages = computed(() => state.value?.pdfPages.filter(page => props.focusPage == null || page.num === props.focusPage) ?? [])
const isPdf = computed(() => props.file.ext.toLowerCase().replace(/^\./, '') === 'pdf')
const hl = computed(() => new Set(props.highlight))
const hlNodes = computed(() => (state.value?.nodes ?? []).filter(n => hl.value.has(n.loc) && n.bbox))

// ---- 缩放 ----
const PAD = 20
const ZOOM_MIN = 0.5, ZOOM_MAX = 4
const zoom = ref(1)
/** 适合宽度时的纸面宽度（px） */
const fitWidth = computed(() => Math.max(240, Math.min(860, viewWidth.value - PAD * 2)))
const paperWidth = computed(() => Math.round(fitWidth.value * zoom.value))
const zoomLabel = computed(() => `${Math.round(zoom.value * 100)}%`)
const clampZoom = (z: number) => Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, Math.round(z * 100) / 100))
/** 改缩放并保持锚点（视口内坐标）下的内容不动 */
function setZoom(next: number, anchor?: { x: number; y: number }) {
  const root = body.value
  const z = clampZoom(next)
  if (!root || z === zoom.value) return
  const ax = anchor?.x ?? root.clientWidth / 2, ay = anchor?.y ?? root.clientHeight / 2
  const ratio = z / zoom.value
  const left = (root.scrollLeft + ax) * ratio - ax, top = (root.scrollTop + ay) * ratio - ay
  zoom.value = z
  void nextTick(() => { root.scrollLeft = left; root.scrollTop = top })
}
const zoomStep = (dir: 1 | -1) => setZoom(dir > 0 ? zoom.value * 1.25 : zoom.value / 1.25)
function onWheel(e: WheelEvent) {
  if (!e.ctrlKey || !body.value) return
  e.preventDefault()
  const r = body.value.getBoundingClientRect()
  setZoom(zoom.value * (e.deltaY < 0 ? 1.1 : 1 / 1.1), { x: e.clientX - r.left, y: e.clientY - r.top })
}
// 缩放停下 150ms 后按新尺寸重绘可视页；重绘前沿用旧画面拉伸，不闪白
let zoomTimer: ReturnType<typeof setTimeout> | undefined
watch([zoom, fitWidth], () => { clearTimeout(zoomTimer); zoomTimer = setTimeout(() => void nextTick(observePages), 150) })

async function loadFile(file: TaskFile) {
  if (states[file.id]) return
  const st = reactive<FileState>({ loading: true, error: '', nodes: [], imageUrl: '', imgW: 0, imgH: 0, pdfPages: [] })
  states[file.id] = st
  try {
    const [res, blob] = await Promise.all([(props.nodeLoader ?? previewApi.nodes)(props.taskId, file.id), (props.rawLoader ?? previewApi.raw)(props.taskId, file.id)])
    st.nodes = res.items
    if (file.ext.toLowerCase().replace(/^\./, '') === 'pdf') await openPdf(file.id, st, await blob.arrayBuffer())
    else st.imageUrl = URL.createObjectURL(blob)
  } catch (e) {
    st.error = e instanceof ApiError
      ? e.code === 'ORIGINAL_DELETED' ? '原件已按保留期限删除' : e.message
      : '原件无法显示，请刷新页面'
  }
  st.loading = false
  if (props.autoZoom) applyAutoZoom()
  await nextTick()
  afterLayout()
  if (!st.error) emit('ready')
}

/** 打开即放大：高亮所在页按约 1.6 倍原始尺寸显示（扫描件常见 10～12 磅字约 17px 高） */
function applyAutoZoom() {
  const node = hlNodes.value[0]
  const page = state.value?.pdfPages.find(p => p.num === node?.page)
  if (page) zoom.value = clampZoom(Math.max(1, (1.6 * page.w) / fitWidth.value))
}

async function openPdf(fileId: string, st: FileState, data: ArrayBuffer) {
  const pdfjs = await import('pdfjs-dist')
  pdfjs.GlobalWorkerOptions.workerSrc = pdfWorkerUrl
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
  if (!root || !isPdf.value) return
  observer = new IntersectionObserver((entries) => {
    for (const en of entries) if (en.isIntersecting) void renderPage(props.file.id, Number((en.target as HTMLElement).dataset.page))
  }, { root, rootMargin: '200px 200px' })
  root.querySelectorAll('.pdf-page').forEach(el => observer?.observe(el))
}

const MAX_PIXELS = 16_000_000 // 单页画布像素上限，放大到 400% 时限制内存
async function renderPage(fileId: string, n: number) {
  const key = `${fileId}:${n}:${paperWidth.value}`
  if (rendered.has(key)) return
  rendered.add(key)
  try {
    const doc = pdfDocs.get(fileId)
    const canvas = body.value?.querySelector<HTMLCanvasElement>(`canvas[data-page="${n}"]`)
    if (!doc || !canvas) throw new Error('canvas missing')
    const page = await doc.getPage(n)
    const base = page.getViewport({ scale: 1 })
    let scale = (paperWidth.value / base.width) * Math.min(window.devicePixelRatio || 1, 2)
    scale = Math.min(scale, Math.sqrt(MAX_PIXELS / (base.width * base.height)))
    const viewport = page.getViewport({ scale })
    // 先画到离屏画布再一次贴上，缩放时不闪白
    const off = document.createElement('canvas')
    off.width = Math.floor(viewport.width)
    off.height = Math.floor(viewport.height)
    await page.render({ canvas: off, viewport }).promise
    if (!rendered.has(key)) return // 期间又缩放了
    canvas.width = off.width
    canvas.height = off.height
    canvas.getContext('2d')?.drawImage(off, 0, 0)
  } catch {
    rendered.delete(key) // 切换文件或卸载时取消；再次进入可视区会重试
  }
}

/**
 * 局部放大（校对扫描文字）：把 loc 的定位框连同四周一圈画到 canvas，宽度贴合 cssWidth，倍数限 1.5～4。
 * 返回定位框在画面中的相对位置（0～1），找不到时返回 null。
 */
async function drawCrop(canvas: HTMLCanvasElement, loc: string, cssWidth: number) {
  const node = state.value?.nodes.find(n => n.loc === loc)
  const doc = pdfDocs.get(props.file.id)
  const pg = state.value?.pdfPages.find(p => p.num === node?.page)
  if (!node?.bbox || !doc || !pg) return null
  const [x0, y0, x1, y1] = node.bbox as [number, number, number, number]
  const mx = Math.max(24, Math.min(48, (x1 - x0) * 0.25)), my = Math.max(14, Math.min(28, (y1 - y0) * 0.8))
  const rx0 = Math.max(0, x0 - mx), ry0 = Math.max(0, y0 - my), rx1 = Math.min(pg.w, x1 + mx), ry1 = Math.min(pg.h, y1 + my)
  const css = Math.min(4, Math.max(1.5, cssWidth / (rx1 - rx0)))
  const dpr = Math.min(window.devicePixelRatio || 1, 2)
  const scale = Math.min(css * dpr, Math.sqrt(MAX_PIXELS / ((rx1 - rx0) * (ry1 - ry0))))
  const page = await doc.getPage(pg.num)
  const viewport = page.getViewport({ scale, offsetX: -rx0 * scale, offsetY: -ry0 * scale })
  canvas.width = Math.ceil((rx1 - rx0) * scale)
  canvas.height = Math.ceil((ry1 - ry0) * scale)
  canvas.style.width = `${Math.round((rx1 - rx0) * css)}px`
  await page.render({ canvas, viewport }).promise
  const w = rx1 - rx0, h = ry1 - ry0
  return { left: (x0 - rx0) / w, top: (y0 - ry0) / h, width: (x1 - x0) / w, height: (y1 - y0) / h }
}

// ---- 高亮与滚动 ----
function scrollToHighlight() {
  const root = body.value
  const el = root?.querySelector<HTMLElement>('.is-highlight')
  if (!root || !el) return
  const r = root.getBoundingClientRect()
  const e = el.getBoundingClientRect()
  // 放得下就居中；比视口大时把起始处（左上角）放在视口内
  root.scrollTo({
    top: root.scrollTop + (e.top - r.top) - Math.max(24, (r.height - e.height) / 2),
    left: root.scrollLeft + (e.left - r.left) - Math.max(16, (r.width - e.width) / 2),
    behavior: 'smooth',
  })
}
function afterLayout() { observePages(); scrollToHighlight() }

watch(() => props.highlight, () => void nextTick(scrollToHighlight))
watch(() => props.focusPage, () => void nextTick(afterLayout))
watch(() => props.file.id, async () => { zoom.value = 1; await loadFile(props.file); await nextTick(); afterLayout() }, { immediate: true })

function pct(v: number, total: number): string { return `${(v / total) * 100}%` }
function boxStyle(bbox: number[], w: number, h: number) {
  const [x0, top, x1, bottom] = bbox as [number, number, number, number]
  return { left: pct(x0, w), top: pct(top, h), width: pct(x1 - x0, w), height: pct(bottom - top, h) }
}
function onImgLoad(e: Event) {
  const img = e.target as HTMLImageElement
  if (state.value) { state.value.imgW = img.naturalWidth; state.value.imgH = img.naturalHeight }
  void nextTick(scrollToHighlight)
}

let resizeObs: ResizeObserver | null = null
onMounted(() => {
  if (!body.value) return
  viewWidth.value = body.value.clientWidth || viewWidth.value
  resizeObs = new ResizeObserver(() => { viewWidth.value = body.value?.clientWidth ?? viewWidth.value })
  resizeObs.observe(body.value)
})
onBeforeUnmount(() => {
  clearTimeout(zoomTimer)
  resizeObs?.disconnect()
  observer?.disconnect()
  for (const st of Object.values(states)) if (st.imageUrl) URL.revokeObjectURL(st.imageUrl)
  for (const task of pdfTasks) void task.destroy()
  pdfTasks.length = 0
  pdfDocs.clear()
})
defineExpose({ drawCrop })
</script>

<template>
  <div class="doc-preview">
    <div v-if="state && !state.loading && !state.error" class="zoombar" role="toolbar" aria-label="缩放原件">
      <button type="button" class="zb" :disabled="zoom <= ZOOM_MIN" title="缩小" aria-label="缩小" @click="zoomStep(-1)"><ZoomOut :size="15" :stroke-width="1.75" /></button>
      <button type="button" class="zb zb-pct" title="适合宽度" @click="setZoom(1)">{{ zoomLabel }}</button>
      <button type="button" class="zb" :disabled="zoom >= ZOOM_MAX" title="放大（Ctrl+滚轮）" aria-label="放大" @click="zoomStep(1)"><ZoomIn :size="15" :stroke-width="1.75" /></button>
    </div>
    <div ref="body" v-loading="!state || state.loading" class="dp-body si-stage" @wheel="onWheel">
      <template v-if="state && !state.loading">
        <SiNotice v-if="state.error" type="warning" :closable="false" :title="state.error" />
        <div v-else-if="isPdf" class="pdf" :style="{ width: `${paperWidth}px` }">
          <div v-for="p in visiblePdfPages" :key="p.num" class="pdf-page si-paper" :data-page="p.num" :style="{ aspectRatio: `${p.w} / ${p.h}` }">
            <canvas :data-page="p.num" class="pdf-page__canvas" />
            <div class="hl-layer">
              <div v-for="n in hlNodes.filter((x) => x.page === p.num)" :key="n.loc" class="hl-box is-highlight" :style="boxStyle(n.bbox!, p.w, p.h)" />
            </div>
          </div>
        </div>
        <div v-else-if="state.imageUrl" class="img-wrap si-paper" :style="{ width: `${paperWidth}px` }">
          <img :src="state.imageUrl" class="img-wrap__img" alt="原件图片" @load="onImgLoad" />
          <div v-if="state.imgW" class="hl-layer">
            <div v-for="n in hlNodes" :key="n.loc" class="hl-box is-highlight" :style="boxStyle(n.bbox!, state.imgW, state.imgH)" />
          </div>
        </div>
      </template>
    </div>
  </div>
</template>

<style scoped>
.doc-preview { position: relative; flex: 1; min-height: 0; display: flex; flex-direction: column; }
.dp-body { flex: 1; min-height: 0; overflow: auto; position: relative; padding: 20px; box-sizing: border-box; border-radius: var(--si-radius-card); }
.pdf, .img-wrap { max-width: none; margin: 0 auto; }
.pdf-page { position: relative; width: 100%; margin-bottom: 8px; border-radius: var(--si-radius-paper); box-shadow: var(--si-paper-shadow); }
.pdf-page__canvas, .img-wrap__img { display: block; width: 100%; height: 100%; }
.img-wrap__img { height: auto; }
.img-wrap { position: relative; border-radius: var(--si-radius-paper); box-shadow: var(--si-paper-shadow); }
.hl-layer { position: absolute; inset: 0; pointer-events: none; }
.hl-box { position: absolute; box-sizing: border-box; border: 1px solid var(--si-warn); border-bottom-width: 2px; background: var(--si-hl-medium); opacity: .7; scroll-margin-block: 48px; }
/* 缩放工具条：浮在原件右上角，不随纸面滚动 */
.zoombar { position: absolute; top: 10px; right: 18px; z-index: 3; display: flex; align-items: center; gap: 2px; padding: 3px; border-radius: 10px; background: var(--si-surface-pop); box-shadow: var(--si-shadow-pop); }
.zb { display: grid; place-items: center; min-width: 28px; height: 28px; padding: 0 4px; border: 0; border-radius: 7px; background: transparent; color: var(--si-ink-2); font: var(--si-font-label-sm); font-variant-numeric: tabular-nums; cursor: pointer; }
.zb:hover:not(:disabled) { background: var(--si-hover-bg); color: var(--si-ink-1); }
.zb:disabled { color: var(--si-ink-4); cursor: not-allowed; }
.zb:focus-visible { outline: 2px solid var(--si-focus); outline-offset: -2px; }
.zb-pct { min-width: 48px; }
</style>
