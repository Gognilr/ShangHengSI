<script setup lang="ts">
import { computed, inject } from 'vue'
import type { Bidder, BidItem, BidLevel } from '@/api/bid'
import { bidResultsKey } from './results'

// 总览（bid.md 2.4 第 1 项、visual.md 7.5）：数字条 → 投标人表（按录入顺序）→ 两两关系 + 覆盖说明。每个数字可点，跳到对应页签并筛选好。
const props = defineProps<{ bidders: Bidder[]; files: number; pages: number }>()
const emit = defineEmits<{ go: [tab: 'bidders' | 'clues' | 'pricing' | 'matrix', filter?: { bidder?: string; pair?: string[] }] }>()
const ctx = inject(bidResultsKey)!
const items = computed(() => (ctx.data.value?.items ?? []).filter(i => i.review_status !== 'excluded'))
const issues = computed(() => items.value.filter(i => i.kind === 'issue'))
const clues = computed(() => items.value.filter(i => i.kind === 'clue'))
const levels: BidLevel[] = ['strong', 'medium', 'weak']
const count = (list: BidItem[], level: BidLevel) => list.filter(i => i.level === level).length

// 待人工核对：未核对的问题与线索、无法判断或引文待核实的格、人工核对行、未标注的签章页
function pendingFor(bidder: string) {
  const d = ctx.data.value
  if (!d) return 0
  const manual = d.requirements.filter(r => r.method === 'manual')
  const cells = d.cells.filter(c => c.bidder_id === bidder)
  const reviewed = (rid: string) => cells.some(c => c.requirement_id === rid && c.review_status !== 'pending')
  return items.value.filter(i => i.kind !== 'normal_dup' && i.review_status === 'pending' && i.bidder_ids.includes(bidder)).length
    + cells.filter(c => c.review_status === 'pending' && c.verdict !== 'manual' && (c.verdict === 'unclear' || c.cite === 'unverified')).length
    + manual.filter(r => !reviewed(r.id)).length
    + d.signing.filter(s => s.bidder_id === bidder && !s.mark).length
}
const rows = computed(() => props.bidders.map(b => {
  const own = issues.value.filter(i => i.bidder_ids.includes(b.id))
  const price = ctx.data.value?.pricing?.bidders[b.id]
  const over = price?.over_ceiling
  return {
    bidder: b, veto: count(own, 'veto'), other: own.length - count(own, 'veto'), pending: pendingFor(b.id),
    total: price?.total ?? (price?.totals_found.length ? `${price.totals_found.length} 个不同总价` : '未识别'),
    ceiling: over == null ? null : over.startsWith('-') ? '低于限价' : over === '0.00' ? '等于限价' : `▲ 超限价 ${over}`,
    clues: levels.map(level => ({ level, n: count(clues.value.filter(i => i.bidder_ids.includes(b.id)), level) })).filter(x => x.n),
  }
}))
const figures = computed(() => {
  const all = ctx.data.value?.items.filter(i => i.kind !== 'normal_dup') ?? []
  return {
    veto: count(issues.value, 'veto'), vetoBidders: new Set(issues.value.filter(i => i.level === 'veto').flatMap(i => i.bidder_ids)).size,
    clues: levels.map(level => ({ level, n: count(clues.value, level) })), clueTotal: clues.value.length,
    pending: props.bidders.reduce((sum, b) => sum + pendingFor(b.id), 0)
      - items.value.filter(i => i.kind !== 'normal_dup' && i.review_status === 'pending' && i.bidder_ids.length > 1).reduce((s, i) => s + i.bidder_ids.length - 1, 0),
    verified: all.filter(i => i.review_status === 'verified').length, excluded: all.filter(i => i.review_status === 'excluded').length,
  }
})
// 两两关系：上三角，单元格为两家共同涉及的线索按强 / 中 / 弱计数
const pairs = computed(() => props.bidders.slice(0, -1).map((a, i) => ({
  a, cells: props.bidders.slice(1).map((b, j) => j < i ? null : {
    b, counts: levels.map(level => ({ level, n: clues.value.filter(c => c.level === level && c.bidder_ids.includes(a.id) && c.bidder_ids.includes(b.id)).length })).filter(x => x.n),
  }),
})))
const short = (name: string) => name.replace(/(股份)?有限(责任)?公司$/, '').slice(-6)
const levelText: Record<string, string> = { strong: '强', medium: '中', weak: '弱' }
</script>

