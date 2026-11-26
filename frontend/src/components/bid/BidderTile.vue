<script setup lang="ts">
import { computed, ref } from 'vue'
import { CircleCheck, FolderOpen, Hourglass, KeyRound, ScanText, Upload } from '@lucide/vue'
import type { BidFile, Bidder } from '@/api/bid'

// 投标人卡（澄明 8.5、第九节）：代号方块加字号（悬停显示全称）、文件数与页数、这一家的状态；
// 有严重问题时顶部一条 3px 红线、边框偏红；选中时青色光圈。整张卡可拖入文件。
const props = defineProps<{
  bidder: Bidder; files: BidFile[]; busy: boolean; phase: 'materials' | 'checking' | 'results'
  severe?: number; hint?: number; selected?: boolean; disabled?: boolean
}>()
const emit = defineEmits<{ open: []; select: []; drop: [event: DragEvent] }>()
const over = ref(false)
const code = computed(() => props.bidder.code.replace(/[（）()]/g, ''))
const pages = computed(() => props.bidder.pages ?? props.files.reduce((n, f) => n + (f.page_count ?? 0), 0))
const ocr = computed(() => props.bidder.ocr)
const ocrPending = computed(() => !!ocr.value && ocr.value.total > ocr.value.done)
// 状态依次为：读取中、扫描页识别 23 / 76、已就绪；检查中只显示“检查中”；结果出来后显示严重 / 提示条数
const state = computed(() => {
  const f = props.files
  if (!f.length) return { tone: 'idle', text: '还没有文件，拖到这里', icon: Upload }
  const failed = f.filter(x => x.state === 'failed')
  if (failed.some(x => x.needs_password)) return { tone: 'warn', text: '需要输入 PDF 密码', icon: KeyRound }
  if (failed.length) return { tone: 'warn', text: `${failed.length} 份文件读取失败`, icon: KeyRound }
  const waiting = f.filter(x => x.state === 'waiting').length
  if (waiting) return { tone: 'busy', text: props.busy ? `读取中 ${f.length - waiting} / ${f.length}` : '等待读取', icon: Hourglass, bar: (f.length - waiting) / f.length }
  if (ocrPending.value) return { tone: 'busy', text: `扫描页识别 ${ocr.value!.done} / ${ocr.value!.total}`, icon: ScanText, bar: ocr.value!.done / Math.max(1, ocr.value!.total) }
  if (props.phase === 'checking') return { tone: 'busy', text: '检查中', icon: Hourglass, bar: -1 }
  return { tone: 'ok', text: '已就绪', icon: CircleCheck }
})
const bad = computed(() => props.phase === 'results' && (props.severe ?? 0) > 0)
function click() { if (props.phase === 'results') emit('select'); else emit('open') }
function drop(event: DragEvent) { over.value = false; if (!props.disabled) emit('drop', event) }
</script>

<template>
  <article class="bc si-card" :class="{ bad, on: selected, over }" role="button" tabindex="0" :aria-pressed="phase === 'results' ? !!selected : undefined"
    :aria-label="`投标人 ${code} ${bidder.name}`" @click="click" @keydown.enter="click" @dragover.prevent="over = !disabled" @dragleave.self="over = false" @drop.prevent.stop="drop">
    <div class="bc-h">
      <span class="code" aria-hidden="true">{{ code }}</span>
      <b :title="bidder.name">{{ bidder.short || bidder.name }}</b>
      <button v-if="phase === 'results'" type="button" class="open" :aria-label="`${bidder.name} 的文件与签章页`" title="文件与签章页" @click.stop="emit('open')"><FolderOpen :size="15" :stroke-width="1.75" /></button>
    </div>
    <span class="meta num">{{ files.length }} 个文件<template v-if="pages"> · {{ pages }} 页</template></span>
    <span v-if="phase === 'results'" class="bc-st">
      <span v-if="severe" class="si-badge is-hi"><i class="d" />严重 {{ severe }}</span>
      <span v-if="hint" class="si-badge">提示 {{ hint }}</span>
      <span v-if="!severe && !hint" class="si-badge">未列出问题</span>
      <span v-if="ocrPending" class="ocr num"><ScanText :size="13" :stroke-width="1.75" />识别 {{ ocr!.done }} / {{ ocr!.total }}</span>
    </span>
    <span v-else class="bc-st" :class="state.tone"><component :is="state.icon" :size="15" :stroke-width="1.75" />{{ state.text }}</span>
    <span v-if="phase !== 'results' && state.bar !== undefined" class="prog" :class="{ flow: state.bar < 0 }" aria-hidden="true"><i :style="{ width: state.bar < 0 ? '100%' : `${Math.max(6, state.bar * 100)}%` }" /></span>
  </article>
</template>

<style scoped>
.bc { position: relative; display: flex; flex-direction: column; gap: 8px; padding: 14px 16px; overflow: hidden; cursor: pointer; }
.bc:hover { transform: translateY(-3px); box-shadow: var(--si-shadow-2); border-color: var(--si-hair-strong); }
.bc:focus-visible { outline: none; box-shadow: var(--si-ring), var(--si-shadow-2); }
.bc.over { border-color: var(--si-brand-2); background: var(--si-blue-tint); }
.bc.bad { border-color: color-mix(in srgb, var(--si-danger) 40%, var(--si-hair)); }
.bc.bad::before { content: ""; position: absolute; left: 0; right: 0; top: 0; height: 3px; background: var(--si-danger); }
.bc.on { box-shadow: var(--si-ring), var(--si-shadow-2); border-color: var(--si-brand-2); }
.bc-h { display: flex; align-items: center; gap: 10px; min-width: 0; }
.code { flex: none; display: grid; place-items: center; width: 30px; height: 30px; border-radius: var(--si-radius-sm); background: var(--si-m-bid-soft); color: var(--si-m-bid); font: var(--si-font-code); font-weight: 600; }
.bc-h b { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font: var(--si-font-h3); color: var(--si-ink-1); }
.open { flex: none; display: grid; place-items: center; width: 28px; height: 28px; margin-left: auto; padding: 0; border: 0; border-radius: var(--si-radius-sm); background: transparent; color: var(--si-ink-4); cursor: pointer; }
.open:hover { background: var(--si-hover-bg); color: var(--si-ink-1); }
.meta { font: var(--si-font-caption); color: var(--si-ink-3); }
.bc-st { display: flex; align-items: center; flex-wrap: wrap; gap: 6px; font: var(--si-font-meta); color: var(--si-ink-2); }
.bc-st.ok { color: var(--si-blue-ink); }
.bc-st.busy { color: var(--si-info-ink); }
.bc-st.warn { color: var(--si-warn-ink); }
.bc-st.idle { color: var(--si-ink-4); }
.ocr { display: inline-flex; align-items: center; gap: 4px; font: var(--si-font-caption); color: var(--si-info-ink); }
.prog { display: block; height: 4px; border-radius: 2px; background: var(--si-sunken); overflow: hidden; }
.prog i { display: block; height: 100%; border-radius: 2px; background: var(--si-progress-flow); background-size: 200% 100%; transition: width var(--si-d3) var(--si-ease-out); }
html[data-fx="full"] .prog i, html[data-fx="lite"] .prog i { animation: bc-flow 2.2s linear infinite; }
.prog.flow i { opacity: .7; }
@keyframes bc-flow { from { background-position: 100% 0; } to { background-position: -100% 0; } }
</style>
