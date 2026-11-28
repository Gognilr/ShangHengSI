<script setup lang="ts">
import { ref, watch } from 'vue'
import { ZoomIn } from '@lucide/vue'
import { bidApi } from '@/api/bid'

// 原件页图（澄明 8.5 证据栏）：命中处由服务器用琥珀色浅底框出；服务器现场渲染这一页时显示白纸、页码和顶部流动细线，
// 图到达后模糊淡入（si-blur-in）；点开看大图。
const props = defineProps<{ projectId: string; fileId: string; page: number; loc?: string | null; alt: string }>()
const emit = defineEmits<{ zoom: [] }>()
const loaded = ref(false), failed = ref(false)
watch(() => [props.fileId, props.page, props.loc], () => { loaded.value = false; failed.value = false })
</script>

<template>
  <button type="button" class="shot" :class="{ loaded }" :aria-label="`${alt}，查看大图`" @click="emit('zoom')">
    <span v-if="!loaded && !failed" class="pending" aria-hidden="true"><i class="si-progress-flow" /><span class="num">第 {{ page }} 页</span></span>
    <span v-if="failed" class="failed">这一页暂时显示不了，可到原件中查看</span>
    <img v-show="!failed" :key="`${fileId}:${page}:${loc}`" :class="{ 'si-blur-in': loaded }" :src="bidApi.pageUrl(projectId, fileId, page, 760, loc)" :alt="alt" loading="lazy" @load="loaded = true" @error="failed = true">
    <span class="zoom" aria-hidden="true"><ZoomIn :size="14" :stroke-width="1.75" />大图</span>
  </button>
</template>

<style scoped>
.shot { position: relative; display: block; width: 100%; min-height: 220px; max-height: 560px; padding: 0; border: 0; border-radius: var(--si-radius-tag); background: var(--si-paper); box-shadow: var(--si-paper-shadow); overflow: auto; cursor: zoom-in; }
.shot img { display: block; width: 100%; }
.pending { position: absolute; inset: 0; display: grid; place-items: center; color: var(--si-ink-4); font: var(--si-font-meta); }
.pending i { position: absolute; left: 0; right: 0; top: 0; height: 2px; }
.failed { display: grid; place-items: center; min-height: 220px; padding: 24px; font: var(--si-font-meta); color: var(--si-ink-3); }
.zoom { position: absolute; right: 8px; top: 8px; display: inline-flex; align-items: center; gap: 4px; padding: 3px 8px; border-radius: var(--si-radius-badge); background: var(--si-toast-bg); color: var(--si-toast-ink); font: var(--si-font-badge); opacity: 0; transition: opacity var(--si-d2) var(--si-ease-out); }
.shot:hover .zoom, .shot:focus-visible .zoom { opacity: .9; }
.shot:focus-visible { outline: none; box-shadow: var(--si-ring), var(--si-paper-shadow); }
</style>
