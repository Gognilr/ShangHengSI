<script setup lang="ts">
// 翻译入口的往返线（澄明二版 6.14）：开关决定线的颜色。
// 本机翻译：整条青色三站；云端翻译：出现遮住、复检两站，模型段变紫，回来在本机还原。切换时线当场重画。
import { computed, ref, watch } from 'vue'
import { useFx } from '@/utils/motion'

const props = defineProps<{ cloud: boolean }>()
const fx = useFx(), svg = ref<SVGSVGElement>()
const stations = computed(() => props.cloud ? ['读取', '确认遮住', '复检', '模型翻译', '还原'] : ['读取', '本机翻译', '完成'])
const xs = computed(() => props.cloud ? [10, 80, 150, 220, 290] : [10, 150, 290])
const zone = (name: string) => name === '模型翻译' ? 'var(--si-trip-m)' : 'var(--si-trip-l)'
// 线段颜色：进入模型翻译的一段为紫，从模型回来的一段前半紫后半青（回来在本机还原）
const segs = computed(() => xs.value.slice(1).map((x, i) => {
  const from = xs.value[i]!, name = stations.value[i + 1]!
  if (props.cloud && name === '还原') { const mid = (from + x) / 2; return [{ a: from, b: mid, c: 'var(--si-trip-m)' }, { a: mid, b: x, c: 'var(--si-trip-l)' }] }
  return [{ a: from, b: x, c: props.cloud && name === '模型翻译' ? 'var(--si-trip-m)' : 'var(--si-trip-l)' }]
}).flat())
watch(() => props.cloud, () => {
  if (fx.value === 'full') svg.value?.animate([{ opacity: .2, transform: 'translateX(-6px)' }, { opacity: 1, transform: 'none' }], { duration: 420, easing: 'cubic-bezier(.22,1,.36,1)' })
})
</script>

<template>
  <div class="tline" role="status">
    <svg ref="svg" viewBox="0 0 300 40" width="300" aria-hidden="true">
      <path v-for="(s, i) in segs" :key="`p${i}`" :d="`M${s.a} 14H${s.b}`" :stroke="s.c" stroke-width="3" stroke-linecap="round" />
      <g v-for="(name, i) in stations" :key="name">
        <circle :cx="xs[i]" cy="14" r="6" :fill="zone(name)" />
        <text :x="xs[i]" y="36" text-anchor="middle" font-size="11.5" fill="var(--si-ink-3)">{{ name }}</text>
      </g>
    </svg>
    <div class="says">
      <template v-if="cloud"><b>遮住、复检后发送，回来在本机还原</b><span>名称与金额换成代号后才交给模型</span></template>
      <template v-else><b>全程本机 · 不外发</b><span>在公司服务器上的本地翻译模型完成 · 万字约一小时</span></template>
    </div>
  </div>
</template>

<style scoped>
.tline { display: flex; align-items: center; gap: 20px; margin-top: 18px; padding: 18px 22px; border: 1px solid var(--si-hair); border-radius: 16px; background: var(--si-surface-1); }
.tline svg { flex: none; height: 40px; overflow: visible; }
.tline svg :is(path, circle) { transition: stroke var(--si-d4) var(--si-ease-out), fill var(--si-d4) var(--si-ease-out); }
.says { min-width: 0; }
.says b { display: block; color: var(--si-ink-1); font: var(--si-font-label); font-weight: 600; }
.says span { color: var(--si-ink-3); font: var(--si-font-caption); }
@media (max-width: 720px) { .tline { flex-direction: column; align-items: flex-start; } }
</style>
