<script setup lang="ts">
import { ref, watch } from 'vue'
import { bidReviewLabel, type BidReview, type BidReviewStatus } from '@/api/bid'

// 核对状态“待核对 / 已核实 / 已排除”与备注（bid.md 2.4）；父组件乐观更新，失败回滚
const props = defineProps<{ review: BidReview; veto?: boolean }>()
const emit = defineEmits<{ change: [patch: Partial<BidReview>] }>()
const note = ref(props.review.note ?? ''), editing = ref(false)
watch(() => props.review.note, v => { if (!editing.value) note.value = v ?? '' })
const order: BidReviewStatus[] = ['pending', 'verified', 'excluded']
function set(status: BidReviewStatus) { if (status !== props.review.review_status) emit('change', { review_status: status }) }
function save() {
  editing.value = false
  const text = note.value.trim()
  if (text !== (props.review.note ?? '')) emit('change', { note: text })
}
</script>

<template>
  <div class="review">
    <div class="states" role="radiogroup" aria-label="核对状态">
      <button v-for="s in order" :key="s" type="button" role="radio" :aria-checked="review.review_status === s" :class="{ on: review.review_status === s }" @click="set(s)">{{ bidReviewLabel[s] }}</button>
    </div>
    <input v-model="note" class="note" maxlength="500" placeholder="备注（可选，随报告导出）" aria-label="备注" @focus="editing = true" @blur="save" @keyup.enter="($event.target as HTMLInputElement).blur()">
    <span v-if="veto && review.review_status !== 'verified'" class="hint">否决性结论须点“已核实”，报告中才以肯定语气写出</span>
  </div>
</template>

<style scoped>
.review { display: flex; align-items: center; flex-wrap: wrap; gap: 12px; }
.states { display: inline-flex; border: 1px solid var(--si-hair-strong); border-radius: 8px; overflow: hidden; }
.states button { padding: 4px 12px; border: 0; background: transparent; font: var(--si-font-label-sm); color: var(--si-ink-3); cursor: pointer; }
.states button + button { border-left: 1px solid var(--si-hair); }
.states button:hover { color: var(--si-ink-1); background: var(--si-hover-bg); }
.states button.on { color: var(--si-ink-1); font-weight: 600; background: var(--si-surface-2); }
.note { flex: 1; min-width: 200px; max-width: 420px; padding: 4px 0; border: 0; border-bottom: 1px solid var(--si-hair); background: transparent; font: var(--si-font-meta); color: var(--si-ink-1); outline: none; }
.note:focus { border-bottom-color: var(--si-ink-3); }
.note::placeholder { color: var(--si-ink-4); }
.hint { font: var(--si-font-meta); color: var(--si-ink-4); }
</style>
