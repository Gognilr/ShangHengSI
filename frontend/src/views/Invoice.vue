<script setup lang="ts">
// 发票管家（V36-I；澄明二版 6.29，发票管家设计第一～六节）：拖入一批发票 → 本机识别 → 下载 Excel 清单。
// 不留记录：原件与结果只在服务器内存盘，按时清除；不进历史记录、工作台与侧栏数字。页面只看进度与预览，不改值。
// 离开页面后回来仍能看到这一批：浏览器只记批次 id（不含内容），取不到即视为已清除。
import { computed, nextTick, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import { ChevronRight, Download, Eraser, FileUp } from '@lucide/vue'
import { ElMessage } from 'element-plus'
import { invoiceApi, type InvoiceBatch, type InvoiceRow } from '@/api/invoice'
import { ApiError } from '@/api/http'
import SiIllus from '@/components/brand/illus/SiIllus.vue'
import MoneyRoll from '@/components/invoice/MoneyRoll.vue'
import { money } from '@/components/invoice/money'
import SiBadge from '@/components/ui/SiBadge.vue'
import SiNotice from '@/components/ui/SiNotice.vue'
import SiStagger from '@/components/ui/SiStagger.vue'
import SiTrip from '@/components/ui/SiTrip.vue'
import { useAuthStore } from '@/stores/auth'
import { useFx } from '@/utils/motion'
import { saveBlob } from '@/utils/save'
import { formatClock } from '@/utils/time'
import { invoiceTrip } from '@/utils/trip'

const auth = useAuthStore()
const fx = useFx()

/* ================= 上限（与后端 intake 一致，先在浏览器里拦下明显超限的） ================= */
const EXTS = ['pdf', 'ofd', 'xml', 'jpg', 'jpeg', 'png', 'zip']
const MAX_FILES = 100, MAX_ONE = 20 * 1024 * 1024, MAX_TOTAL = 200 * 1024 * 1024
const ext = (n: string) => n.slice(n.lastIndexOf('.') + 1).toLowerCase()
function precheck(files: File[]): string {
  if (!files.length) return '没有可识别的文件'
  const bad = files.find(f => !EXTS.includes(ext(f.name)))
  if (bad) return `“${bad.name}”不是支持的格式，只收 PDF、OFD、XML、JPG、PNG 和 ZIP`
  if (files.length > MAX_FILES) return `每批不超过 ${MAX_FILES} 个文件，这次选了 ${files.length} 个`
  const big = files.find(f => f.size > MAX_ONE)
  if (big) return `“${big.name}”超过 20 MB，请压缩或拆开后再传`
  if (files.reduce((s, f) => s + f.size, 0) > MAX_TOTAL) return '每批合计不超过 200 MB，请分批整理'
  return ''
}

/* ================= 批次 ================= */
const storeKey = computed(() => `si.invoice.batch.${auth.user?.id ?? ''}`)
function remember(id: string | null) {
  try { if (id) localStorage.setItem(storeKey.value, id); else localStorage.removeItem(storeKey.value) } catch { /* 只影响回到本页时能否接上 */ }
}
function recalled(): string | null { try { return localStorage.getItem(storeKey.value) } catch { return null } }

const batch = shallowRef<InvoiceBatch | null>(null)
const upload = ref<number | null>(null)
const error = ref('')
const notice = ref('')
const added = ref(0)
const work = computed(() => upload.value != null || !!batch.value)
const finished = computed(() => batch.value?.status === 'done')
const trip = computed(() => invoiceTrip({ upload: upload.value, status: batch.value?.status, done: batch.value?.progress.done, total: batch.value?.progress.total }))

let poll: ReturnType<typeof setTimeout> | undefined, expiry: ReturnType<typeof setTimeout> | undefined
function stopTimers() { clearTimeout(poll); clearTimeout(expiry) }
onBeforeUnmount(stopTimers)

/** 回到票夹：清除后、超时后、出错后 */
function reset(msg = '') {
  stopTimers()
  batch.value = null
  upload.value = null
  added.value = 0
  onlyHinted.value = false
  remember(null)
  notice.value = msg
}

async function refresh(id: string) {
  try {
    const b = await invoiceApi.get(id)
    if (batch.value?.id !== id) return
    batch.value = b
    if (b.status === 'queued' || b.status === 'running') { poll = setTimeout(() => void refresh(id), 1000); return }
    if (b.status === 'done') {
      // 到点后服务器清除，页面也回到票夹
      const left = Date.parse(b.expires_at) - Date.now()
      expiry = setTimeout(() => void refresh(id), Math.max(5000, left + 3000))
    }
  } catch (e) {
    if (batch.value?.id !== id) return
    if (e instanceof ApiError && e.status === 404) { reset('上一批已按时清除。原件与结果不留记录，需要时请重新拖入。'); return }
    // 网络抖动：稍后再取，不打断页面
    poll = setTimeout(() => void refresh(id), 3000)
  }
}

onMounted(() => {
  const id = recalled()
  if (id && /^[0-9a-f]{32}$/.test(id)) { resumed = true; batch.value = { id, status: 'queued', progress: { done: 0, total: 0 }, expires_at: '' }; void refresh(id) }
  else if (id) remember(null)
  window.addEventListener('paste', onPaste)
})
onBeforeUnmount(() => window.removeEventListener('paste', onPaste))

/* ================= 收票：拖入、点击、粘贴 ================= */
const picker = ref<HTMLInputElement>()
const dragging = ref(false)
let dragDepth = 0
const drops = ref<{ k: number; i: number }[]>([])
const dropMore = ref(0)

async function start(list: File[]) {
  if (upload.value != null || (batch.value && !finished.value && batch.value.status !== 'failed')) return
  const msg = precheck(list)
  if (msg) { error.value = msg; return }
  error.value = ''
  notice.value = ''
  resumed = false
  stopTimers()
  // 完整档：文件化作小票卡依次落进票夹（每张晚 45 ms，最多 8 张，其余写 +n），落完再收成窄带
  const anim = fx.value === 'full' && !work.value
  if (anim) {
    drops.value = list.slice(0, 8).map((_, i) => ({ k: Date.now() + i, i }))
    dropMore.value = Math.max(0, list.length - 8)
  }
  added.value = list.length
  batch.value = null
  upload.value = 0
  const landed = anim ? new Promise(r => setTimeout(r, 560 + Math.min(list.length, 8) * 45)) : Promise.resolve()
  try {
    const [{ id }] = await Promise.all([invoiceApi.upload(list, (a, b) => { upload.value = b ? Math.round(a / b * 100) : 0 }), landed])
    remember(id)
    batch.value = { id, status: 'queued', progress: { done: 0, total: list.length }, expires_at: '' }
    upload.value = null
    await refresh(id)
  } catch (e) {
    await landed
    upload.value = null
    error.value = e instanceof ApiError ? e.message : '上传失败，请稍后重试'
  } finally {
    drops.value = []
    dropMore.value = 0
  }
}
const uploadingView = computed(() => upload.value != null && drops.value.length === 0)
const showEntry = computed(() => !work.value || drops.value.length > 0)

function pick(e: Event) {
  const input = e.target as HTMLInputElement
  const files = Array.from(input.files ?? [])
  input.value = ''
  if (files.length) void start(files)
}
function onDragEnter(e: DragEvent) { if (!e.dataTransfer?.types.includes('Files')) return; dragDepth++; dragging.value = true }
function onDragLeave() { dragDepth = Math.max(0, dragDepth - 1); if (!dragDepth) dragging.value = false }
function onDrop(e: DragEvent) {
  dragDepth = 0; dragging.value = false
  const files = Array.from(e.dataTransfer?.files ?? [])
  if (files.length) void start(files)
}
function onPaste(e: ClipboardEvent) {
  if (!showEntry.value || upload.value != null) return
  const t = e.target as HTMLElement | null
  if (t && (t.isContentEditable || /^(INPUT|TEXTAREA)$/.test(t.tagName))) return
  const files = Array.from(e.clipboardData?.files ?? [])
  if (!files.length) return
  e.preventDefault()
  void start(files)
}

/* ================= 结果 ================= */
const rows = computed<InvoiceRow[]>(() => batch.value?.rows ?? [])
const onlyHinted = ref(false)
const shownRows = computed(() => onlyHinted.value ? rows.value.filter(r => r.hints.length) : rows.value)
const summary = computed(() => batch.value?.summary)
const unrecognized = computed(() => batch.value?.unrecognized ?? [])
// 识别中：每识别完一个文件弹入一行占位，正在识别的那一行带扫描光条（预览行在全部识别完后才有内容，V36Q-22）
const pending = computed(() => {
  const p = batch.value?.progress
  if (!p || finished.value || batch.value?.status === 'failed' || (batch.value?.status === 'queued' && !p.done)) return [] as { i: number; cur: boolean }[]
  const n = Math.min(p.total, p.done + 1)
  return Array.from({ length: n }, (_, i) => ({ i, cur: i === p.done }))
})
const SHORT: [RegExp, string][] = [
  [/数电票.*专用/, '数电专票'], [/数电票.*普通/, '数电普票'], [/电子专用/, '电子专票'], [/通行费/, '通行费电子普票'],
  [/卷票/, '普票（卷票）'], [/电子普通/, '电子普票'], [/专用发票/, '增值税专票'], [/普通发票/, '增值税普票'],
  [/铁路/, '铁路电子客票'], [/航空|行程单/, '机票行程单'],
]
const shortKind = (k: string) => SHORT.find(([re]) => re.test(k))?.[1] ?? (k || '—')
const expiresAt = computed(() => batch.value?.expires_at ? formatClock(batch.value.expires_at) : '')
// 只在第一次看到这一批的结果时滚动金额；从别处回来直接显示
const rolled = ref(false)
let resumed = false
watch(finished, (on, was) => {
  rolled.value = on && was === false && !resumed
  if (rolled.value) setTimeout(() => { rolled.value = false }, 2500)
})

const downloading = ref(false)
const ticked = ref(0)
async function download() {
  const id = batch.value?.id
  if (!id || downloading.value) return
  downloading.value = true
  try {
    const { blob, filename } = await invoiceApi.excel(id)
    const res = await saveBlob(blob, filename)
    if (res !== 'cancelled') {
      if (res === 'downloaded') ElMessage.success('发票清单已下载')
      if (fx.value === 'full') ticked.value++
    }
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) reset('上一批已按时清除。原件与结果不留记录，需要时请重新拖入。')
    else ElMessage.error(e instanceof ApiError ? e.message : '下载失败，请稍后重试')
  } finally {
    downloading.value = false
  }
}
const clearing = ref(false)
async function clear() {
  const id = batch.value?.id
  if (!id || clearing.value) return
  clearing.value = true
  try { await invoiceApi.clear(id) } catch (e) { if (!(e instanceof ApiError && e.status === 404)) { ElMessage.error(e instanceof ApiError ? e.message : '清除失败，请稍后重试'); clearing.value = false; return } }
  clearing.value = false
  reset()
  ElMessage.success('已清除，服务器上不再有这一批')
  await nextTick()
  folder.value?.focus()
}
const folder = ref<HTMLElement>()
function again() { reset(); void nextTick(() => folder.value?.focus()) }
</script>

