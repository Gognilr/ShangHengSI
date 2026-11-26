<script setup lang="ts">
import { Calculator } from '@lucide/vue'
import type { Calc } from '@/api/chat'
import { calcStatusLabel } from '@/utils/labels'

/** 一条本机计算（V31-U3 第二节；澄明 8.3 中性卡、青色计算器图标）：算式为还原后的文字；结果只显示，不进入任何发送内容。 */
defineProps<{ calc: Calc }>()
</script>

<template>
  <div class="calc-result">
    <div class="calc-h"><span class="calc-ic"><Calculator :size="15" :stroke-width="1.75" /></span><b>本机计算 · 结果确定</b></div>
    <div class="expression">{{ calc.expression }}</div>
    <div v-if="calc.status === 'ok' && calc.result !== null" class="result num">= {{ calc.result }}</div>
    <div v-else class="failed">{{ calcStatusLabel(calc.status) }}</div>
  </div>
</template>

<style scoped>
.calc-result { margin: 12px 0; padding: 14px 18px; border: 1px solid var(--si-hair-faint); border-radius: var(--si-radius-md); background: var(--si-card-2); white-space: normal; }
.calc-h { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; }
.calc-h b { color: var(--si-ink-1); font: var(--si-font-label); font-weight: 600; }
.calc-ic { display: inline-grid; flex: none; place-items: center; width: 28px; height: 28px; border-radius: var(--si-radius-sm); background: var(--si-blue-tint); color: var(--si-brand-2); }
.expression { font: var(--si-font-body); color: var(--si-ink-2); white-space: pre-wrap; overflow-wrap: anywhere; }
.result { margin-top: 2px; font: var(--si-font-h3); color: var(--si-ink-1); overflow-wrap: anywhere; }
.failed { font: var(--si-font-meta); color: var(--si-warn-ink); }
</style>
