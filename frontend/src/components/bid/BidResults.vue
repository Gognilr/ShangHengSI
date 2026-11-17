<script setup lang="ts">
import { computed, onBeforeUnmount, provide, ref, watch } from 'vue'
import { ChevronLeft, ChevronRight } from '@lucide/vue'
import { ApiError } from '@/api/http'
import { bidApi, type BidCheck, type BidProject, type BidReview, type BidReviewStatus, type BidSigning, type BidSignMark } from '@/api/bid'
import BidOverview from './BidOverview.vue'
import BidMatrix from './BidMatrix.vue'
import BidBidderIssues from './BidBidderIssues.vue'
import BidClues from './BidClues.vue'
import BidPricing from './BidPricing.vue'
import BidFocus from './BidFocus.vue'
import { bidResultsKey, pricingTypes, reviewTodos, shortNames, todoSteps } from './results'
import './results.css'

// 初审结果五个页签（bid.md 2.4、visual.md 7.5）。live 为真时（初审中）每 2 秒刷新，已完成的步骤先显示。
const props = defineProps<{ project: BidProject; stateKey: string; live?: boolean }>()
const data = ref<BidCheck>(), error = ref(''), notice = ref('')
type Tab = 'overview' | 'matrix' | 'bidders' | 'clues' | 'pricing'
const tab = ref<Tab>('overview'), bidder = ref(''), pair = ref<string[] | null>(null)
const names = computed(() => Object.fromEntries(props.project.bidders.map(b => [b.id, b.name])))
const shorts = computed(() => shortNames(props.project.bidders))
const todos = computed(() => reviewTodos(data.value, props.project.bidders))
const steps = computed(() => todoSteps(data.value, props.project.bidders))
const fileNames = computed(() => Object.fromEntries(props.project.files.map(f => [f.id, f.name])))
const renderable = computed(() => Object.fromEntries(props.project.files.map(f => [f.id, !f.original_deleted && ['pdf', 'jpg', 'jpeg', 'png'].includes(f.ext)])))

let timer: ReturnType<typeof setTimeout> | undefined, saving = 0
async function load() {
  if (timer) clearTimeout(timer)
  try {
    const next = await bidApi.check(props.project.id)
    if (!saving) { data.value = next; error.value = '' }  // 核对状态保存中时不让轮询结果覆盖本地修改
  } catch (e) { error.value = e instanceof ApiError ? e.message : '初审结果读取失败' }
  if (props.live) timer = setTimeout(() => void load(), 2000)
}
watch(() => [props.stateKey, props.live], () => void load(), { immediate: true })
onBeforeUnmount(() => { if (timer) clearTimeout(timer) })

// 乐观更新：先改本地，失败回滚并提示
async function optimistic(target: BidReview, patch: Partial<BidReview>, send: () => Promise<BidReview>) {
  const before = { review_status: target.review_status, note: target.note }
  Object.assign(target, patch); notice.value = ''; saving++
  try { Object.assign(target, await send()) }
  catch (e) { Object.assign(target, before); notice.value = e instanceof ApiError ? e.message : '核对状态没有保存，已恢复，请重试' }
  finally { saving-- }
}
async function reviewItem(id: string, patch: Partial<BidReview>) {
  const item = data.value?.items.find(i => i.id === id)
  if (item) await optimistic(item, patch, () => bidApi.reviewItem(props.project.id, id, patch))
}
async function reviewCell(rid: string, bid: string, patch: Partial<BidReview>) {
  if (!data.value) return
  let cell = data.value.cells.find(c => c.requirement_id === rid && c.bidder_id === bid)
  const created = !cell
  if (!cell) {  // 人工核对行首次标注：本地先建一格
    cell = { requirement_id: rid, bidder_id: bid, verdict: 'manual', locs: [], reason: null, source: 'local', cite: null, review_status: 'pending', note: null }
    data.value.cells.push(cell)
    cell = data.value.cells[data.value.cells.length - 1]!
  }
  const target = cell
  await optimistic(target, patch, () => bidApi.reviewCell(props.project.id, { requirement_id: rid, bidder_id: bid, ...patch }))
  if (created && notice.value && data.value) data.value.cells = data.value.cells.filter(c => c !== target)
}
// 批量核对：先改本地（人工核对行没有格时先建），一次请求，失败整体回滚
async function reviewBatch(itemIds: string[], refs: { requirement_id: string; bidder_id: string }[], status: BidReviewStatus) {
  const d = data.value
  if (!d || (!itemIds.length && !refs.length)) return true
  const ids = new Set(itemIds)
  const items = d.items.filter(i => ids.has(i.id))
  const created: typeof d.cells = []
  const cells = refs.map(r => {
    const found = d.cells.find(c => c.requirement_id === r.requirement_id && c.bidder_id === r.bidder_id)
    if (found) return found
    d.cells.push({ ...r, verdict: 'manual', locs: [], reason: null, source: 'local', cite: null, review_status: 'pending', note: null })
    const cell = d.cells[d.cells.length - 1]!
    created.push(cell)
    return cell
  })
  const before = [...items, ...cells].map(t => [t, t.review_status] as const)
  before.forEach(([t]) => { t.review_status = status })
  notice.value = ''; saving++
  try { await bidApi.reviewBatch(props.project.id, { items: items.map(i => i.id), cells: refs, review_status: status }); return true }
  catch (e) {
    before.forEach(([t, s]) => { t.review_status = s })
    if (created.length) d.cells = d.cells.filter(c => !created.includes(c))
    notice.value = e instanceof ApiError ? e.message : '核对状态没有保存，已恢复，请重试'
    return false
  } finally { saving-- }
}
async function markSigning(pages: BidSigning[], mark: BidSignMark | null) {
  if (!pages.length) return
  const before = pages.map(s => [s, s.mark] as const)
  pages.forEach(s => { s.mark = mark }); notice.value = ''; saving++
  try {
    if (pages.length === 1) await bidApi.sign(props.project.id, pages[0]!, mark)
    else await bidApi.reviewBatch(props.project.id, { signing: pages.map(s => ({ file_id: s.file_id, page: s.page, loc: s.loc })), mark })
  } catch (e) {
    before.forEach(([s, m]) => { s.mark = m })
    notice.value = e instanceof ApiError ? e.message : '标注没有保存，已恢复，请重试'
  } finally { saving-- }
}
// 逐条核对（V34Q-27 第二部分第 3 条）：进入时记下待核各步，核完自动跳下一条
const focusAt = ref<string | null>(null)
function focus(key?: string) { focusAt.value = key ?? steps.value.find(s => !s.done)?.key ?? steps.value[0]?.key ?? null }

