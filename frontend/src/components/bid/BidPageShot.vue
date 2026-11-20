<script setup lang="ts">
import { ref, watch } from 'vue'
import { ZoomIn } from '@lucide/vue'
import { bidApi } from '@/api/bid'

// 原件页图（澄明 8.5 证据栏）：命中处由服务器用琥珀色浅底框出；加载完前是骨架，点开看大图。
const props = defineProps<{ projectId: string; fileId: string; page: number; loc?: string | null; alt: string }>()
const emit = defineEmits<{ zoom: [] }>()
const loaded = ref(false), failed = ref(false)
watch(() => [props.fileId, props.page, props.loc], () => { loaded.value = false; failed.value = false })
</script>

<template>
  <button type="button" class="shot" :class="{ loaded }" :aria-label="`${alt}，查看大图`" @click="emit('zoom')">
    <span v-if="!loaded && !failed" class="skeleton" aria-hidden="true" />
    <span v-if="failed" class="failed">这一页暂时显示不了，可到原件中查看</span>
    <img v-show="!failed" :key="`${fileId}:${page}:${loc}`" :src="bidApi.pageUrl(projectId, fileId, page, 760, loc)" :alt="alt" loading="lazy" @load="loaded = true" @error="failed = true">
    <span class="zoom" aria-hidden="true"><ZoomIn :size="14" :stroke-width="1.75" />大图</span>
  </button>
</template>

<style scoped>
.shot { position: relative; display: block; width: 100%; min-height: 220px; max-height: 560px; padding: 0; border: 0; border-radius: 6px; background: var(--si-paper); box-shadow: var(--si-paper-shadow); overflow: auto; cursor: zoom-in; }
.shot img { display: block; width: 100%; }
.skeleton { position: absolute; inset: 0; background: linear-gradient(90deg, var(--si-surface-2) 25%, color-mix(in srgb, var(--si-surface-2) 55%, var(--si-surface-1)) 50%, var(--si-surface-2) 75%) 0 0 / 200% 100%; animation: shot-sheen 1.4s linear infinite; }
@keyframes shot-sheen { to { background-position: -200% 0; } }
html:is([data-fx="lite"], [data-fx="off"]) .skeleton { animation: none; }
.failed { display: grid; place-items: center; min-height: 220px; padding: 24px; font: var(--si-font-meta); color: var(--si-ink-3); }
.zoom { position: absolute; right: 8px; top: 8px; display: inline-flex; align-items: center; gap: 4px; padding: 3px 8px; border-radius: 999px; background: var(--si-toast-bg); color: var(--si-toast-ink); font: var(--si-font-badge); opacity: 0; transition: opacity var(--si-d2) var(--si-ease-out); }
.shot:hover .zoom, .shot:focus-visible .zoom { opacity: .9; }
.shot:focus-visible { outline: none; box-shadow: var(--si-ring), var(--si-paper-shadow); }
</style>
