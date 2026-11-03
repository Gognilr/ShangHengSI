<script setup lang="ts">
import { computed } from 'vue'
import type { Template } from '@/api/assistant'
import { ASSISTANT_TEMPLATE } from '@/utils/labels'
const props = defineProps<{ modelValue: Template | null; cards?: boolean; asrEnabled?: boolean }>()
// 下拉只列可用模板；卡片把未启用转写的“会议纪要”置灰并写原因，让用户知道有这个功能（V31Q-92）
const templates = computed(() => Object.fromEntries(Object.entries(ASSISTANT_TEMPLATE).filter(([key]) => key !== 'minutes' || props.asrEnabled)))
const emit = defineEmits<{ 'update:modelValue': [value: Template | null] }>()
const descriptions: Record<string, string> = { translate: '分段翻译并核对原文', summary: '提取文件中的重点', reply: '根据来函起草回复', crosscheck: '核对多份文件的一致性', compare: '对齐报价项目，在本地比较价格', minutes: '把录音整理为议题、决议与待办' }
</script>
<template>
  <div v-if="cards" class="template-cards" aria-label="选择模板">
    <el-button v-for="(label, key) in ASSISTANT_TEMPLATE" :key="key" :type="modelValue === key ? 'primary' : 'default'" :plain="modelValue === key" :disabled="key === 'minutes' && !asrEnabled" @click="emit('update:modelValue', modelValue === key ? null : key as Template)">
      <span>{{ label }}<span class="description">{{ key === 'minutes' && !asrEnabled ? '录音转写未启用，请联系管理员' : descriptions[key] }}</span></span>
    </el-button>
  </div>
  <!-- 输入框下方一行用下拉选择，清空即不用模板 -->
  <el-select v-else class="template-select" :model-value="modelValue ?? undefined" clearable placeholder="不用模板" aria-label="选择模板" @update:model-value="emit('update:modelValue', ($event || null) as Template | null)">
    <el-option v-for="(label, key) in templates" :key="key" :value="key" :label="label" />
  </el-select>
</template>
<style scoped>
.template-cards :deep(.el-button) { margin-left: 0; }
.template-cards { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }
.template-cards :deep(.el-button) { height: auto; min-height: 80px; padding: 16px; white-space: normal; }
.description { display: block; font: var(--si-font-meta); color: var(--el-text-color-secondary); margin-top: 8px; }
.template-select { width: 128px; }
</style>
