<script setup lang="ts">
import type { Component } from 'vue'
// 页面级空状态（V3-A 4.2；V3.4 visual.md 第五节）：32px 淡墨图标 + 标题 + 一句原因 + 可选操作，左对齐于内容列；compact 用于表格内（居中）
withDefaults(defineProps<{ icon?: Component; title: string; description?: string; compact?: boolean }>(), { compact: false })
</script>

<template>
  <div class="empty-state" :class="{ compact }">
    <component :is="icon" v-if="icon && !compact" class="icon" :size="32" :stroke-width="1.5" aria-hidden="true" />
    <p class="title">{{ title }}</p>
    <p v-if="description" class="description">{{ description }}</p>
    <div v-if="$slots.default" class="actions"><slot /></div>
  </div>
</template>

<style scoped>
.empty-state { display: flex; flex-direction: column; align-items: flex-start; gap: 6px; padding: 40px 0; text-align: left; }
.icon { color: var(--si-ink-4); margin-bottom: 6px; }
.title { margin: 0; font: var(--si-font-h3); color: var(--si-ink-1); }
.description { margin: 0; max-width: 420px; font: var(--si-font-body); color: var(--si-ink-3); }
.actions { margin-top: 12px; }
.compact { align-items: center; padding: 24px 16px; gap: 4px; text-align: center; }
.compact .title { font: var(--si-font-body); font-weight: 600; }
.compact .description { font: var(--si-font-meta); }
.compact .actions { margin-top: 8px; }
</style>
