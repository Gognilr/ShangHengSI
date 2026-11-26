<script setup lang="ts">
import { computed } from 'vue'

/**
 * 迷你柱形（澄明二版第四节）：按天的计数。周末降透明度；零值画 2px 底线；还没到的日子画浅底线；每根带悬停提示。
 * color 传一个颜色变量，例如 var(--si-ai)；bad 的那天用红色。
 */
export interface SparkDay { value: number; label: string; weekend?: boolean; future?: boolean; bad?: boolean }
const props = withDefaults(defineProps<{ days: SparkDay[]; color?: string; height?: number; ariaLabel?: string }>(), { color: 'var(--si-ai)', height: 28 })
const max = computed(() => Math.max(1, ...props.days.map(d => d.value)))
const total = computed(() => props.days.reduce((s, d) => s + (d.future ? 0 : d.value), 0))
</script>

<template>
  <span class="si-spark" :style="{ '--sc': color, height: `${height}px` }" role="img" :aria-label="ariaLabel ?? `合计 ${total}`">
    <i v-for="(d, i) in days" :key="i" :class="{ we: d.weekend, z: !d.value && !d.future, fu: d.future, bad: d.bad }"
       :style="d.value && !d.future ? { height: `${Math.max(3, d.value / max * height)}px` } : undefined" :title="`${d.label}：${d.future ? '—' : d.value}`" />
  </span>
</template>

<style scoped>
.si-spark { display: inline-flex; align-items: flex-end; gap: 2px; }
i { display: block; width: 4px; min-height: 2px; border-radius: 2px 2px 0 0; background: var(--sc); }
i.we { opacity: .45; }
i.z { height: 2px; border-radius: 1px; background: var(--si-hair-strong); }
i.fu { height: 2px; background: var(--si-hair); opacity: .6; }
i.bad { background: var(--si-danger); }
i:hover { outline: 2px solid color-mix(in srgb, var(--sc) 35%, transparent); }
</style>