<template>
  <!-- 入口：票夹 -->
  <section v-if="showEntry" class="inv-entry si-stagger" @dragenter.prevent="onDragEnter" @dragover.prevent @dragleave="onDragLeave" @drop.prevent="onDrop">
    <header>
      <h1 class="entry-title">发票管家</h1>
      <p class="entry-lede">把一批发票拖进来，在本机识别，整理成一份 Excel 发票清单。不调用模型，不留记录。</p>
    </header>
    <SiNotice v-if="notice" class="note" type="info" :title="notice" closable @close="notice = ''" />
    <SiNotice v-if="error" class="note" type="error" :title="error" />
    <div ref="folder" class="folder si-hov" :class="{ drag: dragging, busy: drops.length > 0 }" role="button" tabindex="0"
      aria-label="选择发票文件，也可以拖入或按 Ctrl+V 粘贴" @click="picker?.click()" @keydown.enter.self="picker?.click()" @keydown.space.self.prevent="picker?.click()">
      <div class="clip-wrap">
        <SiIllus kind="folder" class="illus" :open="dragging" />
        <span v-for="d in drops" :key="d.k" class="drop" :style="{ '--i': d.i }" aria-hidden="true"><i /><i /></span>
        <span v-if="dropMore" class="drop-more" aria-hidden="true">+{{ dropMore }}</span>
      </div>
      <b class="f-t">{{ dragging ? '松开即可加入' : drops.length ? `已加入 ${added} 个文件` : '拖入发票，或点击选择' }}</b>
      <span class="f-h">也可以按 Ctrl+V 粘贴</span>
      <span class="f-b" aria-hidden="true"><FileUp :size="15" :stroke-width="1.75" />选择文件</span>
    </div>
    <p class="foot">PDF、OFD、XML、JPG、PNG，可整包拖入 zip；一批最多 100 张 · 全程本机 · 原件只在内存中，30 分钟后清除</p>
    <input ref="picker" type="file" multiple accept=".pdf,.ofd,.xml,.jpg,.jpeg,.png,.zip" hidden @change="pick">
  </section>

  <!-- 识别中与结果：票夹收成上方一条窄带，下方是清单预览 -->
  <section v-else class="inv-work" aria-label="发票清单">
    <header class="band">
      <SiIllus kind="folder" class="band-illus" />
      <div class="band-t">
        <b>发票管家</b>
        <span v-if="uploadingView">已加入 {{ added }} 个文件</span>
        <span v-else-if="finished && summary">本批 {{ batch?.progress.total }} 个文件 · 识别出 {{ summary.count }} 张</span>
        <span v-else-if="batch?.progress.total">共 {{ batch.progress.total }} 个文件</span>
      </div>
      <SiTrip :trip="trip" scale="bar" />
    </header>

    <div class="body">
      <SiNotice v-if="batch?.status === 'failed'" type="error" :title="batch.error || '识别时发生内部错误，请重新上传'">
        <template #actions><el-button @click="again">重新拖入</el-button></template>
      </SiNotice>

      <template v-else>
        <div class="sum">
          <p v-if="finished && summary" class="sum-t">
            <b class="num">{{ summary.count }}</b> 张 · 价税合计 <b class="num">¥{{ money(summary.total) }}</b>
            <template v-if="summary.hinted"> ·
              <button type="button" class="hinted" :class="{ on: onlyHinted }" :aria-pressed="onlyHinted" @click="onlyHinted = !onlyHinted">有核对提示 <b class="num">{{ summary.hinted }}</b> 张</button>
              <button v-if="onlyHinted" type="button" class="all" @click="onlyHinted = false">显示全部</button>
            </template>
          </p>
          <p v-else class="sum-t wait">{{ uploadingView ? '正在把文件送进内存盘…' : '正在本机识别，识别完的文件会依次出现在下面' }}</p>
          <div v-if="finished" class="acts">
            <el-button :disabled="clearing" @click="clear"><Eraser :size="15" :stroke-width="1.75" />清除</el-button>
            <el-button v-if="summary?.count" type="primary" :loading="downloading" @click="download">
              <span v-if="!downloading" class="dl-ic" aria-hidden="true"><Download :size="15" :stroke-width="1.75" class="ic-a" :class="{ gone: ticked }" /><svg v-if="ticked" :key="ticked" class="tick" viewBox="0 0 16 16"><path d="M3 8.5l3.2 3L13 4.5" /><rect class="tick-m" x="1" y="1" width="14" height="14" /></svg></span>
              下载 Excel 清单
            </el-button>
          </div>
        </div>

        <div class="tbl-wrap">
          <table class="tbl">
            <thead><tr>
              <th class="c-seq">序号</th><th class="c-kind">票种</th><th class="c-no">发票号码</th><th class="c-date">开票日期</th>
              <th>销售方</th><th class="c-amt">价税合计</th><th class="c-rate">税率</th><th class="c-amt c-tax">税额</th><th class="c-hint">核对提示</th>
            </tr></thead>
            <SiStagger v-if="finished" tag="tbody">
              <tr v-for="(r, i) in shownRows" :key="r.seq" :data-i="i" :class="{ hint: r.hints.length }">
                <td class="c-seq num">{{ r.seq }}</td>
                <td class="c-kind" :title="r.kind">{{ shortKind(r.kind) }}</td>
                <td class="c-no mono">{{ r.number || '—' }}</td>
                <td class="c-date num">{{ r.date || '—' }}</td>
                <td class="c-seller" :title="r.seller_name">{{ r.seller_name || '—' }}</td>
                <td class="c-amt"><MoneyRoll :value="r.total" :roll="rolled" :delay="Math.min(i, 7) * 45" /></td>
                <td class="c-rate num" :title="r.rates">{{ r.rates || '—' }}</td>
                <td class="c-amt c-tax"><MoneyRoll :value="r.tax" :roll="rolled" :delay="Math.min(i, 7) * 45" /></td>
                <td class="c-hint">
                  <el-tooltip v-if="r.hints.length" placement="top-end" :show-after="200">
                    <template #content><div class="hint-full"><p v-for="h in r.hints" :key="h">{{ h }}</p></div></template>
                    <span tabindex="0" class="hint-b"><SiBadge tone="warn">{{ r.hints[0] }}</SiBadge><span v-if="r.hints.length > 1" class="more">等 {{ r.hints.length }} 条</span></span>
                  </el-tooltip>
                  <span v-else class="ok">—</span>
                </td>
              </tr>
            </SiStagger>
            <SiStagger v-else tag="tbody">
              <tr v-for="p in pending" :key="p.i" :data-i="0" class="ph" :class="{ cur: p.cur }">
                <td class="c-seq num">{{ p.i + 1 }}</td>
                <td colspan="7" class="ph-c"><span v-if="p.cur" class="scan" aria-hidden="true" /><span class="ph-t">{{ p.cur ? `正在识别第 ${p.i + 1} 个文件` : `第 ${p.i + 1} 个文件已识别` }}</span><span class="ph-bars" aria-hidden="true"><i /><i /><i /></span></td>
                <td class="c-hint"><span class="ok">—</span></td>
              </tr>
            </SiStagger>
          </table>
          <p v-if="finished && !rows.length" class="none">这一批没有识别出发票，原因见下方“未识别”。</p>
        </div>

        <details v-if="finished && unrecognized.length" class="unrec">
          <summary><ChevronRight :size="15" :stroke-width="2" class="chev" />未识别 <b class="num">{{ unrecognized.length }}</b> 个文件</summary>
          <ul><li v-for="(u, i) in unrecognized" :key="i"><b :title="u.name">{{ u.name }}</b><span>{{ u.reason }}</span></li></ul>
        </details>

        <p class="keep">原件与结果只在服务器内存中，30 分钟后自动清除，不留记录。<template v-if="finished && expiresAt">这一批将在 {{ expiresAt }} 清除。</template></p>
      </template>
    </div>
  </section>
