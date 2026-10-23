<script setup lang="ts">
import { Check, Close } from '@element-plus/icons-vue'
withDefaults(defineProps<{ active: number; status?: 'wait' | 'process' | 'finish' | 'error' | 'success' }>(), { status: 'process' })
const titles = ['上传与设置', '确认脱敏', '审核中']
</script>
<template>
  <ol class="step-bar" aria-label="审核步骤">
    <li v-for="(title, index) in titles" :key="title" :class="{ done: index < active, current: index === active, error: index === active && status === 'error' }" :aria-current="index === active ? 'step' : undefined">
      <span class="number"><el-icon v-if="index < active"><Check /></el-icon><el-icon v-else-if="index === active && status === 'error'"><Close /></el-icon><span v-else>{{ index + 1 }}</span></span>
      <span>{{ title }}</span>
    </li>
  </ol>
</template>
<style scoped>
.step-bar { display: flex; margin: 0 0 24px; padding: 0; list-style: none; }
li { position: relative; flex: 1; display: flex; align-items: center; justify-content: center; gap: 12px; color: var(--el-text-color-secondary); }
li + li::before { content: ''; position: absolute; left: -18%; width: 36%; height: 1px; background: var(--el-border-color); }
.number { z-index: 1; width: 32px; height: 32px; border: 1px solid var(--el-border-color); border-radius: 50%; background: var(--el-bg-color-page); display: grid; place-items: center; }
.current { font-weight: 600; color: var(--el-color-primary); }
.current .number { background: var(--el-color-primary); color: var(--el-color-white); border-color: var(--el-color-primary); box-shadow: 0 0 0 4px var(--el-color-primary-light-9); }
.done { color: var(--el-color-primary); }
.done .number { color: var(--el-color-primary); background: var(--el-color-primary-light-9); border-color: var(--el-color-primary-light-7); }
.done + li::before { background: var(--el-color-primary); }
.error { color: var(--el-color-danger); }
.error .number { background: var(--el-color-danger); border-color: var(--el-color-danger); box-shadow: 0 0 0 4px var(--el-color-danger-light-9); }
</style>