<template>
  <div class="overview">
    <div class="figs">
      <div class="fig"><p class="si-overline">投标人</p><p class="v num">{{ bidders.length }}<small>家 · {{ files }} 个文件<template v-if="pages"> · {{ pages }} 页</template></small></p></div>
      <button type="button" class="fig" @click="emit('go', 'bidders')"><p class="si-overline">疑似否决项</p><p class="v num" :class="{ d: figures.veto }">{{ figures.veto }}<small>{{ figures.veto ? `涉及 ${figures.vetoBidders} 家` : '未发现' }}</small></p></button>
      <button type="button" class="fig" @click="emit('go', 'clues')"><p class="si-overline">串标线索</p><p class="v num">{{ figures.clueTotal }}<small class="lv"><span v-for="c in figures.clues" :key="c.level" :class="c.level">{{ levelText[c.level] }} <b>{{ c.n }}</b></span></small></p></button>
      <div class="fig"><p class="si-overline">待人工核对</p><p class="v num">{{ figures.pending }}<small>已核实 {{ figures.verified }} · 已排除 {{ figures.excluded }}</small></p></div>
    </div>

    <table class="bidders">
      <colgroup><col><col style="width:104px"><col style="width:92px"><col style="width:104px"><col style="width:168px"><col style="width:176px"><col style="width:150px"></colgroup>
      <thead><tr><th>投标人（按录入顺序）</th><th class="r">疑似否决项</th><th class="r">其他问题</th><th class="r">待人工核对</th><th class="r">投标总价</th><th>与限价</th><th>涉及串标线索</th></tr></thead>
      <tbody>
        <tr v-for="r in rows" :key="r.bidder.id">
          <td class="name">{{ r.bidder.name }}</td>
          <td class="r num"><button type="button" :class="{ hi: r.veto }" @click="emit('go', 'bidders', { bidder: r.bidder.id })">{{ r.veto }}</button></td>
          <td class="r num"><button type="button" @click="emit('go', 'bidders', { bidder: r.bidder.id })">{{ r.other }}</button></td>
          <td class="r num"><button type="button" @click="emit('go', 'bidders', { bidder: r.bidder.id })">{{ r.pending }}</button></td>
          <td class="r num"><button type="button" @click="emit('go', 'pricing')">{{ r.total }}</button></td>
          <td><span v-if="r.ceiling" :class="{ hi: r.ceiling.startsWith('▲') }">{{ r.ceiling }}</span><span v-else class="muted">—</span></td>
          <td><button v-if="r.clues.length" type="button" class="lv" @click="emit('go', 'clues', { pair: [r.bidder.id] })"><span v-for="c in r.clues" :key="c.level" :class="c.level">{{ levelText[c.level] }} <b>{{ c.n }}</b></span></button><span v-else class="muted">—</span></td>
        </tr>
      </tbody>
    </table>

    <div class="two">
      <section v-if="bidders.length > 1">
        <h3 class="sub">两两关系<span>点击单元格查看对照</span></h3>
        <div class="tri" :style="{ gridTemplateColumns: `72px repeat(${bidders.length - 1}, minmax(0, 1fr))` }">
          <span />
          <span v-for="b in bidders.slice(1)" :key="b.id" class="hd" :title="b.name">{{ short(b.name) }}</span>
          <template v-for="row in pairs" :key="row.a.id">
            <span class="rh" :title="row.a.name">{{ short(row.a.name) }}</span>
            <template v-for="(cell, j) in row.cells" :key="j">
              <span v-if="!cell" class="na" />
              <button v-else-if="cell.counts.length" type="button" class="lv" @click="emit('go', 'clues', { pair: [row.a.id, cell.b.id] })"><span v-for="c in cell.counts" :key="c.level" :class="c.level">{{ levelText[c.level] }} <b>{{ c.n }}</b></span></button>
              <span v-else class="muted">—</span>
            </template>
          </template>
        </div>
      </section>
      <section v-else><h3 class="sub">两两关系</h3><p class="muted">只有 1 家，不比对串标线索。</p></section>
      <section>
        <h3 class="sub">覆盖说明<span>没有列出，不等于没有问题</span></h3>
        <ul class="cov"><li v-for="note in ctx.data.value?.coverage ?? []" :key="note">{{ note }}</li></ul>
      </section>
    </div>
  </div>
