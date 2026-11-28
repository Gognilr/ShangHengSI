<script setup lang="ts">
import { computed, ref } from 'vue'
import { CircleCheck, FolderOpen, Hourglass, KeyRound, ScanText, Upload } from '@lucide/vue'
import type { BidFile, Bidder, BidPricing } from '@/api/bid'

// 投标人卡（澄明 8.5、第九节）：代号方块加字号（悬停显示全称）、文件数与页数、这一家的状态；
// 有严重问题时顶部一条 3px 红线、边框偏红；选中时青色光圈。整张卡可拖入文件。
const props = defineProps<{
  bidder: Bidder; files: BidFile[]; busy: boolean; phase: 'materials' | 'checking' | 'results'
  severe?: number; hint?: number; preliminary?: boolean; selected?: boolean; disabled?: boolean
  price?: BidPricing['bidders'][string]
}>()
const emit = defineEmits<{ open: []; select: []; drop: [event: DragEvent] }>()
const over = ref(false)
const code = computed(() => props.bidder.code.replace(/[（）()]/g, ''))
const pages = computed(() => props.bidder.pages ?? props.files.reduce((n, f) => n + (f.page_count ?? 0), 0))
const ocr = computed(() => props.bidder.ocr)
const ocrPending = computed(() => !!ocr.value && ocr.value.total > ocr.value.done)
// 材料卡只显示自己的读取和扫描进度，全项目检查进度在顶部工具条。
const state = computed(() => {
  const f = props.files
  if (!f.length) return { tone: 'idle', text: '还没有文件，拖到这里', icon: Upload }
  const failed = f.filter(x => x.state === 'failed')
  if (failed.some(x => x.needs_password)) return { tone: 'warn', text: '需要输入 PDF 密码', icon: KeyRound }
  if (failed.length) return { tone: 'warn', text: `${failed.length} 份文件读取失败`, icon: KeyRound }
  const waiting = f.filter(x => x.state === 'waiting').length
  if (waiting) return { tone: 'busy', text: props.busy ? `读取中 ${f.length - waiting} / ${f.length}` : '等待读取', icon: Hourglass, bar: (f.length - waiting) / f.length }
  if (ocrPending.value) return { tone: 'busy', text: `扫描页识别 ${ocr.value!.done} / ${ocr.value!.total}`, icon: ScanText, bar: ocr.value!.done / Math.max(1, ocr.value!.total) }
  return { tone: 'ok', text: '已就绪', icon: CircleCheck }
})
const bad = computed(() => props.phase === 'results' && (props.severe ?? 0) > 0)
const statusLabel = computed(() => {
  if (props.phase !== 'results') return state.value.text
  if (props.severe === undefined) return '正在读取结果'
  const label = props.severe ? `严重 ${props.severe}` : props.preliminary ? '初步，未列出严重问题' : '没有严重问题'
  return `${label}${props.hint ? `，提示 ${props.hint}` : ''}`
})
// 本机检查提取的报价（唱标价更正后以唱标价为准）：费率、下浮率自带 %，金额按币种标单位
const priceText = computed(() => {
  const p = props.price
  if (!p?.total) return p && p.totals_found.length > 1 ? `找到 ${p.totals_found.length} 个不同报价，请核对` : '未识别到报价'
  const unit = p.total.endsWith('%') ? '' : !p.currency || p.currency === 'CNY' ? ' 元' : ` ${p.currency}`
  return `${p.total}${unit}`
})
// 含税口径与税率：只显示投标文件中写明的（成对的不含税价、税率、含税价，或总价旁单独写的口径与税率），没写不猜
const taxText = computed(() => {
  const p = props.price
  if (!p?.total) return ''
  if (!p.tax_pairs?.length) return p.tax_rate ? `税率 ${p.tax_rate}` : ''
  const pair = p.tax_pairs.find(x => x.gross === p.total || x.net === p.total) ?? (p.tax_pairs.length === 1 ? p.tax_pairs[0] : undefined)
  if (!pair) return ''
  const other = pair.gross === p.total ? `不含税 ${pair.net}` : pair.net === p.total ? `含税 ${pair.gross}` : ''
  return [`税率 ${pair.rate}`, other].filter(Boolean).join(' · ')
})
function activate() { if (props.phase === 'results') emit('select'); else emit('open') }
function drop(event: DragEvent) { over.value = false; if (!props.disabled) emit('drop', event) }
</script>

