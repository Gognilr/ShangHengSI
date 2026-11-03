<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'

/**
 * 文档光谱条（V31-U3 3.3）：画布右缘的竖轨，刻度位置按节点序号（不按像素），视窗框按滚动比例。
 * 只给鼠标用（aria-hidden），键盘用户通过问题栏定位。
 */
export interface SpectrumTick { pos: number; kind: 'high' | 'medium' | 'low' | 'local' | 'cap'; loc: string; fid?: string }
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
function rank(t: SpectrumTick) { return t.kind === 'cap' ? 0 : t.kind === 'local' ? 1 : 2 }

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
  <div class="spectrum" aria-hidden="true" @click="onTrack">
    <span class="frame" :style="{ top: `${frame.top * 100}%`, height: `${frame.height * 100}%` }" />
    <span v-for="(t, i) in ordered" :key="i" class="tick" :class="`t-${t.kind}`" :style="{ top: `${t.pos * 100}%` }" @click.stop="emit('pick', t)" />
  </div>
</template>

<style scoped>
.spectrum { position: absolute; top: 8px; bottom: 8px; right: 4px; width: 8px; border-radius: 999px; background: var(--si-surface-2); cursor: pointer; z-index: 2; }
.frame { position: absolute; left: -2px; right: -2px; min-height: 8px; border: 1px solid var(--si-ink-3); border-radius: 999px; pointer-events: none; box-sizing: border-box; }
.tick { position: absolute; left: 0; right: 0; height: 2px; transform: translateY(-50%); }
.tick:hover { transform: translateY(-50%) scaleY(2); }
.t-high, .t-medium, .t-low { height: 3px; }
.t-high { background: var(--si-danger); }
.t-medium { background: var(--si-warn); }
.t-low { background: var(--si-info); }
.t-local { background: var(--si-ink-3); }
.t-cap { background: color-mix(in srgb, var(--el-color-primary) 40%, transparent); }
</style>
