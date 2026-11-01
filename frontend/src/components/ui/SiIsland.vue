<script setup lang="ts">
import { computed, toRef } from 'vue'
import { useLoop } from '@/utils/motion'
import { useAppearanceStore } from '@/stores/appearance'

/** 灵动岛（V31-U1 第五节）：黑色全圆角胶囊，宽度按状态变化；等待中的旋转只在完整特效播放。 */
const props = withDefaults(defineProps<{ state: 'idle' | 'busy' | 'done' | 'error'; text: string; progress?: number | null; actionLabel?: string }>(), { progress: null, actionLabel: undefined })
const emit = defineEmits<{ action: [] }>()

const store = useAppearanceStore()
const spinning = useLoop(toRef(() => props.state === 'busy' && props.progress == null))
const RING = 2 * Math.PI * 7
const dash = computed(() => {
  const ratio = props.progress == null ? 0.28 : Math.min(1, Math.max(0, props.progress))
  return `${RING * ratio} ${RING}`
})
</script>

<template>
  <div class="si-island" :class="[`is-${state}`, { glow: state === 'busy' && store.fxResolved === 'full' }]" role="status" aria-live="polite">
    <span v-if="state === 'busy'" class="ring" :class="{ spin: spinning }" aria-hidden="true">
      <svg viewBox="0 0 16 16"><circle class="track" cx="8" cy="8" r="7" /><circle class="arc" cx="8" cy="8" r="7" :stroke-dasharray="dash" /></svg>
    </span>
    <span v-else class="dot" aria-hidden="true" />
    <span class="text">{{ text }}</span>
    <button v-if="state === 'done' && actionLabel" type="button" class="action" @click="emit('action')">{{ actionLabel }}</button>
  </div>
</template>

<style scoped>
.si-island { display: inline-flex; align-items: center; gap: 8px; box-sizing: border-box; width: max-content; max-width: 240px; height: 30px; padding: 0 12px; overflow: hidden; border-radius: 999px; background: var(--si-island-bg); color: var(--si-island-ink); font: var(--si-font-label-sm); white-space: nowrap; transition: max-width var(--si-dur-2) var(--si-spring); }
.is-busy, .is-error { max-width: 320px; }
.is-done { max-width: 380px; padding-right: 4px; }
.glow { box-shadow: 0 0 0 1px color-mix(in srgb, var(--si-cyan) 35%, transparent), 0 0 16px color-mix(in srgb, var(--si-cyan) 25%, transparent); }
.text { min-width: 0; overflow: hidden; text-overflow: ellipsis; }
.dot { flex: none; width: 8px; height: 8px; border-radius: 50%; background: var(--si-ok); }
.is-error .dot { background: var(--si-danger); }
.ring { flex: none; width: 16px; height: 16px; }
.ring svg { display: block; width: 100%; height: 100%; transform: rotate(-90deg); }
.ring.spin { animation: si-island-spin 1s linear infinite; }
.track, .arc { fill: none; stroke-width: 2; }
.track { stroke: color-mix(in srgb, var(--si-island-ink) 22%, transparent); }
.arc { stroke: var(--si-cyan); stroke-linecap: round; }
.action { flex: none; height: 22px; padding: 0 10px; border: 0; border-radius: 999px; background: color-mix(in srgb, var(--si-island-ink) 18%, transparent); color: var(--si-island-ink); font: var(--si-font-label-sm); cursor: pointer; transition: background-color var(--si-dur-1) var(--si-ease); }
.action:hover { background: color-mix(in srgb, var(--si-island-ink) 28%, transparent); }
.action:focus-visible { outline: 2px solid var(--si-focus); outline-offset: 1px; }
@keyframes si-island-spin { to { transform: rotate(360deg); } }
</style>
