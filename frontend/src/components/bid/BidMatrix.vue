<script setup lang="ts">
import { computed, inject, ref } from 'vue'
import { bidMethodLabel, type Bidder, type BidCell, type BidCheck } from '@/api/bid'
import BidCellDrawer from './BidCellDrawer.vue'
import { bidResultsKey } from './results'

// 要求响应矩阵：严格按 bid-frontend-spec.md 三之二显示，序号沿用要求清单。
const props = defineProps<{ bidders: Bidder[]; modelState: string | null }>()
const ctx = inject(bidResultsKey)!
const onlyProblems = ref(true)
const groupLabels = ['否决条款', '★ / 实质性条款', '资格条件', '一般要求', '人工核对']
type Requirement = BidCheck['requirements'][number]
const groupOf = (r: Requirement) => r.method === 'manual' ? 4 : ({ veto: 0, substantive: 1, qualification: 2 } as Record<string, number>)[r.group] ?? 3
// 只缓存位置；取格子时经响应式数组读取，核对状态改动即时反映
const cellIndex = computed(() => new Map((ctx.data.value?.cells ?? []).map((c, i) => [`${c.requirement_id}:${c.bidder_id}`, i])))
const cellOf = (rid: string, bid: string) => { const i = cellIndex.value.get(`${rid}:${bid}`); return i === undefined ? undefined : ctx.data.value?.cells[i] }
function cellRank(method: string, c?: BidCell): number {
  if (!c) return 2
  if (c.review_status === 'excluded') return 3
  if (method === 'manual' || c.verdict === 'manual')
    return method === 'manual' && c.verdict === 'manual' && c.review_status === 'verified' ? 3 : 2
  if (c.cite === 'unverified') return 2
  return ({ no: 0, missing: 1, unclear: 2, manual: 2, yes: 3 })[c.verdict] ?? 2
}
const problem = (method: string, c?: BidCell) => cellRank(method, c) < 3
function rowRank(r: Requirement) {
  return Math.min(...props.bidders.map(b => cellRank(r.method, cellOf(r.id, b.id))), 3)
}
const all = computed(() => [...(ctx.data.value?.requirements ?? [])].filter(r => r.group !== 'format')
  .sort((a, b) => groupOf(a) - groupOf(b) || rowRank(a) - rowRank(b) || a.seq - b.seq))
const rows = computed(() => all.value.filter(r => !onlyProblems.value || props.bidders.some(b => problem(r.method, cellOf(r.id, b.id)))))
const hasAnomaly = (r: Requirement, verdict: 'no' | 'missing') =>
  r.method !== 'manual' && props.bidders.some(b => {
    const c = cellOf(r.id, b.id)
    return !!c && c.review_status !== 'excluded' && c.cite !== 'unverified' && c.verdict === verdict
  })
const countRows = (list: Requirement[], verdict: 'no' | 'missing') => list.filter(r => hasAnomaly(r, verdict)).length
const groups = computed(() => groupLabels.map((label, index) => {
  const groupRows = all.value.filter(r => groupOf(r) === index)
  const items = rows.value.filter(r => groupOf(r) === index)
  return { label, total: groupRows.length, items, no: countRows(groupRows, 'no'), missing: countRows(groupRows, 'missing') }
}).filter(g => g.items.length))
const hidden = computed(() => all.value.length - rows.value.length)
const noRows = computed(() => countRows(all.value, 'no'))
const missingRows = computed(() => countRows(all.value, 'missing'))

const symbols: Record<string, { s: string; label: string; tone: string }> = {
  yes: { s: '✓', label: '响应', tone: 'plain' }, no: { s: '不响应', label: '疑似不响应', tone: 'verify' },
  missing: { s: '未找到', label: '未找到', tone: 'verify' }, unclear: { s: '？', label: '无法判断', tone: 'plain' },
}
function view(method: string, c?: BidCell) {
  if (method === 'manual' || c?.verdict === 'manual') {
    if (c?.review_status === 'excluded') return { s: '—', label: '不适用', tone: 'plain' }
    if (method === 'manual' && c?.verdict === 'manual' && c.review_status === 'verified') return { s: '✓', label: '已核实符合', tone: 'plain' }
    return { s: '？', label: '人工核对', tone: 'plain' }
  }
  if (!c) return { s: '？', label: method === 'model' ? (props.modelState === 'running' ? '模型核对中' : '未核对') : '本地规则未能判断', tone: 'plain' }
  if (c.cite === 'unverified') return { s: '？', label: '引文待核实', tone: 'plain' }
  if (c.verdict === 'no' && c.review_status === 'verified') return { s: '不响应', label: '已核实不响应', tone: 'confirmed' }
  return symbols[c.verdict] ?? symbols.unclear!
}
function title(method: string, c?: BidCell) {
  const v = view(method, c)
  const parts = [v.label, c ? (c.verdict === 'manual' || method === 'manual' ? '人工核对' : c.source === 'model' ? '模型判断' : '本地规则') : bidMethodLabel[method as 'local']]
  if (c && c.review_status !== 'pending') parts.push(c.review_status === 'verified' ? '已核实' : (method === 'manual' || c.verdict === 'manual' ? '本条不适用' : '已排除'))
  return parts.join(' · ')
}
const drawer = ref<{ rid: string; bid: string } | null>(null)
</script>

