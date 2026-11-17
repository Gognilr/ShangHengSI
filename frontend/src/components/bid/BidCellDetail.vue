<script setup lang="ts">
import { computed, inject, ref, watch } from 'vue'
import { ApiError } from '@/api/http'
import { bidApi, bidGroupLabel, bidMethodLabel, type BidCellDetail, type BidReview } from '@/api/bid'
import BidReviewBar from './BidReviewBar.vue'
import { bidResultsKey } from './results'

// 单元格详情：要求原文与出处、该家对应片段（可看原件页）、判断理由、核对状态与备注。单元格抽屉与逐条核对共用。
const props = defineProps<{ requirementId: string; bidderId: string; noBar?: boolean }>()
const emit = defineEmits<{ loaded: [detail: BidCellDetail] }>()
const ctx = inject(bidResultsKey)!
const detail = ref<BidCellDetail>(), error = ref('')
const verdictLabel: Record<string, string> = { yes: '响应', no: '疑似不响应', missing: '未找到', unclear: '无法判断', manual: '人工核对' }
// 核对状态以页面数据为准（乐观更新后即时反映）
const live = computed<BidReview>(() => {
  const c = ctx.data.value?.cells.find(x => x.requirement_id === props.requirementId && x.bidder_id === props.bidderId)
  return c ?? { review_status: 'pending', note: null }
})
const canReview = computed(() => !!detail.value && (detail.value.cell !== null || detail.value.requirement.method === 'manual'))
async function load() {
  error.value = ''; detail.value = undefined
  try { detail.value = await bidApi.cell(ctx.projectId, props.requirementId, props.bidderId); emit('loaded', detail.value) }
  catch (e) { error.value = e instanceof ApiError ? e.message : '读取失败' }
}
watch(() => [props.requirementId, props.bidderId], load, { immediate: true })
</script>

<template>
  <p v-if="error" class="error" role="alert">{{ error }}</p>
  <div v-if="detail" class="body">
    <section>
      <h3 class="si-overline">要求 {{ detail.requirement.seq }} · {{ bidGroupLabel[detail.requirement.group] }} · {{ bidMethodLabel[detail.requirement.method] }}</h3>
      <p class="req">{{ detail.requirement.text }}</p>
      <p v-for="(s, i) in detail.requirement.sources" :key="i" class="source"><span class="hint">出处：{{ s.file }}</span>{{ s.text }}</p>
    </section>
    <section>
      <h3 class="si-overline">判断</h3>
      <template v-if="detail.cell">
        <p class="verdict"><b>{{ verdictLabel[detail.cell.verdict] ?? detail.cell.verdict }}</b><span class="hint">{{ detail.cell.verdict === 'manual' ? '人工标注' : detail.cell.source === 'model' ? '模型判断（模型只看到脱敏片段与投标人代号）' : '本地规则' }}</span>
          <span v-if="detail.cell.cite === 'unverified'" class="warn">引文未能在片段中核实，请对照原件</span></p>
        <p v-if="detail.cell.reason" class="reason">{{ detail.cell.reason }}</p>
      </template>
      <p v-else class="hint">{{ detail.requirement.method === 'manual' ? '这一行需要人工对照原件核对，核对后在下方标注。' : '还没有判断结果（模型核对未完成或本地规则未能判断）。' }}</p>
    </section>
    <section v-if="detail.places.length">
      <h3 class="si-overline">{{ detail.bidder.name }}的对应片段</h3>
      <article v-for="(p, i) in detail.places" :key="i" class="place">
        <p class="hint">{{ p.file }}<template v-if="p.page"> 第 {{ p.page }} 页</template>
          <button v-if="p.page && p.renderable" type="button" class="link" @click="ctx.openPage(p.file_id, p.page)">查看原件页</button></p>
        <p class="ctx"><span v-if="p.before" class="dim">{{ p.before }}<br></span><mark>{{ p.text }}</mark><span v-if="p.after" class="dim"><br>{{ p.after }}</span></p>
      </article>
    </section>
    <section v-if="canReview && !noBar">
      <h3 class="si-overline">核对</h3>
      <BidReviewBar :review="live" :veto="detail.cell?.verdict === 'no' && detail.requirement.group === 'veto'" @change="patch => ctx.reviewCell(requirementId, bidderId, patch)" />
    </section>
  </div>
  <p v-else-if="!error" class="hint" role="status">正在读取…</p>
</template>

<style scoped>
.body { display: flex; flex-direction: column; gap: 24px; }
section .si-overline { padding-bottom: 6px; border-bottom: 1px solid var(--si-hair); }
.req { margin: 0; font: var(--si-font-body); color: var(--si-ink-1); white-space: pre-wrap; }
.source { display: flex; flex-direction: column; gap: 2px; margin: 8px 0 0; font: var(--si-font-meta); color: var(--si-ink-2); }
.verdict { display: flex; flex-wrap: wrap; align-items: baseline; gap: 10px; margin: 0; font: var(--si-font-body); color: var(--si-ink-1); }
.warn { font: var(--si-font-meta); color: var(--si-warn-ink); }
.reason { margin: 8px 0 0; font: var(--si-font-body); color: var(--si-ink-2); white-space: pre-wrap; }
.place + .place { margin-top: 14px; }
.place .hint { margin: 0 0 4px; }
.ctx { margin: 0; padding: 10px 12px; border: 1px solid var(--si-hair); border-radius: var(--si-radius-card); background: var(--si-paper); font: var(--si-font-body); color: var(--si-paper-ink); white-space: pre-wrap; overflow-wrap: anywhere; }
.dim { color: var(--si-paper-ink-2); }
mark { background: var(--si-hl-medium); color: inherit; }
.link { margin-left: 8px; padding: 0; border: 0; background: transparent; font: var(--si-font-meta); color: var(--si-ink-2); text-decoration: underline; text-underline-offset: 3px; cursor: pointer; }
.hint { font: var(--si-font-meta); color: var(--si-ink-3); }
.error { color: var(--si-danger-ink); font: var(--si-font-meta); }
</style>
