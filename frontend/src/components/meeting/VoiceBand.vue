<script setup lang="ts">
// 声纹带（澄明二版 6.12）：会议纪要入口的主图。静止时是一条起伏的声纹，指针靠近的柱子变深抬高；
// 录音中（传入 levels）变成实时声波。中间放录音按钮的位置由插槽决定。
import { computed, ref } from 'vue'
import { useFx } from '@/utils/motion'

const props = defineProps<{ levels?: number[]; paused?: boolean }>()
const N = 96
const fx = useFx()
const strip = ref<HTMLElement>()
const near = ref<number[]>([])
const base = Array.from({ length: N }, (_, i) => 10 + Math.round(Math.abs(Math.sin(i * .55) * 40 + Math.sin(i * .17) * 26 + Math.cos(i * 1.3) * 10)))
const heights = computed(() => props.levels
  ? Array.from({ length: N }, (_, i) => props.paused ? 6 : Math.round(6 + 84 * Math.min(1, props.levels![i * props.levels!.length / N | 0] ?? 0)))
  : base)
function move(event: PointerEvent) {
  if (props.levels || fx.value !== 'full' || !strip.value) return
  const rect = strip.value.getBoundingClientRect(), x = event.clientX - rect.left
  near.value = Array.from(strip.value.children).map(el => Math.abs((el as HTMLElement).offsetLeft - x))
}
const scale = (i: number) => { const d = near.value[i]; return d !== undefined && d < 90 ? 1 + (90 - d) / 90 * .6 : 1 }
</script>

<template>
  <div ref="strip" class="vb-strip" :class="{ 'is-live': !!levels }" aria-hidden="true" @pointermove="move" @pointerleave="near = []">
    <i v-for="(h, i) in heights" :key="i" :class="{ near: (near[i] ?? 999) < 60 }" :style="{ '--h': `${h}px`, transform: levels ? undefined : `scaleY(${scale(i)})` }" />
  </div>
</template>

<style scoped>
.vb-strip { position: relative; display: flex; align-items: center; justify-content: space-between; gap: 3px; height: 120px; padding: 0 26px; overflow: hidden; border-radius: 60px; background: var(--si-m-meet-soft); }
.vb-strip i { display: block; width: 4px; height: var(--h); border-radius: 2px; background: color-mix(in srgb, var(--si-m-meet) 45%, transparent); transform-origin: center; transition: transform var(--si-d2) var(--si-ease-out), height 160ms linear, background-color var(--si-d2) var(--si-ease-out); }
.vb-strip i.near { background: var(--si-m-meet); }
.vb-strip.is-live i { background: var(--si-m-meet); }
</style>
