<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import type { PDFDocumentLoadingTask, PDFDocumentProxy } from 'pdfjs-dist'
import pdfWorkerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'
import { ApiError } from '@/api/http'
import { previewApi, type PreviewNode } from '@/api/preview'
import type { TaskFile } from '@/api/tasks'

/**
 * 原件视图（V31-U3 3.3）：只显示 PDF 页面与图片，按 loc 的 bbox 高亮并滚动到第一个；文字视图由画布负责。
 * 原件一律先取 Blob（PDF 转 ArrayBuffer，图片用 object URL），不把接口地址交给 pdfjs 或 img。
 */
const props = defineProps<{ taskId: string; file: TaskFile; highlight: string[] }>()

interface PdfPage { num: number; w: number; h: number }
interface FileState { loading: boolean; error: string; nodes: PreviewNode[]; imageUrl: string; imgW: number; imgH: number; pdfPages: PdfPage[] }

const states = reactive<Record<string, FileState>>({})
const pdfDocs = new Map<string, PDFDocumentProxy>() // 不放进响应式状态
const pdfTasks: PDFDocumentLoadingTask[] = []
const rendered = new Set<string>()
const body = ref<HTMLElement | null>(null)
const viewWidth = ref(600)
const state = computed(() => states[props.file.id])
const isPdf = computed(() => props.file.ext.toLowerCase().replace(/^\./, '') === 'pdf')
const hl = computed(() => new Set(props.highlight))
const hlNodes = computed(() => (state.value?.nodes ?? []).filter(n => hl.value.has(n.loc) && n.bbox))

async function loadFile(file: TaskFile) {
  if (states[file.id]) return
  const st = reactive<FileState>({ loading: true, error: '', nodes: [], imageUrl: '', imgW: 0, imgH: 0, pdfPages: [] })
  states[file.id] = st
  try {
    const [res, blob] = await Promise.all([previewApi.nodes(props.taskId, file.id), previewApi.raw(props.taskId, file.id)])
    st.nodes = res.items
    if (file.ext.toLowerCase().replace(/^\./, '') === 'pdf') await openPdf(file.id, st, await blob.arrayBuffer())
    else st.imageUrl = URL.createObjectURL(blob)
  } catch (e) {
    st.error = e instanceof ApiError
      ? e.code === 'ORIGINAL_DELETED' ? '原件已按保留期限删除' : e.message
      : '原件无法显示，请刷新页面'
  }
  st.loading = false
  await nextTick()
  afterLayout()
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
  }, { root, rootMargin: '200px 0px' })
  root.querySelectorAll('.pdf-page').forEach(el => observer?.observe(el))
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
    const viewport = page.getViewport({ scale: (Math.max(viewWidth.value, 300) / base.width) * Math.min(window.devicePixelRatio || 1, 2) })
    canvas.width = Math.floor(viewport.width)
    canvas.height = Math.floor(viewport.height)
    await page.render({ canvas, viewport }).promise
  } catch {
    rendered.delete(key) // 切换文件或卸载时取消；再次进入可视区会重试
  }
}

// ---- 高亮与滚动 ----
function scrollToHighlight() {
  const root = body.value
  const el = root?.querySelector<HTMLElement>('.is-highlight')
  if (!root || !el) return
  const r = root.getBoundingClientRect()
  const e = el.getBoundingClientRect()
  root.scrollTo({ top: root.scrollTop + (e.top - r.top) - (r.height - e.height) / 2, behavior: 'smooth' })
}
function afterLayout() { observePages(); scrollToHighlight() }

watch(() => props.highlight, () => void nextTick(scrollToHighlight))
watch(() => props.file.id, async () => { await loadFile(props.file); await nextTick(); afterLayout() }, { immediate: true })

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
  resizeObs = new ResizeObserver(() => { viewWidth.value = body.value?.clientWidth ?? viewWidth.value })
  resizeObs.observe(body.value)
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
  <div ref="body" v-loading="!state || state.loading" class="doc-preview si-stage">
    <template v-if="state && !state.loading">
      <el-alert v-if="state.error" type="warning" :closable="false" show-icon :title="state.error" />
      <div v-else-if="isPdf" class="pdf">
        <div v-for="p in state.pdfPages" :key="p.num" class="pdf-page si-paper" :data-page="p.num" :style="{ aspectRatio: `${p.w} / ${p.h}` }">
          <canvas :data-page="p.num" class="pdf-page__canvas" />
          <div class="hl-layer">
            <div v-for="n in hlNodes.filter((x) => x.page === p.num)" :key="n.loc" class="hl-box is-highlight" :style="boxStyle(n.bbox!, p.w, p.h)" />
          </div>
        </div>
      </div>
      <div v-else-if="state.imageUrl" class="img-wrap si-paper">
        <img :src="state.imageUrl" class="img-wrap__img" alt="原件图片" @load="onImgLoad" />
        <div v-if="state.imgW" class="hl-layer">
          <div v-for="n in hlNodes" :key="n.loc" class="hl-box is-highlight" :style="boxStyle(n.bbox!, state.imgW, state.imgH)" />
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.doc-preview { flex: 1; min-height: 0; overflow: auto; position: relative; padding: 20px; box-sizing: border-box; border-radius: var(--si-radius-card); }
.pdf, .img-wrap { max-width: 860px; margin: 0 auto; }
.pdf-page { position: relative; width: 100%; margin-bottom: 8px; border-radius: var(--si-radius-paper); box-shadow: var(--si-paper-shadow); }
.pdf-page__canvas, .img-wrap__img { display: block; width: 100%; height: 100%; }
.img-wrap__img { height: auto; }
.img-wrap { position: relative; border-radius: var(--si-radius-paper); box-shadow: var(--si-paper-shadow); }
.hl-layer { position: absolute; inset: 0; pointer-events: none; }
.hl-box { position: absolute; box-sizing: border-box; border: 1px solid var(--si-warn); border-bottom-width: 2px; background: var(--si-hl-medium); opacity: .7; scroll-margin-block: 48px; }
</style>
