<script setup lang="ts">
import type { Component } from 'vue'
// 页面级空状态（V3-A 4.2）：图标 + 标题 + 一句原因 + 可选操作；compact 用于表格内
withDefaults(defineProps<{ icon?: Component; title: string; description?: string; compact?: boolean }>(), { compact: false })
</script>

<template>
  <div class="empty-state" :class="{ compact }">
    <component :is="icon" v-if="icon && !compact" class="icon" :size="48" :stroke-width="1.5" aria-hidden="true" />
    <p class="title">{{ title }}</p>
    <p v-if="description" class="description">{{ description }}</p>
    <div v-if="$slots.default" class="actions"><slot /></div>
  </div>
</template>

<style scoped>
.empty-state { display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 8px; padding: 48px 24px; text-align: center; }
.icon { color: var(--el-text-color-secondary); margin-bottom: 8px; }
.title { margin: 0; font: var(--si-font-h4); color: var(--el-text-color-primary); }
.description { margin: 0; max-width: 420px; font: var(--si-font-body); color: var(--el-text-color-secondary); }
.actions { margin-top: 12px; }
.compact { padding: 24px 16px; gap: 4px; }
.compact .title { font: var(--si-font-body); font-weight: 600; }
.compact .description { font: var(--si-font-meta); }
.compact .actions { margin-top: 8px; }
</style>
