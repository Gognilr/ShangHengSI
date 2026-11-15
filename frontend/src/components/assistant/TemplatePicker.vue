<script setup lang="ts">
// 模板快捷标签（V31-U5 2.1）：点一下只选中、进入输入框的模板行，再点取消；不弹文件窗口（V34Q-09）。
// 说明与所需文件数显示在输入框的模板行（V34-A-04），这里不再用悬停提示。翻译与会议纪要是侧栏独立菜单，这里不列
import type { Template } from '@/api/assistant'
import { ASSISTANT_TEMPLATE } from '@/utils/labels'
defineProps<{ modelValue: Template | null; chips?: boolean; asrEnabled?: boolean }>()
const CHAT: Template[] = ['summary', 'reply', 'crosscheck', 'compare']
const emit = defineEmits<{ 'update:modelValue': [value: Template | null] }>()
</script>
<template>
  <div class="template-chips" role="group" aria-label="常用模板">
    <span class="lead">常用</span>
    <button v-for="key in CHAT" :key="key" type="button" class="chip" :class="{ active: modelValue === key }" :aria-pressed="modelValue === key" @click="emit('update:modelValue', modelValue === key ? null : key)">{{ ASSISTANT_TEMPLATE[key] }}</button>
  </div>
</template>
<style scoped>
.template-chips { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.lead { margin-right: 4px; font: var(--si-font-meta); color: var(--si-ink-4); }
.chip { height: 30px; padding: 0 14px; border: 1px solid var(--si-hair); border-radius: 15px; background: transparent; color: var(--si-ink-2); font: var(--si-font-label); cursor: pointer; transition: background-color var(--si-dur-1) var(--si-ease), color var(--si-dur-1) var(--si-ease), border-color var(--si-dur-1) var(--si-ease); }
.chip:hover:not(:disabled) { background: var(--si-hover-bg); color: var(--si-ink-1); }
.chip.active { border-color: var(--si-hair-strong); background: var(--si-select-bg); color: var(--si-ink-1); }
.chip:disabled { cursor: not-allowed; opacity: .5; }
</style>