<template>
  <article class="bc si-card" :class="{ bad, on: selected, over }" @dragover.prevent="over = !disabled" @dragleave.self="over = false" @drop.prevent.stop="drop">
    <span v-if="bad" class="severe-tag num">严重 {{ severe }}</span>
    <div class="bc-h">
      <span class="code" aria-hidden="true">{{ code }}</span>
      <b :title="bidder.name">{{ bidder.short || bidder.name }}</b>
    </div>
    <span v-if="phase === 'results' && severe !== undefined" class="price" :class="{ none: !price?.total }"><span class="price-k">{{ price?.manual_total ? '唱标价' : '报价' }}</span><b class="num" :title="priceText">{{ priceText }}</b><em v-if="price?.tax_basis" class="tax-tag">{{ price.tax_basis }}</em></span>
    <span v-if="phase === 'results' && severe !== undefined && taxText" class="tax num" :title="taxText">{{ taxText }}</span>
    <span v-else class="lines" aria-hidden="true"><i /><i /><i /></span>
    <span class="meta num">{{ files.length }} 个文件<template v-if="pages"> · {{ pages }} 页</template></span>
    <span v-if="phase === 'results'" class="bc-st">
      <span v-if="severe === undefined" class="hint">正在读取结果…</span>
      <template v-else>
        <span v-if="severe" class="si-badge is-hi"><i class="d" />严重 {{ severe }}</span>
        <span v-else class="si-badge">{{ preliminary ? '初步 · 未列出严重问题' : '没有严重问题' }}</span>
        <span v-if="hint" class="si-badge">提示 {{ hint }}</span>
      </template>
      <span v-if="ocrPending" class="ocr num"><ScanText :size="13" :stroke-width="1.75" />识别 {{ ocr!.done }} / {{ ocr!.total }}</span>
    </span>
    <span v-else class="bc-st" :class="state.tone"><component :is="state.icon" :size="15" :stroke-width="1.75" />{{ state.text }}</span>
    <span v-if="phase !== 'results' && state.bar !== undefined" class="prog" aria-hidden="true"><i :style="{ width: `${Math.max(6, state.bar * 100)}%` }" /></span>
    <button type="button" class="tile-action" :aria-pressed="phase === 'results' ? !!selected : undefined" :title="bidder.name"
      :aria-label="`投标人 ${code} ${bidder.name}，${statusLabel}`" @click="activate" />
    <button v-if="phase === 'results'" type="button" class="open" :aria-label="`${bidder.name} 的文件与签章页`" title="文件与签章页" @click="emit('open')"><FolderOpen :size="15" :stroke-width="1.75" /></button>
  </article>
</template>

<style scoped>
.bc { position: relative; display: flex; flex-direction: column; gap: 8px; min-height: 138px; padding: 14px 14px 10px; border-radius: var(--si-radius-control) var(--si-radius-control) 5px 5px; background: var(--si-paper);
  transition: border-color var(--si-d2) var(--si-ease-out), box-shadow var(--si-d3) var(--si-ease-out), transform var(--si-d3) var(--si-ease-out), opacity var(--si-d3) var(--si-ease-out); }
