<script setup lang="ts">
import { computed } from 'vue'
import { Calculator, Sparkles } from '@lucide/vue'
import SiCount from '@/components/ui/SiCount.vue'

/** 两种结论的分区标题（V31-U3 0.3）：本地计算 · 结果确定 / AI 建议 · 请核实 / 无法核验；small 用于本机计算卡片。 */
const props = withDefaults(defineProps<{ kind: 'local' | 'ai' | 'unverified'; count?: number; small?: boolean }>(), { small: false })
const TITLE = { local: '本地计算 · 结果确定', ai: 'AI 建议 · 请核实', unverified: '无法核验' } as const
const title = computed(() => props.small && props.kind === 'local' ? '本机计算' : TITLE[props.kind])
</script>

<template>
  <div class="zone-header" :class="[`zone-${kind}`, { small }]">
    <component :is="kind === 'ai' ? Sparkles : Calculator" class="icon" :size="small ? 12 : 14" :stroke-width="1.75" aria-hidden="true" />
    <span class="title">{{ title }}</span>
    <span v-if="count !== undefined" class="count"><SiCount :value="count" /></span>
    <span class="actions"><slot /></span>
  </div>
</template>

<style scoped>
.zone-header { display: flex; align-items: center; gap: 6px; min-height: 28px; font: var(--si-font-label); color: var(--el-text-color-primary); }
.zone-header.small { min-height: 20px; font: var(--si-font-label-sm); }
.icon { flex: none; color: var(--si-ink-2); }
.zone-ai .icon { color: var(--si-ai); }
.zone-unverified, .zone-unverified .icon { color: var(--si-ink-3); }
.count { color: var(--si-ink-3); font-variant-numeric: tabular-nums; }
.actions { display: flex; align-items: center; gap: 8px; margin-left: auto; }
.actions:empty { display: none; }
</style>