</template>

<style scoped>
/* ---- 入口：票夹（内容宽 1040） ---- */
.inv-entry { max-width: 1040px; margin: 0 auto; padding: 48px 24px 64px; box-sizing: border-box; }
.note { margin-bottom: 16px; }
.folder { display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 6px; height: 340px; padding: 0 24px; border: 1px solid var(--si-hair); border-radius: var(--si-radius-sheet); background: var(--si-surface-1); box-shadow: var(--si-shadow-1); cursor: pointer; transition: transform var(--si-d3) var(--si-ease-out), box-shadow var(--si-d3) var(--si-ease-out), border-color var(--si-d3) var(--si-ease-out); }
.folder:hover { box-shadow: var(--si-shadow-2); }
.folder:focus-visible { outline: 3px solid var(--si-ring); outline-offset: 2px; }
.folder.drag { border-color: var(--si-m-inv); box-shadow: 0 0 0 4px var(--si-m-inv-soft), var(--si-shadow-2); transform: scale(1.01); }
.clip-wrap { position: relative; width: 300px; height: 164px; margin-bottom: 8px; }
.illus { width: 100%; height: 100%; }
.f-t { color: var(--si-ink-1); font: var(--si-font-h3); }
.f-h { color: var(--si-ink-3); font: var(--si-font-meta); }
.f-b { display: inline-flex; align-items: center; gap: 6px; margin-top: 10px; padding: 0 14px; min-height: 36px; border: 1px solid var(--si-hair); border-radius: var(--el-border-radius-base); color: var(--si-ink-2); font: var(--si-font-body); }
.acts :deep(.el-button > span) { gap: 6px; }
.foot { margin: 20px 0 0; color: var(--si-ink-3); font: var(--si-font-meta); text-align: center; }
/* 落入：小票卡从上方依次落进夹口 */
.drop { position: absolute; left: 50%; top: 40%; width: 46px; height: 32px; margin: -16px 0 0 -23px; box-sizing: border-box; padding: 8px 7px; border: 1.5px solid var(--si-m-inv); border-radius: 4px; background: var(--si-paper); box-shadow: var(--si-shadow-1); opacity: 0; animation: inv-drop 520ms var(--si-ease-out) both; animation-delay: calc(var(--i) * 45ms); }
.drop i { display: block; height: 3px; margin-bottom: 5px; border-radius: 2px; background: color-mix(in srgb, var(--si-m-inv) 38%, var(--si-surface-1)); }
.drop i + i { width: 60%; }
@keyframes inv-drop { 0% { opacity: 0; transform: translate(calc((var(--i) - 3.5) * 14px), -120px) rotate(calc((var(--i) - 3.5) * 4deg)); } 30% { opacity: 1; } 85% { opacity: 1; transform: translate(0, 8px) scale(.9); } 100% { opacity: 0; transform: translate(0, 14px) scale(.8); } }
.drop-more { position: absolute; right: 18px; top: 8px; padding: 0 8px; border-radius: var(--si-radius-badge); background: var(--si-m-inv-soft); color: var(--si-m-inv); font: var(--si-font-badge); line-height: 22px; }

