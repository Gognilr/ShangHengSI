<script setup lang="ts">
// 自动保存的原位状态（V31-U5F-06）：保存中 / 已保存 / 保存失败·重试
import type { SaveStatus } from '@/utils/autosave'
defineProps<{ status: SaveStatus; message?: string }>()
defineEmits<{ retry: [] }>()
</script>

<template>
  <span class="save-state" :class="status" role="status" aria-live="polite">
    <template v-if="status === 'saving'">保存中…</template>
    <template v-else-if="status === 'saved'">已保存</template>
    <template v-else-if="status === 'error'">保存失败{{ message ? `：${message}` : '' }}<button type="button" class="retry" @click="$emit('retry')">重试</button></template>
  </span>
</template>

<style scoped>
.save-state { font: var(--si-font-meta); color: var(--el-text-color-secondary); margin-left: 12px; }
.save-state.saved { color: var(--el-color-success); }
.save-state.error { color: var(--el-color-danger); }
.retry { margin-left: 6px; padding: 0; border: 0; background: none; color: var(--el-color-primary); font: inherit; cursor: pointer; }
</style>
