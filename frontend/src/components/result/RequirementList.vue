<script setup lang="ts">
import { ref } from 'vue'
import type { RequirementResult } from '@/api/runs'
import EmptyState from '@/components/EmptyState.vue'
import { VERDICT_TAG, reqSourceLabel, unableReasonLabel, verdictLabel } from '@/utils/labels'

/** 审核要求逐条结论（V31-U3 3.5）；信息已脱敏而无法判断的，可补充信息后追问（V31-E 5.2）。 */
defineProps<{ items: RequirementResult[]; askEnabled: boolean }>()
const emit = defineEmits<{ ask: [prefill: string] }>()
const expanded = ref(new Set<string>())
function toggle(id: string) {
  const next = new Set(expanded.value)
  if (!next.delete(id)) next.add(id)
  expanded.value = next
}
function prefill(r: RequirementResult) {
  const chars = Array.from(r.req_text)
  return `关于审核要求 ${r.req_id}（${chars.length > 30 ? `${chars.slice(0, 30).join('')}…` : r.req_text}）：补充信息——`
}
</script>

<template>
  <EmptyState v-if="!items.length" compact title="本轮没有逐条结论" />
  <ul v-else class="reqs">
    <li v-for="r in items" :key="`${r.req_source}:${r.req_id}`" class="req">
      <div class="head">
        <span class="id">{{ r.req_id }}<template v-if="r.dimension"> · {{ r.dimension }}</template></span>
        <span class="source">{{ reqSourceLabel(r.req_source) }}</span>
        <el-tag :type="VERDICT_TAG[r.verdict] ?? 'info'" size="small" round class="verdict">{{ verdictLabel(r.verdict) }}</el-tag>
        <span v-if="r.verdict === 'unable' && r.unable_reason" class="reason">{{ unableReasonLabel(r.unable_reason) }}</span>
      </div>
      <button type="button" class="text" :class="{ open: expanded.has(r.req_id) }" :aria-expanded="expanded.has(r.req_id)" @click="toggle(r.req_id)">{{ r.req_text }}</button>
      <p v-if="r.note" class="note">{{ r.note }}</p>
      <el-button v-if="r.verdict === 'unable' && r.unable_reason === 'redacted' && askEnabled" link type="primary" size="small" class="ask" @click="emit('ask', prefill(r))">补充信息后追问</el-button>
    </li>
  </ul>
</template>

<style scoped>
.reqs { margin: 0; padding: 0; list-style: none; }
.req { padding: 10px 0; border-bottom: 1px solid var(--si-hair); }
.head { display: flex; align-items: center; gap: 8px; }
.id { font: var(--si-font-label); color: var(--el-text-color-primary); }
.source { font: var(--si-font-caption); color: var(--si-ink-3); }
.verdict { margin-left: auto; }
.reason { font: var(--si-font-caption); color: var(--si-ink-3); }
.text { display: -webkit-box; width: 100%; margin-top: 4px; padding: 0; border: 0; background: transparent; color: var(--si-ink-2); font: var(--si-font-body); text-align: left; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; cursor: pointer; overflow-wrap: anywhere; }
.text.open { display: block; -webkit-line-clamp: unset; white-space: pre-wrap; }
.note { margin: 4px 0 0; font: var(--si-font-meta); color: var(--si-ink-3); white-space: pre-wrap; overflow-wrap: anywhere; }
.ask { margin-top: 4px; padding: 0; }
</style>
