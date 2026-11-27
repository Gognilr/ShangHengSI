<script setup lang="ts">
// 发言泳道（澄明二版 6.13）：每位发言人一条泳道，按逐字稿时间戳画出说话段，播放头贯穿所有泳道。
// 点泳道任一段跳到那句话；方向键沿用播放条的规定（前后 5 秒）。不解码录音，只用逐字稿的时间戳。
import { computed } from 'vue'
import type { PreviewNode } from '@/api/preview'
import { stamp } from '@/utils/transcript'

const props = defineProps<{ nodes: PreviewNode[]; speakers: Record<string, string | null>; current: number; duration: number; activeLoc: string }>()
const emit = defineEmits<{ seek: [ms: number, loc?: string]; step: [deltaMs: number] }>()
const COLORS = ['var(--si-m-meet)', 'var(--si-info)', 'var(--si-k-addr)', 'var(--si-k-id)', 'var(--si-ai)', 'var(--si-brand)']
const total = computed(() => Math.max(props.duration, ...props.nodes.map(n => n.t_end_ms ?? 0), 1))
const pct = (ms: number) => `${Math.min(100, Math.max(0, ms / total.value * 100))}%`
const lanes = computed(() => {
  const ids = [...new Set(props.nodes.map(n => Number(n.speaker ?? 0)))].sort((a, b) => a - b)
  return ids.map(id => ({
    id, color: COLORS[Math.max(0, id - 1) % COLORS.length],
    name: props.speakers[String(id)] || `发言人 ${id}`,
    segs: props.nodes.filter(n => Number(n.speaker ?? 0) === id && n.t_start_ms != null).map(n => {
      const a = n.t_start_ms ?? 0, b = Math.max(n.t_end_ms ?? a, a)
      return { loc: n.loc, a, left: pct(a), width: `${Math.max(0.15, (b - a) / total.value * 100)}%`, title: `${stamp(a)} ${n.text ?? ''}` }
    }),
  }))
})
const axis = computed(() => Array.from({ length: 6 }, (_, i) => stamp(total.value * i / 5)).map(s => s.replace(/^00:/, '')))
function key(event: KeyboardEvent) {
  const step = event.key === 'ArrowRight' ? 5000 : event.key === 'ArrowLeft' ? -5000 : 0
  if (step) { event.preventDefault(); emit('step', step) }
}
</script>

<template>
  <div class="lane-wrap">
    <div class="lanes" role="group" :aria-label="`发言泳道，${lanes.length} 位发言人`" tabindex="0" @keydown="key">
      <div v-for="lane in lanes" :key="lane.id" class="lane" :style="{ '--lc': lane.color }">
        <span class="ln"><i />{{ lane.name }}</span>
        <span class="lt" />
        <button v-for="s in lane.segs" :key="s.loc" type="button" class="sg" :class="{ cur: s.loc === activeLoc }" :style="{ left: s.left, width: s.width }" :title="s.title" :aria-label="`${lane.name} ${stamp(s.a)}`" tabindex="-1" @click="emit('seek', s.a, s.loc)" />
      </div>
      <span v-if="lanes.length" class="ph" :style="{ left: pct(current), transform: `translateX(${-current / total * 96}px)` }" aria-hidden="true" />
    </div>
    <div class="lane-axis" aria-hidden="true"><span v-for="(t, i) in axis" :key="i">{{ t }}</span></div>
  </div>
</template>

<style scoped>
.lanes { position: relative; margin-top: 12px; padding-left: 96px; border-radius: var(--si-radius-control); }
.lanes:focus-visible { outline: 2px solid var(--si-ring); outline-offset: 4px; }
.lane { position: relative; display: flex; align-items: center; height: 26px; }
.lane + .lane { margin-top: 6px; }
.ln { position: absolute; left: -96px; width: 88px; display: flex; align-items: center; gap: 6px; overflow: hidden; color: var(--si-ink-2); font: var(--si-font-caption); white-space: nowrap; text-overflow: ellipsis; }
.ln i { flex: none; width: 8px; height: 8px; border-radius: 50%; background: var(--lc); }
.lt { position: absolute; inset: 8px 0; border-radius: 5px; background: var(--si-sunken); }
.sg { position: absolute; top: 5px; bottom: 5px; min-width: 2px; padding: 0; border: 0; border-radius: 4px; background: var(--lc); opacity: .75; cursor: pointer; transition: opacity var(--si-d2) var(--si-ease-out), transform var(--si-d2) var(--si-ease-out); }
.sg:hover { opacity: 1; transform: scaleY(1.25); }
.sg.cur { opacity: 1; box-shadow: 0 0 0 2px var(--si-surface-1), 0 0 0 3.5px var(--lc); }
.ph { position: absolute; top: -6px; bottom: -6px; width: 2px; margin-left: 96px; border-radius: 1px; background: var(--si-ink-1); pointer-events: none; }
.ph::before { content: ''; position: absolute; top: -4px; left: -4px; width: 10px; height: 10px; border-radius: 50%; background: var(--si-ink-1); }
.lane-axis { display: flex; justify-content: space-between; padding-left: 96px; margin-top: 8px; color: var(--si-ink-4); font: var(--si-font-caption); font-variant-numeric: tabular-nums; }
</style>