/* ---- 工作区：窄带 + 清单预览 ---- */
.inv-work { display: flex; flex-direction: column; height: 100%; min-height: 0; }
.band { flex: none; display: flex; align-items: center; gap: 14px; height: 64px; padding: 0 24px 0 16px; border-bottom: 1px solid var(--si-hair); background: var(--si-surface-1); }
.band-illus { flex: none; width: 66px; height: 36px; }
.band-t { flex: 1; min-width: 0; display: flex; flex-direction: column; }
.band-t b { color: var(--si-ink-1); font: var(--si-font-h4); }
.band-t span { overflow: hidden; color: var(--si-ink-3); font: var(--si-font-caption); text-overflow: ellipsis; white-space: nowrap; }
.body { flex: 1; min-height: 0; overflow-y: auto; padding: 20px 24px 40px; }
.body > * { max-width: 1240px; margin-left: auto; margin-right: auto; }
.sum { display: flex; align-items: center; gap: 16px; min-height: 40px; margin-bottom: 12px; }
.sum-t { flex: 1; margin: 0; color: var(--si-ink-2); font: var(--si-font-body); font-size: calc(15px * var(--si-fs)); }
.sum-t b { color: var(--si-ink-1); font-weight: 600; }
.sum-t.wait { color: var(--si-ink-3); }
.hinted { padding: 2px 10px; border: 0; border-radius: var(--si-radius-badge); background: var(--si-warn-soft); color: var(--si-warn-ink); font: inherit; cursor: pointer; transition: box-shadow var(--si-d2) var(--si-ease-out); }
.hinted b { color: inherit; }
.hinted:hover { box-shadow: inset 0 0 0 1px var(--si-warn-line); }
.hinted.on { box-shadow: inset 0 0 0 1.5px var(--si-warn); }
.hinted:focus-visible, .all:focus-visible { outline: 2px solid var(--si-focus); outline-offset: 2px; }
.all { margin-left: 6px; padding: 0; border: 0; background: none; color: var(--si-link, var(--si-brand)); font: var(--si-font-meta); cursor: pointer; }
.acts { flex: none; display: flex; }
/* 下载完成：图标换成一枚勾，遮片从左向右退开（只动 transform） */
.dl-ic { position: relative; display: inline-grid; width: 15px; height: 15px; }
.dl-ic > * { grid-area: 1 / 1; }
.ic-a.gone { opacity: 0; }
.tick { width: 15px; height: 15px; overflow: visible; }
.tick path { fill: none; stroke: currentColor; stroke-width: 2.2; stroke-linecap: round; stroke-linejoin: round; }
.tick-m { fill: var(--el-color-primary); transform-box: fill-box; transform-origin: right; animation: inv-tick 420ms var(--si-ease-out) both; }
@keyframes inv-tick { to { transform: scaleX(0); } }
.el-button--primary:hover .tick-m { fill: var(--el-color-primary-light-3); }

