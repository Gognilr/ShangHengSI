<script setup lang="ts">
import { computed } from 'vue'

// 核对进度条（V34Q-27 第三部分第 6 条）：已核 / 待核 / 已排除三段的细条，旁边保留“已核 / 共”数字。
const props = defineProps<{ done: number; total: number; excluded: number }>()
const all = computed(() => Math.max(1, props.total + props.excluded))
const pct = (n: number) => `${(n / all.value) * 100}%`
const label = computed(() => `已核 ${props.done} · 待核 ${props.total - props.done}` + (props.excluded ? ` · 已排除 ${props.excluded}` : ''))
</script>

<template>
  <span class="pb" :title="label">
    <span class="bar" role="img" :aria-label="label">
      <i class="done" :style="{ width: pct(done) }" /><i class="left" :style="{ width: pct(total - done) }" /><i class="ex" :style="{ width: pct(excluded) }" />
    </span>
    <span class="num"><slot>{{ done }} / {{ total }}</slot></span>
  </span>
</template>

<style scoped>
.pb { display: inline-flex; align-items: center; gap: 10px; }
.bar { display: inline-flex; width: 64px; height: 4px; border-radius: 2px; overflow: hidden; background: var(--si-hair); }
.bar i { display: block; height: 100%; }
.done { background: var(--si-brand); }
.left { background: var(--si-hair-strong); }
.ex { background: repeating-linear-gradient(90deg, var(--si-ink-5) 0 2px, transparent 2px 4px); }
.num { min-width: 52px; text-align: right; font-variant-numeric: tabular-nums; }
</style>
