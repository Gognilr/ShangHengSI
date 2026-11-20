<script setup lang="ts">
import type { Component } from 'vue'
// 空状态与就地状态（澄明 8.10）：浅色圆内放图标 + 一句标题 + 一句说明 + 可选按钮，居中；不写“暂无数据”。
// tone：brand 没有数据（衡青浅底）；neutral 功能未启用 / 无权限（中性）；danger 出错（红色浅底，说明写原因和怎么办）。
// compact 用于表格内、侧栏等窄处：小圆、小字。
withDefaults(defineProps<{ icon?: Component; title: string; description?: string; compact?: boolean; tone?: 'brand' | 'neutral' | 'danger' }>(),
  { compact: false, tone: 'brand' })
</script>

<template>
  <div class="empty-state" :class="[{ compact }, `tone-${tone}`]" :role="tone === 'danger' ? 'alert' : undefined">
    <span v-if="icon" class="ring" aria-hidden="true"><component :is="icon" :size="compact ? 18 : 24" :stroke-width="1.75" /></span>
    <p class="title">{{ title }}</p>
    <p v-if="description" class="description">{{ description }}</p>
    <div v-if="$slots.default" class="actions"><slot /></div>
  </div>
</template>

<style scoped>
.empty-state { display: flex; flex-direction: column; align-items: center; padding: 40px 24px; text-align: center; }
.ring { display: grid; place-items: center; width: 56px; height: 56px; margin-bottom: 14px; border-radius: 50%; background: var(--si-blue-tint); color: var(--si-brand-2); }
.tone-neutral .ring { background: var(--si-hover-bg); color: var(--si-ink-3); }
.tone-danger .ring { background: var(--si-danger-soft); color: var(--si-danger); }
.title { margin: 0; font: var(--si-font-h3); color: var(--si-ink-1); }
.description { margin: 8px 0 0; max-width: 420px; font: var(--si-font-meta); color: var(--si-ink-3); }
.actions { display: flex; gap: 8px; justify-content: center; margin-top: 16px; }
.compact { padding: 24px 16px; }
.compact .ring { width: 40px; height: 40px; margin-bottom: 10px; }
.compact .title { font: var(--si-font-body); font-weight: 600; }
.compact .description { margin-top: 4px; }
.compact .actions { margin-top: 10px; }
</style>