/* 表格换皮：表头 13px 辅助色，行高 56，无竖线，无斑马纹 */
.tbl-wrap { overflow: hidden; border: 1px solid var(--si-hair); border-radius: var(--si-radius-md); background: var(--si-surface-1); }
.tbl { width: 100%; border-collapse: collapse; table-layout: fixed; font: var(--si-font-body); }
.tbl th, .tbl td { box-sizing: border-box; }
.tbl th { height: 44px; padding: 0 14px; border-bottom: 1px solid var(--si-hair); color: var(--si-ink-3); font: var(--si-font-meta); font-weight: 500; text-align: left; white-space: nowrap; }
.tbl td { position: relative; height: 56px; padding: 0 14px; border-bottom: 1px solid var(--si-hair-faint); overflow: hidden; color: var(--si-ink-2); text-overflow: ellipsis; white-space: nowrap; }
.tbl tbody tr:last-child td { border-bottom: 0; }
.tbl tbody tr:hover td { background: var(--si-hover-bg); }
.c-seq { width: 64px; } .c-kind { width: 112px; } .c-no { width: 204px; } .c-date { width: 116px; }
.c-amt { width: 128px; text-align: right !important; } .c-tax { width: 112px; } .c-rate { width: 80px; white-space: nowrap; } .c-hint { width: 232px; }
td.c-seller, td.c-amt { color: var(--si-ink-1); }
.mono { font-family: var(--si-font-mono); font-variant-numeric: tabular-nums; }
.num { font-variant-numeric: tabular-nums; }
.hint-b { display: inline-flex; align-items: center; gap: 6px; max-width: 100%; outline: none; }
.hint-b :deep(.si-badge) { min-width: 0; overflow: hidden; text-overflow: ellipsis; }
.hint-b:focus-visible { outline: 2px solid var(--si-focus); outline-offset: 2px; border-radius: var(--si-radius-badge); }
.more { flex: none; color: var(--si-warn-ink); font: var(--si-font-caption); }
.hint-full { max-width: 360px; }
.hint-full p { margin: 0; white-space: normal; }
.hint-full p + p { margin-top: 4px; }
.ok { color: var(--si-ink-4); }
.none { margin: 0; padding: 28px; color: var(--si-ink-3); text-align: center; font: var(--si-font-body); }
/* 识别中的占位行与扫描光条 */
.ph-t { color: var(--si-ink-3); font: var(--si-font-meta); }
.ph.cur .ph-t { color: var(--si-ink-1); }
.ph-bars { display: inline-flex; gap: 14px; margin-left: 24px; vertical-align: middle; }
.ph-bars i { display: block; width: 90px; height: 8px; border-radius: 4px; background: var(--si-surface-2); }
.ph-bars i:nth-child(2) { width: 140px; } .ph-bars i:nth-child(3) { width: 64px; }
.scan { position: absolute; inset: 0; overflow: hidden; pointer-events: none; }
.scan::after { content: ''; position: absolute; top: 0; bottom: 0; left: 0; width: 22%; background: linear-gradient(90deg, transparent, color-mix(in srgb, var(--si-brand-2) 22%, transparent) 60%, color-mix(in srgb, var(--si-brand-2) 55%, transparent) 96%, transparent); animation: inv-scan 1.4s var(--si-ease-io) infinite; }
.ph.cur td:first-child { box-shadow: inset 3px 0 0 var(--si-brand-2); }
@keyframes inv-scan { from { transform: translateX(-100%); } to { transform: translateX(560%); } }
html:is([data-fx="lite"], [data-fx="off"]) .scan { display: none; }