// 查看原件页：大图对话框，可翻页
const page = ref<{ fileId: string; page: number } | null>(null)
const pageCount = computed(() => props.project.files.find(f => f.id === page.value?.fileId)?.page_count ?? 1)
function openPage(fileId: string, n: number) { page.value = { fileId, page: n } }
provide(bidResultsKey, { projectId: props.project.id, data, names, shorts, fileNames, renderable, todos, steps, reviewItem, reviewCell, reviewBatch, markSigning, focus, openPage })

// 各投标人默认选疑似否决项最多（其次问题最多）的一家（V34Q-25 第 6 条）
watch(data, d => {
  if (bidder.value || !d) return
  const score = (id: string) => { const own = d.items.filter(i => i.kind === 'issue' && i.review_status !== 'excluded' && i.bidder_ids.includes(id)); return own.filter(i => i.level === 'veto').length * 1000 + own.length }
  bidder.value = [...props.project.bidders].sort((a, b) => score(b.id) - score(a.id))[0]?.id ?? ''
})

// 页签计数统一为“待核”数（V34Q-27 第二部分第 7 条）：各投标人页是按家看的同一批事项，不另计数
const counts = computed(() => {
  const open = todos.value.filter(t => !t.done)
  const items = new Map((data.value?.items ?? []).map(i => [i.id, i]))
  const itemOf = (key: string) => items.get(key.slice(1))
  return {
    all: open.length, matrix: open.filter(t => t.kind === 'cell' || t.kind === 'manual').length,
    clues: open.filter(t => t.kind === 'item' && itemOf(t.key)?.kind === 'clue').length,
    pricing: open.filter(t => t.kind === 'item' && pricingTypes.has(itemOf(t.key)?.type ?? '')).length,
  }
})
const tabs = computed(() => [
  { key: 'overview' as Tab, label: '总览', n: counts.value.all }, { key: 'matrix' as Tab, label: '要求响应矩阵', n: counts.value.matrix },
  { key: 'bidders' as Tab, label: '各投标人' }, { key: 'clues' as Tab, label: '串标线索', n: counts.value.clues },
  { key: 'pricing' as Tab, label: '报价分析', n: counts.value.pricing },
])
function go(next: Tab, filter?: { bidder?: string; pair?: string[] }) {
  if (filter?.bidder) bidder.value = filter.bidder
  pair.value = next === 'clues' ? filter?.pair ?? null : pair.value
  tab.value = next
}
// 页脚说明：各页签的口径与限制写在这里，标题旁不再跟说明句（V34Q-25 第 1 条）
const declaration = '本系统提供初审线索，不构成对串通投标或否决投标的认定，由评标委员会核实后决定。'
const notes = computed(() => {
  const d = data.value
  const base = d?.pricing?.base ? names.value[d.pricing.base] ?? '' : ''
  switch (tab.value) {
    case 'overview': return [...(d?.coverage ?? []), '没有列出的事项，不等于投标文件没有问题。']
    case 'matrix': return ['实心符号为本地规则得出，空心符号为模型判断；“模型判断”行在模型核对完成前显示虚线点“未核对”。', '“人工核对”行需要对照原件，可在各投标人页逐家标注。']
    case 'bidders': return ['签章页只看图核对，不识别印章内容；Word 文件没有页面图，请到原件核对。', '否决性结论须标为“已核实”，报告中才以肯定语气写出。']
    case 'clues': return ['“正常重复”为招标范本、厂家资料等共有内容，一般不是线索。', '线索按《招标投标法实施条例》第四十条的情形列出，仅供评标委员会核实。']
    default: return ['金额由本机计算，四舍五入到分；投标人按录入顺序排列，不按价格排序。',
      '只有唯一总价的投标人参加最高、最低与平均比较；比其余各家平均低 20% 以上提示“异常低价”，由评标委员会要求说明是否低于成本。',
      ...(base ? [`分项对齐以${base}的报价表为基准，最多 300 项；“给定”为各家同价的分项，不参与比值。`] : []),
      '两两比值取高的一家比低的一家；各项比值越接近（变异系数越小），越像按同一比例算出。']
  }
})
</script>

