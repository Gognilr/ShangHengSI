<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'

/**
 * 问题地形条（二版 6.8，由 V31-U3 的文档光谱条加强）：纸面右缘外 16px 的竖轨，宽 10px，常驻显示；
 * 刻度按严重程度着色、高 6px，位置按节点序号（不按像素），视窗框按滚动比例。
 * 悬停时刻度放大 1.8 倍并在左侧浮出问题标题；点刻度纸面滚到原文并闪一次。
 * 只给鼠标用（aria-hidden），键盘用户通过问题栏定位。
 */
export interface SpectrumTick { pos: number; kind: 'high' | 'medium' | 'low' | 'local' | 'cap' | 'chg' | 'chg-u'; loc: string; fid?: string; title?: string }
const props = defineProps<{ ticks: SpectrumTick[]; scroller: HTMLElement | undefined }>()
const emit = defineEmits<{ pick: [tick: SpectrumTick]; seek: [ratio: number] }>()

const MERGE_OVER = 400
// 替换刻度过多时按 1% 区间合并；问题与本地校验刻度不合并
const shown = computed(() => {
  const caps = props.ticks.filter(t => t.kind === 'cap')
  if (caps.length <= MERGE_OVER) return props.ticks
  const seen = new Set<number>()
  const merged = caps.filter(t => { const k = Math.floor(t.pos * 100); if (seen.has(k)) return false; seen.add(k); return true })
  return [...merged, ...props.ticks.filter(t => t.kind !== 'cap')]
})
// 问题刻度画在最上层
const ordered = computed(() => [...shown.value].sort((a, b) => rank(a) - rank(b)))
function rank(t: SpectrumTick) { return t.kind === 'cap' ? 0 : t.kind === 'local' || t.kind === 'chg' ? 1 : t.kind === 'low' ? 2 : t.kind === 'medium' ? 3 : 4 }
const hover = ref<SpectrumTick | null>(null)

const frame = ref({ top: 0, height: 1 })
let raf = 0
function update() {
  raf = 0
  const el = props.scroller
  if (!el || !el.scrollHeight) return
  frame.value = { top: el.scrollTop / el.scrollHeight, height: Math.min(1, el.clientHeight / el.scrollHeight) }
}
function schedule() { if (!raf) raf = requestAnimationFrame(update) }
let resize: ResizeObserver | undefined
watch(() => props.scroller, (el, old) => {
  old?.removeEventListener('scroll', schedule)
  resize?.disconnect()
  if (!el) return
  el.addEventListener('scroll', schedule, { passive: true })
  resize = new ResizeObserver(schedule)
  resize.observe(el)
  if (el.firstElementChild) resize.observe(el.firstElementChild)
  schedule()
}, { immediate: true })
watch(() => props.ticks, schedule)
onBeforeUnmount(() => { props.scroller?.removeEventListener('scroll', schedule); resize?.disconnect(); cancelAnimationFrame(raf) })

function onTrack(event: MouseEvent) {
  const box = (event.currentTarget as HTMLElement).getBoundingClientRect()
  emit('seek', Math.min(1, Math.max(0, (event.clientY - box.top) / box.height)))
}
</script>

<template>
  <div class="spectrum" aria-hidden="true" @click="onTrack" @mouseleave="hover = null">
    <span class="frame" :style="{ top: `${frame.top * 100}%`, height: `${frame.height * 100}%` }" />
    <span v-for="(t, i) in ordered" :key="i" class="tick" :class="`t-${t.kind}`" :style="{ top: `${t.pos * 100}%` }"
      @click.stop="emit('pick', t)" @mouseenter="hover = t.title ? t : null" @mouseleave="hover = null" />
    <span v-if="hover" class="tip" :style="{ top: `${hover.pos * 100}%` }">{{ hover.title }}</span>
  </div>
</template>

<style scoped>
/* 纸面最宽 760、居中：竖轨贴在纸面右缘外 16px；画布窄时退到画布右缘 */
.spectrum { position: absolute; top: 8px; bottom: 8px; left: min(calc(50% + 396px), calc(100% - 16px)); width: 10px; border-radius: 5px; background: var(--si-hair-faint); cursor: pointer; z-index: 2; }
.frame { position: absolute; left: -3px; right: -3px; min-height: 10px; border: 1px solid var(--si-hair-strong); border-radius: 6px; pointer-events: none; box-sizing: border-box; }
.tick { position: absolute; left: 0; right: 0; height: 2px; border-radius: 1px; transform: translateY(-50%); transition: transform var(--si-d2) var(--si-ease-spring); }
.t-high, .t-medium, .t-low, .t-chg, .t-chg-u { height: 6px; border-radius: 3px; }
.t-high, .t-medium, .t-low { transform-origin: center; }
.t-high:hover, .t-medium:hover, .t-low:hover, .t-chg:hover, .t-chg-u:hover { transform: translateY(-50%) scale(1.8); }
.t-high { background: var(--si-danger); }
.t-medium { background: var(--si-warn); }
.t-low { background: var(--si-info); }
.t-local { background: var(--si-ink-3); }
.t-cap { background: color-mix(in srgb, var(--si-brand) 40%, transparent); }
/* 对比上一稿（V35-D-02）：改动为主色，未留痕为警示色 */
.t-chg { background: var(--si-brand); }
.t-chg-u { background: var(--si-warn); }
.tip { position: absolute; right: 22px; max-width: 320px; padding: 6px 10px; overflow: hidden; border: 1px solid var(--si-hair); border-radius: var(--si-radius-sm); background: var(--si-surface-pop); box-shadow: var(--si-shadow-3); color: var(--si-ink-1); font: var(--si-font-caption); text-overflow: ellipsis; white-space: nowrap; transform: translateY(-50%); pointer-events: none; }
html[data-fx="full"] .tip { animation: sp-tip var(--si-d2) var(--si-ease-out); }
@keyframes sp-tip { from { opacity: 0; transform: translate(4px, -50%); } }
</style>
