<script setup lang="ts">
// 模板快捷标签（V31-U5 2.1）：点一下进入输入框，再点取消；未启用转写时“会议纪要”置灰并写原因
import type { Template } from '@/api/assistant'
import { ASSISTANT_TEMPLATE } from '@/utils/labels'
defineProps<{ modelValue: Template | null; chips?: boolean; asrEnabled?: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [value: Template | null] }>()
const descriptions: Record<string, string> = { translate: '分段翻译并核对原文', summary: '提取文件中的重点', reply: '根据来函起草回复', crosscheck: '核对多份文件的一致性', compare: '对齐报价项目，在本地比较价格', minutes: '把录音整理为议题、决议与待办' }
</script>
<template>
  <div class="template-chips" role="group" aria-label="模板">
    <el-tooltip v-for="(label, key) in ASSISTANT_TEMPLATE" :key="key" :content="key === 'minutes' && !asrEnabled ? '录音转写未启用，请联系管理员' : descriptions[key]" placement="bottom">
      <button type="button" class="chip" :class="{ active: modelValue === key }" :disabled="key === 'minutes' && !asrEnabled" :aria-pressed="modelValue === key" @click="emit('update:modelValue', modelValue === key ? null : key as Template)">{{ label }}</button>
    </el-tooltip>
  </div>
</template>
<style scoped>
.template-chips { display: flex; flex-wrap: wrap; justify-content: center; gap: 8px; }
.chip { padding: 5px 14px; border: 1px solid var(--si-hair-strong); border-radius: 16px; background: transparent; color: var(--si-ink-2); font: var(--si-font-label); cursor: pointer; transition: background-color var(--si-dur-1) var(--si-ease), color var(--si-dur-1) var(--si-ease), border-color var(--si-dur-1) var(--si-ease); }
.chip:hover:not(:disabled) { background: var(--si-surface-2); color: var(--si-ink-1); }
.chip.active { border-color: var(--si-blue); background: color-mix(in srgb, var(--si-blue) 12%, transparent); color: var(--si-blue-ink); }
.chip:disabled { cursor: not-allowed; opacity: .5; }
</style>
