<script setup lang="ts">
import { computed } from 'vue'

/** 环形用量（澄明二版第四节）：直径 40、线宽 4，超过 85% 变琥珀；中间写百分数，label 给读屏与悬停。 */
const props = withDefaults(defineProps<{ value: number; label?: string; color?: string }>(), { color: 'var(--si-ai)' })
const pct = computed(() => Math.max(0, Math.min(100, Math.round(props.value))))
const C = 2 * Math.PI * 17
</script>

<template>
  <span class="si-ring" :class="{ hot: pct > 85 }" :style="{ '--rc': color }" role="img" :aria-label="`${label ?? '用量'} ${pct}%`" :title="label">
    <svg viewBox="0 0 40 40" aria-hidden="true"><circle class="rb" cx="20" cy="20" r="17" /><circle class="rv" cx="20" cy="20" r="17" :stroke-dasharray="`${pct / 100 * C} ${C}`" /></svg>
    <b aria-hidden="true">{{ pct }}</b>
  </span>
</template>

<style scoped>
.si-ring { position: relative; flex: none; display: inline-block; width: 40px; height: 40px; }
svg { width: 40px; height: 40px; transform: rotate(-90deg); }
.rb { fill: none; stroke: var(--si-sunken); stroke-width: 4; }
.rv { fill: none; stroke: var(--rc); stroke-width: 4; stroke-linecap: round; transition: stroke-dasharray var(--si-d4) var(--si-ease-out); }
.hot { --rc: var(--si-warn) !important; }
b { position: absolute; inset: 0; display: grid; place-items: center; font: 500 calc(10.5px * var(--si-fs)) var(--si-font-mono); color: var(--si-ink-2); }
</style>
