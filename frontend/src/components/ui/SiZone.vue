<script setup lang="ts">
import { computed, type Component } from 'vue'
import { Calculator, CircleHelp, Lock, Sparkles } from '@lucide/vue'
import SiCount from '@/components/ui/SiCount.vue'

/**
 * 两个地带（澄明二版第三节第 2 条，ZoneHeader 扩展而来）：区块级底色，不是一行标题。
 * local 本机产出（浅青底，计算器或锁）：原文、本机计算、遮住胶囊、本机检查结论、逐字稿；
 * ai 模型产出（浅紫底，星点）：AI 建议、模型补查、纪要草稿、云端译文、对话回答；unverified 无法核验（中性）。
 * actions 插槽只放一个短按钮或一句说明；筛选分段控件放在地带内第一行，不挤进标题（F-00）。
 */
const props = withDefaults(defineProps<{ kind: 'local' | 'ai' | 'unverified'; title?: string; count?: number; lock?: boolean; icon?: Component }>(), { lock: false })
const TITLE = { local: '本机计算 · 结果确定', ai: 'AI 建议 · 请核实', unverified: '无法核验' } as const
const glyph = computed(() => props.icon ?? (props.kind === 'ai' ? Sparkles : props.kind === 'unverified' ? CircleHelp : props.lock ? Lock : Calculator))
</script>

<template>
  <section class="si-zone" :class="`z-${kind}`">
    <header class="zh">
      <component :is="glyph" :size="16" :stroke-width="1.75" aria-hidden="true" />
      <span class="zt">{{ title ?? TITLE[kind] }}</span>
      <span v-if="count !== undefined" class="zc"><SiCount :value="count" /></span>
      <span v-if="$slots.actions" class="za"><slot name="actions" /></span>
    </header>
    <p v-if="$slots.sub" class="zs"><slot name="sub" /></p>
    <slot />
  </section>
</template>

<style scoped>
.si-zone { position: relative; padding: 14px 16px 16px; border-radius: var(--si-radius-card); }
.z-local { background: var(--si-zone-l); box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--si-brand-line) 55%, transparent); }
.z-ai { background: var(--si-zone-m); box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--si-ai-line) 60%, transparent); }
.z-unverified { background: var(--si-card-2); box-shadow: inset 0 0 0 1px var(--si-hair); }
.zh { display: flex; align-items: center; gap: 8px; min-height: 28px; margin-bottom: 12px; font: var(--si-font-label); font-weight: 600; }
.z-local .zh { color: var(--si-blue-ink); }
.z-ai .zh { color: var(--si-ai-ink); }
.z-unverified .zh { color: var(--si-ink-3); }
.zc { display: inline-flex; align-items: center; height: 20px; padding: 0 7px; border-radius: var(--si-radius-badge); background: var(--si-surface-1); font: var(--si-font-badge); }
.za { display: flex; align-items: center; gap: 6px; margin-left: auto; font-weight: 400; }
.zs { margin: -8px 0 12px; font: var(--si-font-meta); color: var(--si-ink-3); }
.si-zone + .si-zone { margin-top: 14px; }
</style>
