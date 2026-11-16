<script setup lang="ts">
import { Check } from '@lucide/vue'
// 状态“圆点 + 文字”（visual.md 第五节）：进行中松青空心环，完成墨色小对勾，待处理赭黄圆点，其余淡墨圆点
defineProps<{ tone: 'run' | 'warn' | 'ok' | 'idle'; label: string }>()
</script>

<template>
  <span class="bid-status" :class="tone">
    <Check v-if="tone === 'ok'" class="mark" :size="13" :stroke-width="2" aria-hidden="true" />
    <i v-else class="mark" aria-hidden="true" />
    {{ label }}
  </span>
</template>

<style scoped>
.bid-status { display: inline-flex; align-items: center; gap: 6px; font: var(--si-font-meta); color: var(--si-ink-2); white-space: nowrap; }
.mark { flex: none; }
i.mark { width: 6px; height: 6px; border-radius: 50%; background: var(--si-ink-4); }
.warn i.mark { background: var(--si-warn); }
.run i.mark { width: 8px; height: 8px; box-sizing: border-box; background: transparent; border: 1.5px solid var(--si-brand); border-right-color: transparent; animation: bid-spin 1s linear infinite; }
.ok .mark { color: var(--si-ink-2); }
.warn { color: var(--si-warn-ink); }
@keyframes bid-spin { to { transform: rotate(360deg); } }
</style>
