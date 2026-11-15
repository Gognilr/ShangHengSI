<script setup lang="ts">
import { Check, X } from '@lucide/vue'
withDefaults(defineProps<{ active: number; status?: 'wait' | 'process' | 'finish' | 'error' | 'success'; titles?: string[]; ariaLabel?: string }>(),
  { status: 'process', titles: () => ['上传与设置', '确认脱敏', '审核中'], ariaLabel: '审核步骤' })
</script>
<template>
  <ol class="step-bar" :aria-label="ariaLabel">
    <li v-for="(title, index) in titles" :key="title" :class="{ done: index < active, current: index === active, error: index === active && status === 'error' }" :aria-current="index === active ? 'step' : undefined">
      <span class="number"><el-icon v-if="index < active"><Check :size="16" :stroke-width="1.75" /></el-icon><el-icon v-else-if="index === active && status === 'error'"><X :size="16" :stroke-width="1.75" /></el-icon><span v-else>{{ index + 1 }}</span></span>
      <span>{{ title }}</span>
      <span v-if="index < titles.length - 1" class="connector" aria-hidden="true"></span>
    </li>
  </ol>
</template>
<style scoped>
/* V3.4 visual.md 第五节：一行文字步骤，上下各一条细线；已完成灰底勾，当前松青实心序号 + 600 字，其余细线圈 */
.step-bar { display: flex; margin: 0 0 24px; padding: 12px 0; border-top: 1px solid var(--si-hair); border-bottom: 1px solid var(--si-hair); list-style: none; font: var(--si-font-label); }
li { flex: 1; min-width: 0; display: flex; align-items: center; color: var(--si-ink-3); }
li:last-child { flex: none; }
.connector { flex: 1; height: 1px; margin: 0 16px; background: var(--si-hair); }
.number { flex: none; width: 24px; height: 24px; margin-right: 10px; border: 1px solid var(--si-hair-strong); border-radius: 50%; box-sizing: border-box; display: grid; place-items: center; font: var(--si-font-code-sm); }
.current { font-weight: 600; color: var(--si-ink-1); }
.current .number { background: var(--si-brand); color: var(--si-white); border-color: var(--si-brand); }
.done { color: var(--si-ink-2); }
.done .number { color: var(--si-ink-2); background: var(--si-surface-2); border-color: transparent; }
.error { color: var(--si-danger-ink); }
.error .number { background: var(--si-danger); color: var(--si-white); border-color: var(--si-danger); }
</style>
