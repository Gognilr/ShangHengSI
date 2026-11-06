<script setup lang="ts">
// V31-U5F-04：全屏确认 / 校对弹窗的顶栏：明显的“返回”按钮（Esc 同效），返回不丢已保存的处理
import { ArrowLeft } from '@lucide/vue'
defineProps<{ title: string; back: string }>()
const emit = defineEmits<{ close: [] }>()
</script>
<template>
  <div class="back-head">
    <el-button @click="emit('close')"><ArrowLeft :size="16" :stroke-width="1.75" /><span class="back-label">{{ back }}</span></el-button>
    <span class="back-title">{{ title }}</span>
    <span class="back-hint">Esc</span>
  </div>
</template>
<style scoped>
.back-head { display: flex; align-items: center; gap: 12px; min-width: 0; }
.back-label { margin-left: 4px; }
.back-title { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font: var(--si-font-h2); color: var(--si-ink-1); }
.back-hint { padding: 0 6px; border: 1px solid var(--si-hair-strong); border-radius: 4px; color: var(--si-ink-3); font: var(--si-font-caption); }
</style>
