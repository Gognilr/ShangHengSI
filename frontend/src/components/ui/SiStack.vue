<script setup lang="ts">
import { computed } from 'vue'

/**
 * 分段条（澄明二版第四节）：七类脱敏、用途、存储等构成。图例在下，点一段（或图例）只看这一类，再点取消。
 * 段的颜色：kind 用七类色，否则用 color（颜色变量）。v-model:picked 为选中的 key。
 */
export interface StackSeg { key: string; label: string; value: number; kind?: 'org' | 'person' | 'contact' | 'id' | 'addr' | 'amount' | 'other'; color?: string }
const props = withDefaults(defineProps<{ segments: StackSeg[]; picked?: string | null; legend?: boolean; pickable?: boolean }>(), { picked: null, legend: true, pickable: true })
const emit = defineEmits<{ 'update:picked': [key: string | null] }>()
const shown = computed(() => props.segments.filter(s => s.value > 0))
const total = computed(() => shown.value.reduce((n, s) => n + s.value, 0))
const fill = (s: StackSeg) => s.kind ? `var(--si-k-${s.kind})` : s.color ?? 'var(--si-ink-4)'
function pick(key: string) { if (props.pickable) emit('update:picked', props.picked === key ? null : key) }
</script>

<template>
  <div class="si-stack">
    <div class="bar" :class="{ pick: picked }" role="img" :aria-label="shown.map(s => `${s.label} ${s.value}`).join('，')">
      <i v-for="s in shown" :key="s.key" :class="{ on: picked === s.key }" :style="{ flexGrow: s.value, background: fill(s) }" :title="`${s.label} ${s.value}（${Math.round(s.value / total * 100)}%）`" @click="pick(s.key)" />
    </div>
    <div v-if="legend" class="lg" :class="{ pick: picked }">
      <component :is="pickable ? 'button' : 'span'" v-for="s in shown" :key="s.key" :type="pickable ? 'button' : undefined" :class="{ on: picked === s.key }" :aria-pressed="pickable ? picked === s.key : undefined" @click="pick(s.key)">
        <i :style="{ background: fill(s) }" />{{ s.label }}<b>{{ s.value }}</b>
      </component>
    </div>
  </div>
</template>

<style scoped>
.bar { display: flex; gap: 2px; height: 10px; overflow: hidden; border-radius: 5px; background: var(--si-sunken); }
.bar > i { display: block; flex-basis: 0; min-width: 3px; height: 100%; cursor: pointer; transition: opacity var(--si-d2) var(--si-ease-out); }
.bar.pick > i { opacity: .28; }
.bar.pick > i.on { opacity: 1; }
.lg { display: flex; flex-wrap: wrap; gap: 6px 14px; margin-top: 10px; font: var(--si-font-meta); color: var(--si-ink-2); }
.lg > * { display: inline-flex; align-items: center; gap: 6px; margin: 0 -4px; padding: 0 4px; border: 0; border-radius: var(--si-radius-tag); background: none; color: inherit; font: inherit; cursor: pointer; }
.lg > span { cursor: default; }
.lg > button:hover { background: var(--si-hover-bg); }
.lg i { width: 8px; height: 8px; border-radius: 2px; }
.lg b { font-family: var(--si-font-mono); font-weight: 500; color: var(--si-ink-1); }
.lg.pick > * { opacity: .5; }
.lg.pick > .on { opacity: 1; }
</style>
