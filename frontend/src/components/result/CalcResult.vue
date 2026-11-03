<script setup lang="ts">
import type { Calc } from '@/api/chat'
import ZoneHeader from '@/components/ui/ZoneHeader.vue'
import { calcStatusLabel } from '@/utils/labels'

/** 一条本机计算（V31-U3 第二节）：算式为还原后的文字；结果只显示，不进入任何发送内容。 */
defineProps<{ calc: Calc }>()
</script>

<template>
  <div class="calc-result">
    <ZoneHeader kind="local" small />
    <div class="expression">{{ calc.expression }}</div>
    <div v-if="calc.status === 'ok' && calc.result !== null" class="result">= {{ calc.result }}</div>
    <div v-else class="failed">{{ calcStatusLabel(calc.status) }}</div>
  </div>
</template>

<style scoped>
.calc-result { margin: 8px 0; padding: 8px 12px; background: var(--si-surface-2); border-radius: var(--si-radius-card); border-left: 2px solid var(--si-ink-3); white-space: normal; }
.expression { margin-top: 2px; font: var(--si-font-body); color: var(--el-text-color-primary); white-space: pre-wrap; overflow-wrap: anywhere; }
.result { font: var(--si-font-h3); font-variant-numeric: tabular-nums; color: var(--el-text-color-primary); overflow-wrap: anywhere; }
.failed { font: var(--si-font-meta); color: var(--si-warn-ink); }
</style>