.bc:hover { transform: translateY(-3px); box-shadow: var(--si-shadow-2); border-color: var(--si-hair-strong); }
.bc:has(.tile-action:focus-visible) { box-shadow: var(--si-ring), var(--si-shadow-2); }
.bc.over { border-color: var(--si-brand-2); background: var(--si-blue-tint); }
.bc.bad { border-color: var(--si-danger-line); }
.bc.bad::before { content: ""; position: absolute; left: -1px; right: -1px; top: -1px; height: 3px; border-radius: var(--si-radius-control) var(--si-radius-control) 0 0; background: var(--si-danger); }
.severe-tag { position: absolute; right: 12px; top: -10px; padding: 1px 8px; border: 1px solid var(--si-danger); border-radius: var(--si-radius-badge); background: color-mix(in srgb, var(--si-danger) 14%, var(--si-paper)); color: var(--si-danger-ink); font: var(--si-font-label-sm); }
.bc.on { transform: translateY(-8px); box-shadow: 0 0 0 2px var(--si-m-bid), var(--si-shadow-2); border-color: var(--si-m-bid); }
html[data-fx="off"] .bc.on, html[data-fx="off"] .bc:hover { transform: none; }
/* 纸面上的几行字，像桌上摊开的一份标书 */
.price { display: flex; align-items: baseline; gap: 8px; min-width: 0; margin: 2px 0; }
.price-k { flex: none; color: var(--si-ink-3); font: var(--si-font-caption); }
.price b { overflow: hidden; color: var(--si-ink-1); font: var(--si-font-h4); text-overflow: ellipsis; white-space: nowrap; }
.tax-tag { flex: none; padding: 0 6px; border-radius: var(--si-radius-badge); background: var(--si-surface-2); color: var(--si-ink-2); font: var(--si-font-badge); font-style: normal; }
.tax { overflow: hidden; margin-top: -4px; color: var(--si-ink-3); font: var(--si-font-caption); text-overflow: ellipsis; white-space: nowrap; }
.price.none b { color: var(--si-ink-3); font: var(--si-font-caption); }
.lines { display: flex; flex-direction: column; gap: 5px; margin: 2px 0; }
.lines i { display: block; height: 5px; border-radius: 3px; background: var(--si-hair); }
.lines i:nth-child(2) { width: 70%; }
.lines i:nth-child(3) { width: 85%; }
.bc-h { display: flex; align-items: center; gap: 10px; min-width: 0; }
.bc:has(.open) .bc-h { padding-right: 30px; }
.code { flex: none; display: grid; place-items: center; width: 30px; height: 30px; border-radius: var(--si-radius-sm); background: var(--si-m-bid-soft); color: var(--si-m-bid); font: var(--si-font-code); font-weight: 600; }
.bc-h b { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font: var(--si-font-h3); color: var(--si-ink-1); }
.tile-action { position: absolute; inset: 0; width: 100%; padding: 0; border: 0; border-radius: inherit; background: transparent; cursor: pointer; }
.tile-action:focus-visible { outline: none; }
.open { position: absolute; top: 16px; right: 12px; display: grid; place-items: center; width: 28px; height: 28px; padding: 0; border: 0; border-radius: var(--si-radius-sm); background: transparent; color: var(--si-ink-3); cursor: pointer; }
.open:hover { background: var(--si-hover-bg); color: var(--si-ink-1); }
.open:focus-visible { outline: none; box-shadow: var(--si-ring); }
.meta { font: var(--si-font-meta); color: var(--si-ink-3); }
.bc-st { display: flex; min-height: 24px; align-items: center; flex-wrap: wrap; gap: 6px; padding-top: 6px; border-top: 1px solid var(--si-hair-faint); font: var(--si-font-meta); color: var(--si-ink-2); }
.bc-st.ok { color: var(--si-blue-ink); }
.bc-st.busy { color: var(--si-info-ink); }
.bc-st.warn { color: var(--si-warn-ink); }
.bc-st.idle { color: var(--si-ink-4); }
.ocr { display: inline-flex; align-items: center; gap: 4px; font: var(--si-font-caption); color: var(--si-info-ink); }
.prog { display: block; height: 4px; border-radius: 2px; background: var(--si-sunken); overflow: hidden; }
.prog i { display: block; height: 100%; border-radius: 2px; background: var(--si-progress-flow); background-size: 200% 100%; transition: width var(--si-d3) var(--si-ease-out); }
html[data-fx="full"] .prog i, html[data-fx="lite"] .prog i { animation: bc-flow 2.2s linear infinite; }
@keyframes bc-flow { from { background-position: 100% 0; } to { background-position: -100% 0; } }
</style>