<template>
  <div class="bid-tab">
    <p class="bid-lede num">{{ all.length }} 条要求中，{{ noRows }} 条有家疑似不响应，{{ missingRows }} 条有家未找到响应内容。</p>
    <section class="bid-block">
      <div class="tools">
        <label class="toggle"><el-switch v-model="onlyProblems" size="small" />只看有问题的行</label>
        <span v-if="onlyProblems && hidden" class="hint num">已隐藏 {{ hidden }} 行</span>
      </div>
      <div v-if="rows.length" class="scroll">
        <table>
          <thead><tr><th class="req">要求</th><th v-for="b in bidders" :key="b.id" class="col" :title="b.name">{{ ctx.shorts.value[b.id] }}</th></tr></thead>
          <tbody v-for="g in groups" :key="g.label">
            <tr class="group-row"><th :colspan="bidders.length + 1" scope="rowgroup"><div class="num">{{ g.label }} · {{ g.total }}<span v-if="g.no || g.missing" class="end"><template v-if="g.no">疑似不响应 {{ g.no }}</template><template v-if="g.no && g.missing"> · </template><template v-if="g.missing">未找到 {{ g.missing }}</template></span></div></th></tr>
            <tr v-for="r in g.items" :key="r.id">
              <th class="req" scope="row"><span class="number num">{{ r.seq }}.</span><span class="text" :class="{ potential: ['veto', 'substantive', 'qualification'].includes(r.group) }">{{ r.text }}</span></th>
              <td v-for="b in bidders" :key="b.id">
                <button type="button" class="cell" :class="{ excluded: cellOf(r.id, b.id)?.review_status === 'excluded' && r.method !== 'manual' && cellOf(r.id, b.id)?.verdict !== 'manual' }" :title="title(r.method, cellOf(r.id, b.id))" :aria-label="`${b.name}：${title(r.method, cellOf(r.id, b.id))}`" @click="drawer = { rid: r.id, bid: b.id }">
                  <span class="symbol" :class="view(r.method, cellOf(r.id, b.id)).tone">{{ view(r.method, cellOf(r.id, b.id)).s }}</span>
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-else class="bid-empty">{{ all.length ? '关闭“只看有问题的行”可查看全部要求。' : '要求清单中没有可逐家判断的要求。' }}</p>
      <p v-if="rows.length" class="legend">✓ 响应　？ 无法判断或引文待核实　— 不适用</p>
    </section>
    <BidCellDrawer v-if="drawer" :requirement-id="drawer.rid" :bidder-id="drawer.bid" @close="drawer = null" />
  </div>
</template>

<style scoped>
.tools { display: flex; align-items: center; gap: 16px; margin-bottom: 12px; }
.toggle { display: inline-flex; align-items: center; gap: 8px; font: var(--si-font-label); color: var(--si-ink-2); cursor: pointer; }
.hint { margin: 0; font: var(--si-font-meta); color: var(--si-ink-4); }
.legend { margin: 12px 0 0; font: var(--si-font-caption); color: var(--si-ink-3); }
.scroll { border-top: 1px solid var(--si-hair-strong); }
table { border-collapse: collapse; width: 100%; table-layout: fixed; font: var(--si-font-body); }
th, td { border-bottom: 1px solid var(--si-hair-faint); }
thead th { padding: 10px 8px; font: var(--si-font-label-sm); color: var(--si-ink-3); text-align: center; background: var(--si-bg-canvas); }
th.req { width: 42%; padding: 10px 16px 10px 0; text-align: left; font-weight: normal; background: var(--si-bg-canvas); overflow-wrap: anywhere; }
.col { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.group-row th { padding: 16px 8px 8px 0; text-align: left; font: var(--si-font-label); color: var(--si-ink-1); border-bottom: 1px solid var(--si-hair-strong); }
.group-row div { display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap; }
.group-row .end { margin-left: auto; font: var(--si-font-meta); color: var(--si-ink-3); }
.number { margin-right: 8px; font: var(--si-font-code-sm); color: var(--si-ink-3); }
.text { display: block; color: var(--si-ink-1); }
.text.potential { font-weight: 600; }
td { padding: 0; text-align: center; }
.cell { position: relative; width: 100%; min-height: 52px; border: 0; background: transparent; cursor: pointer; }
.cell:hover { background: var(--si-hover-bg); }
.cell.excluded { opacity: .4; }
.symbol { display: inline-flex; align-items: center; gap: 6px; font: var(--si-font-body); }
.symbol.plain { color: var(--si-ink-3); }
.symbol.verify { color: var(--si-warn-ink); font-weight: 600; }
.symbol.verify::before { content: ''; width: 6px; height: 6px; border-radius: 50%; background: var(--si-warn); flex: none; }
.symbol.confirmed { color: var(--si-danger-ink); font-weight: 600; }
.symbol.confirmed::before { content: ''; width: 6px; height: 6px; border-radius: 50%; background: var(--si-danger); flex: none; }
</style>