/* 未识别：折叠一行 */
.unrec { margin-top: 14px; border: 1px solid var(--si-hair); border-radius: var(--si-radius-md); background: var(--si-surface-1); }
.unrec summary { display: flex; align-items: center; gap: 6px; padding: 12px 16px; color: var(--si-ink-2); font: var(--si-font-body); list-style: none; cursor: pointer; }
.unrec summary::-webkit-details-marker { display: none; }
.unrec summary b { color: var(--si-ink-1); font-weight: 600; }
.chev { color: var(--si-ink-3); transition: transform var(--si-d2) var(--si-ease-out); }
.unrec[open] .chev { transform: rotate(90deg); }
.unrec ul { margin: 0; padding: 0 16px 12px 37px; list-style: none; }
.unrec li { display: flex; gap: 16px; padding: 6px 0; border-top: 1px solid var(--si-hair-faint); font: var(--si-font-meta); }
.unrec li b { flex: none; max-width: 40%; overflow: hidden; color: var(--si-ink-1); font-weight: 500; text-overflow: ellipsis; white-space: nowrap; }
.unrec li span { color: var(--si-ink-3); }
.keep { margin-top: 14px; color: var(--si-ink-3); font: var(--si-font-caption); }

/* 1366：号码不截断，让核对提示列窄一些（全文在悬停里） */
@media (max-width: 1400px) { .c-seq { width: 52px; } .c-kind { width: 96px; } .c-no { width: 206px; } .c-date { width: 106px; } .c-amt { width: 112px; } .c-tax { width: 96px; } .c-rate { width: 68px; } .c-hint { width: 172px; } }
</style>
