<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { TriangleAlert } from '@lucide/vue'
import type { RunDetail } from '@/api/runs'
import MarkedText from '@/components/result/MarkedText.vue'
import { aiInstructionNotice } from '@/utils/labels'

/** 问题栏顶部的说明（V31-U3 3.4）：制裁、覆盖说明、L-21 提醒、文件摘要与限制事项；问题数与等级筛选在问题栏的分段控件上。 */
const props = defineProps<{ run: RunDetail | null; localOnly: boolean }>()
const emit = defineEmits<{ aiNotice: [] }>()

const SANCTIONS_SHORT: Record<string, string> = { none: '无明显风险', attention: '需注意', high: '高风险' }
const aiInstructions = computed(() => props.run?.coverage?.ai_instructions ?? 0)
const notes = computed(() => props.run?.coverage?.notes ?? [])
const detailsOpen = ref(false)
watch(() => props.run?.id, () => { detailsOpen.value = false })
</script>

<template>
  <section class="rail-summary" :class="{ open: detailsOpen }">
    <p v-if="localOnly" class="local">未做 AI 审核 · 仅本地校验</p>
    <template v-else-if="run">
      <p v-if="run.status === 'failed'" class="local">本次审核失败，未取得有效 AI 结论</p>
      <div v-else-if="run.sanctions.level" class="line1">
        <el-popover v-if="run.sanctions.level" trigger="click" placement="bottom-end" :width="320">
          <template #reference>
            <button type="button" class="sanctions" :class="{ high: run.sanctions.level === 'high' }">制裁：{{ SANCTIONS_SHORT[run.sanctions.level] ?? run.sanctions.level }}</button>
          </template>
          <ul v-if="run.sanctions.reasons.length" class="reasons"><li v-for="(r, i) in run.sanctions.reasons" :key="i">{{ r }}</li></ul>
          <p class="hint">未联网核查，基于模型自身知识，需合规人员确认最新制裁与出口管制规定</p>
        </el-popover>
      </div>
      <p v-if="run.status === 'failed'" class="hint">请重新审核；本地校验结果可在“本地计算”中查看。</p>
      <p v-if="run.status !== 'failed' && run.coverage?.delta && notes[0]" class="delta-note">{{ notes[0] }}</p>
      <p v-else-if="run.coverage?.summary" class="coverage" :title="run.coverage.summary">{{ run.coverage.summary }}</p>
      <button v-if="aiInstructions > 0" type="button" class="ai-notice" @click="emit('aiNotice')">
        <TriangleAlert :size="14" :stroke-width="1.75" aria-hidden="true" /><span>{{ aiInstructionNotice(aiInstructions) }}</span>
      </button>
      <template v-if="run.doc_summary || notes.length">
        <el-button link type="primary" size="small" class="details-toggle" :aria-expanded="detailsOpen" @click="detailsOpen = !detailsOpen">
          {{ detailsOpen ? '收起文件摘要与限制事项' : `文件摘要与限制事项${notes.length ? `（限制事项 ${notes.length} 条）` : ''}` }}
        </el-button>
        <div v-if="detailsOpen" class="details">
          <MarkedText v-if="run.doc_summary" class="doc-summary" :text="run.doc_summary" :marks="run.doc_summary_marks" summary />
          <ul v-if="notes.length"><li v-for="(n, i) in notes" :key="i">{{ n }}</li></ul>
        </div>
      </template>
    </template>
  </section>
</template>

<style scoped>
.rail-summary { flex: none; display: flex; flex-direction: column; gap: 4px; }
.rail-summary:empty { display: none; }
.rail-summary.open { max-height: 50%; overflow-y: auto; }
.local { margin: 0; font: var(--si-font-h3); color: var(--el-text-color-primary); }
.line1 { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.total { display: flex; align-items: baseline; gap: 4px; font: var(--si-font-meta); color: var(--si-ink-2); }
.total b { font: var(--si-font-stat); color: var(--el-text-color-primary); }
.counts { display: flex; gap: 6px; }
.count { display: inline-flex; align-items: center; gap: 4px; height: 24px; padding: 0 8px; border: 1px solid var(--si-hair); border-radius: 999px; background: transparent; color: var(--el-text-color-primary); font: var(--si-font-label-sm); font-variant-numeric: tabular-nums; cursor: pointer; }
.count.active { border-color: var(--el-color-primary); background: var(--el-color-primary-light-9); }
.count i { width: 7px; height: 7px; border-radius: 50%; background: var(--si-info); }
.sev-high i { background: var(--si-danger); }
.sev-medium i { background: var(--si-warn); }
.sanctions { height: 24px; padding: 0 8px; border: 1px solid var(--si-hair); border-radius: 999px; background: transparent; color: var(--si-ink-2); font: var(--si-font-label-sm); cursor: pointer; }
.sanctions.high { border-color: var(--si-danger); color: var(--si-danger-ink); }
.reasons { margin: 0 0 8px; padding-left: 18px; font: var(--si-font-meta); white-space: pre-wrap; overflow-wrap: anywhere; }
.delta-note { margin: 0; padding: 6px 10px; border-radius: 8px; background: var(--si-blue-tint); color: var(--si-blue-ink); font: var(--si-font-meta); overflow-wrap: anywhere; }
.coverage { margin: 0; font: var(--si-font-meta); color: var(--si-ink-2); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ai-notice { display: flex; align-items: flex-start; gap: 6px; padding: 0; border: 0; background: transparent; color: var(--si-warn-ink); font: var(--si-font-meta); text-align: left; cursor: pointer; }
.ai-notice svg { flex: none; margin-top: 3px; }
.details-toggle { align-self: flex-start; padding: 0; }
.details { font: var(--si-font-meta); color: var(--si-ink-2); }
.doc-summary { display: block; white-space: pre-wrap; overflow-wrap: anywhere; }
.details ul { margin: 6px 0 0; padding-left: 18px; white-space: pre-wrap; overflow-wrap: anywhere; }
</style>