</template>

<style scoped>
.overview { display: flex; flex-direction: column; gap: 32px; }
.figs { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); border-bottom: 1px solid var(--si-hair); }
.fig { display: block; margin: 0; padding: 4px 24px 18px 0; border: 0; background: transparent; text-align: left; font: inherit; color: inherit; }
button.fig { cursor: pointer; }
button.fig:hover .v { text-decoration: underline; text-decoration-color: var(--si-hair-strong); text-underline-offset: 6px; }
.fig + .fig { padding-left: 24px; border-left: 1px solid var(--si-hair-faint); }
.fig .si-overline { margin-bottom: 6px; }
.v { margin: 0; font: var(--si-font-stat); color: var(--si-ink-1); display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; }
.v.d { color: var(--si-danger-ink); }
.v small { font: var(--si-font-meta); color: var(--si-ink-3); }
.lv { display: inline-flex; gap: 10px; padding: 0; border: 0; background: transparent; font: var(--si-font-meta); color: var(--si-ink-3); cursor: pointer; }
.lv b { font-family: var(--si-font-mono); font-weight: 600; }
.lv .strong { color: var(--si-danger-ink); }
.lv .medium { color: var(--si-warn-ink); }
.lv .weak { color: var(--si-ink-3); }
table.bidders { width: 100%; border-collapse: collapse; table-layout: fixed; font: var(--si-font-body); }
th { padding: 8px 12px 8px 0; border-bottom: 1px solid var(--si-hair-strong); font: var(--si-font-label-sm); color: var(--si-ink-3); text-align: left; }
td { padding: 11px 12px 11px 0; border-bottom: 1px solid var(--si-hair-faint); color: var(--si-ink-2); }
.r { text-align: right; }
td.name { color: var(--si-ink-1); font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
td button:not(.lv) { padding: 0; border: 0; background: transparent; font: inherit; color: inherit; cursor: pointer; }
td button:hover { text-decoration: underline; text-underline-offset: 3px; }
.hi { color: var(--si-danger-ink) !important; font-weight: 600; }
.muted { color: var(--si-ink-4); font: var(--si-font-meta); }
.two { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 48px; }
.sub { display: flex; align-items: baseline; gap: 10px; margin: 0 0 10px; font: var(--si-font-h4); color: var(--si-ink-1); }
.sub span { font: var(--si-font-meta); color: var(--si-ink-4); }
.tri { display: grid; border-top: 1px solid var(--si-hair); }
.tri > * { display: flex; align-items: center; min-height: 38px; padding: 0 8px; border-bottom: 1px solid var(--si-hair-faint); font: var(--si-font-meta); }
.tri .hd, .tri .rh { color: var(--si-ink-3); overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
.tri .hd { justify-content: flex-start; }
.tri button.lv:hover { background: var(--si-hover-bg); }
.cov { margin: 0; padding: 0; list-style: none; border-top: 1px solid var(--si-hair); }
.cov li { padding: 9px 0; border-bottom: 1px solid var(--si-hair-faint); font: var(--si-font-body); color: var(--si-ink-2); }
</style>
