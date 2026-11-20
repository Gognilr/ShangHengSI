<script setup lang="ts">
import { Check, Loader, X } from '@lucide/vue'
// 澄明第九节：已完成为青色实心对勾，进行中为浅底并转动，模型步骤（ai）用紫色，未开始为占位色
withDefaults(defineProps<{ active: number; status?: 'wait' | 'process' | 'finish' | 'error' | 'success'; titles?: string[]; ariaLabel?: string; ai?: number[] }>(),
  { status: 'process', titles: () => ['上传与设置', '确认脱敏', '审核中'], ariaLabel: '审核步骤', ai: () => [] })
</script>
<template>
  <ol class="step-bar" :aria-label="ariaLabel">
    <li v-for="(title, index) in titles" :key="title" :class="{ done: index < active, current: index === active, error: index === active && status === 'error', ai: ai.includes(index) }" :aria-current="index === active ? 'step' : undefined">
      <span class="number"><Check v-if="index < active" :size="13" :stroke-width="2.5" /><X v-else-if="index === active && status === 'error'" :size="13" :stroke-width="2.5" /><Loader v-else-if="index === active && status === 'process'" class="spin" :size="13" :stroke-width="2.25" /><span v-else>{{ index + 1 }}</span></span>
      <span class="t">{{ title }}</span>
      <span v-if="index < titles.length - 1" class="connector" aria-hidden="true"></span>
    </li>
  </ol>
</template>
<style scoped>
.step-bar { display: flex; align-items: center; gap: 0; margin: 0; padding: 0; list-style: none; font: var(--si-font-label-sm); }
li { flex: none; display: flex; align-items: center; color: var(--si-ink-4); }
.connector { width: 24px; height: 2px; margin: 0 8px; border-radius: 1px; background: var(--si-hair); }
.number { flex: none; width: 22px; height: 22px; margin-right: 6px; border: 1.5px solid var(--si-hair-strong); border-radius: 50%; box-sizing: border-box; display: grid; place-items: center; font: var(--si-font-code-sm); transition: background-color var(--si-d2) var(--si-ease-out), border-color var(--si-d2) var(--si-ease-out); }
.current { color: var(--si-ink-1); font-weight: 600; }
.current .number { border-color: var(--si-blue); background: var(--si-blue-tint); color: var(--si-blue-ink); }
.current.ai .number { border-color: var(--si-ai); background: var(--si-ai-soft); color: var(--si-ai-ink); }
.done { color: var(--si-ink-2); }
.done .number { border-color: var(--si-brand); background: var(--si-brand); color: var(--si-white); }
.done + li .connector, .done .connector { background: var(--si-brand); }
.error { color: var(--si-danger-ink); }
.error .number { background: var(--si-danger); color: var(--si-white); border-color: var(--si-danger); }
.spin { animation: sb-spin 1.1s linear infinite; }
@keyframes sb-spin { to { transform: rotate(360deg); } }
html:is([data-fx="lite"], [data-fx="off"]) .spin { animation: none; }
</style>
