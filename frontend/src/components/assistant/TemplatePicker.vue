<script setup lang="ts">
import type { Template } from '@/api/assistant'
import { ASSISTANT_TEMPLATE } from '@/utils/labels'
defineProps<{ modelValue: Template | null; cards?: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [value: Template | null] }>()
const descriptions: Record<string, string> = { translate: '分段翻译并核对原文', summary: '提取文件中的重点', reply: '根据来函起草回复', crosscheck: '核对多份文件的一致性', compare: '对齐报价项目，在本地比较价格' }
</script>
<template>
  <div :class="cards ? 'template-cards' : 'templates'" aria-label="选择模板">
    <el-button v-for="(label, key) in ASSISTANT_TEMPLATE" :key="key" :type="modelValue === key ? 'primary' : 'default'" :plain="modelValue === key" @click="emit('update:modelValue', modelValue === key ? null : key as Template)">
      <span>{{ label }}<span v-if="cards" class="description">{{ descriptions[key] }}</span></span>
    </el-button>
  </div>
</template>
<style scoped>
.templates { display: flex; flex-wrap: wrap; gap: 8px; }
.templates :deep(.el-button), .template-cards :deep(.el-button) { margin-left: 0; }
.template-cards { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }
.template-cards :deep(.el-button) { height: auto; min-height: 80px; padding: 16px; white-space: normal; }
.description { display: block; font: var(--si-font-meta); color: var(--el-text-color-secondary); margin-top: 8px; }
</style>