<template>
  <div class="results">
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <el-alert v-if="notice" class="notice" :title="notice" type="warning" show-icon @close="notice = ''" />
    <BidFocus v-if="data && focusAt" v-model="focusAt" :bidders="project.bidders" />
    <nav v-show="!focusAt" class="tabs" role="tablist" aria-label="初审结果">
      <button v-for="t in tabs" :key="t.key" type="button" role="tab" :aria-selected="tab === t.key" :class="{ on: tab === t.key }" @click="go(t.key)">{{ t.label }}<i v-if="t.n" class="num" :title="`待核 ${t.n}`">{{ t.n }}</i></button>
    </nav>
    <template v-if="data && !focusAt">
      <BidOverview v-if="tab === 'overview'" :bidders="project.bidders" @go="go" />
      <BidMatrix v-else-if="tab === 'matrix'" :bidders="project.bidders" :model-state="project.model.state" />
      <BidBidderIssues v-else-if="tab === 'bidders'" v-model="bidder" :bidders="project.bidders" />
      <BidClues v-else-if="tab === 'clues'" :pair="pair" @clear="pair = null" />
      <BidPricing v-else :bidders="project.bidders" />
    </template>
    <p v-else-if="!error && !data" class="hint" role="status">正在读取初审结果…</p>
    <ul v-show="!focusAt" class="bid-foot foot"><li v-for="n in notes" :key="n">{{ n }}</li><li>{{ declaration }}</li></ul>

    <el-dialog :model-value="!!page" width="min(920px, 92vw)" append-to-body :title="page ? `${fileNames[page.fileId] ?? '原件'} · 第 ${page.page} 页` : ''" @update:model-value="(v: boolean) => { if (!v) page = null }">
      <div v-if="page" class="page-view">
        <img :src="bidApi.pageUrl(project.id, page.fileId, page.page, 1100)" :alt="`第 ${page.page} 页`">
      </div>
      <template #footer>
        <div v-if="page" class="pager">
          <el-button :disabled="page.page <= 1" @click="page.page--"><ChevronLeft :size="16" :stroke-width="1.75" />上一页</el-button>
          <span class="num">{{ page.page }} / {{ pageCount }}</span>
          <el-button :disabled="page.page >= pageCount" @click="page.page++">下一页<ChevronRight :size="16" :stroke-width="1.75" /></el-button>
        </div>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.results { flex: 1; display: flex; flex-direction: column; gap: 28px; }
.foot { margin-top: auto; padding-top: 14px; }
.tabs { display: flex; gap: 26px; border-bottom: 1px solid var(--si-hair); }
.tabs button { position: relative; height: 40px; padding: 0; border: 0; background: transparent; font: var(--si-font-label); color: var(--si-ink-3); cursor: pointer; }
.tabs button:hover { color: var(--si-ink-1); }
.tabs button.on { color: var(--si-ink-1); font-weight: 600; }
.tabs button.on::after { content: ""; position: absolute; left: 0; right: 0; bottom: -1px; height: 2px; background: var(--si-ink-1); }
.tabs i { margin-left: 5px; font: var(--si-font-code-sm); font-style: normal; color: var(--si-ink-4); }
.hint { margin: 0; font: var(--si-font-meta); color: var(--si-ink-3); }
.error { margin: 0; color: var(--si-danger-ink); font: var(--si-font-meta); }
.page-view { max-height: 70vh; overflow: auto; background: var(--si-stage); border-radius: var(--si-radius-card); }
.page-view img { display: block; width: 100%; }
.pager { display: flex; align-items: center; justify-content: center; gap: 16px; }
.pager .el-button svg { margin: 0 4px; }
</style>
