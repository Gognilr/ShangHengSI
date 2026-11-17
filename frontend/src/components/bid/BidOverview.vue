<script setup lang="ts">
import { computed, inject } from 'vue'
import type { Bidder, BidItem, BidLevel } from '@/api/bid'
import BidLede from './BidLede.vue'
import { bidResultsKey, ceilingView, levelText, progress, totalView, type LedePart } from './results'

// 总览（V34Q-25 第 5 条）：一句结论 + 核对进度 → 投标人表（按录入顺序）→ 有线索的两两关系。覆盖说明在页脚。
const props = defineProps<{ bidders: Bidder[] }>()
const emit = defineEmits<{ go: [tab: 'bidders' | 'clues' | 'pricing' | 'matrix', filter?: { bidder?: string; pair?: string[] }] }>()
const ctx = inject(bidResultsKey)!
const short = (id: string) => ctx.shorts.value[id] ?? ''
const items = computed(() => (ctx.data.value?.items ?? []).filter(i => i.review_status !== 'excluded'))
const issues = computed(() => items.value.filter(i => i.kind === 'issue'))
const clues = computed(() => items.value.filter(i => i.kind === 'clue'))
const excluded = computed(() => (ctx.data.value?.items ?? []).filter(i => i.kind !== 'normal_dup' && i.review_status === 'excluded').length)
const levels: BidLevel[] = ['strong', 'medium', 'weak']
const byLevel = (list: BidItem[]) => levels.map(level => ({ level, n: list.filter(i => i.level === level).length })).filter(x => x.n)
const pricing = computed(() => ctx.data.value?.pricing)
const hasCeiling = computed(() => !!ctx.data.value?.parameters?.ceiling)

const rows = computed(() => props.bidders.map(b => {
  const own = issues.value.filter(i => i.bidder_ids.includes(b.id))
  const veto = own.filter(i => i.level === 'veto').length
  const price = pricing.value?.bidders[b.id]
  return {
    bidder: b, veto, other: own.length - veto, total: totalView(price), ceiling: ceilingView(price, hasCeiling.value),
    clues: byLevel(clues.value.filter(i => i.bidder_ids.includes(b.id))), progress: progress(ctx.todos.value, b.id),
  }
}))
// 两两关系：只列有线索的一对；家数 ≥ 5 时用三角矩阵
const pairs = computed(() => props.bidders.flatMap((a, i) => props.bidders.slice(i + 1).map(b => ({
  a, b, counts: byLevel(clues.value.filter(c => c.bidder_ids.includes(a.id) && c.bidder_ids.includes(b.id))),
}))))
const countsOf = (a: string, b: string) => pairs.value.find(x => x.a.id === a && x.b.id === b)?.counts ?? []
const linked = computed(() => pairs.value.filter(p => p.counts.length))
const triangle = computed(() => props.bidders.length >= 5)
const all = computed(() => progress(ctx.todos.value))

// 结论句：本地拼出（例：“3 家中 2 家有疑似否决项（4 条）；松青 · 鳞木有 1 条强串标线索；赭石报价超限价。”）
const lede = computed<LedePart[]>(() => {
  const parts: LedePart[] = []
  const vetoRows = rows.value.filter(r => r.veto)
  const vetoTotal = issues.value.filter(i => i.level === 'veto').length
  parts.push(vetoRows.length ? [`${props.bidders.length} 家中 ${vetoRows.length} 家有`, [`疑似否决项（${vetoTotal} 条）`, 'd']] : [`${props.bidders.length} 家均未发现疑似否决项`])
  if (props.bidders.length > 1) {
    if (!linked.value.length) parts.push(['各家之间未见串标线索'])
    else {
      const part: LedePart = [`${linked.value.length} 对投标人之间有串标线索：`]
      linked.value.slice(0, 3).forEach((p, i) => {
        part.push(`${i ? '、' : ''}${short(p.a.id)} · ${short(p.b.id)}（`)
        p.counts.forEach((c, k) => part.push(k ? '、' : '', [`${levelText[c.level]} ${c.n}`, c.level === 'strong' ? 'd' : c.level === 'medium' ? 'w' : 'b']))
        part.push('）')
      })
      if (linked.value.length > 3) part.push(' 等')
      parts.push(part)
    }
  }
  const over = rows.value.filter(r => r.ceiling.tone === 'over')
  if (over.length) parts.push([`${over.map(r => short(r.bidder.id)).join('、')}的报价`, ['超最高限价', 'd']])
  return parts
})
const sub = computed(() => `核对进度 已核 ${all.value.done} / 共 ${all.value.total}` + (excluded.value ? ` · 另有 ${excluded.value} 条已排除，不计入` : ''))
</script>

<template>
  <div class="bid-tab">
    <BidLede :parts="lede" :sub="sub" />

    <section class="bid-block">
      <h3 class="bid-title">投标人<span class="n">{{ bidders.length }}</span></h3>
      <table class="bid-table">
        <colgroup><col><col style="width:96px"><col style="width:96px"><col style="width:190px"><col style="width:190px"><col style="width:120px"><col style="width:112px"></colgroup>
        <thead><tr><th>投标人（按录入顺序）</th><th class="r">疑似否决</th><th class="r">其他问题</th><th class="r">投标总价</th><th>与限价</th><th>串标线索</th><th class="r" title="这一家要核的事项（已核 / 共）；涉及两家的串标线索在两家各计一次">核对进度</th></tr></thead>
        <tbody>
          <tr v-for="r in rows" :key="r.bidder.id">
            <td class="name" :title="r.bidder.name">{{ r.bidder.name }}</td>
            <td class="r num"><button v-if="r.veto" type="button" class="bid-link bid-over" @click="emit('go', 'bidders', { bidder: r.bidder.id })">{{ r.veto }}</button><span v-else class="bid-zero">—</span></td>
            <td class="r num"><button v-if="r.other" type="button" class="bid-link" @click="emit('go', 'bidders', { bidder: r.bidder.id })">{{ r.other }}</button><span v-else class="bid-zero">—</span></td>
            <td class="r num">
              <button v-if="r.total.text" type="button" class="bid-link" @click="emit('go', 'pricing')">{{ r.total.text }}</button><span v-else class="bid-zero">未识别</span>
              <small v-if="r.total.multi.length" :title="'出现的总价：' + r.total.multi.join('、')">{{ r.total.multi.length }} 个总价</small>
            </td>
            <td><span :class="{ 'bid-over': r.ceiling.tone === 'over', 'bid-zero': r.ceiling.tone === 'none' }">{{ r.ceiling.text }}</span><span v-if="r.ceiling.tone === 'over'" class="num bid-over bid-diff">{{ r.ceiling.diff }}</span></td>
            <td><button v-if="r.clues.length" type="button" class="bid-link bid-lv" @click="emit('go', 'clues', { pair: [r.bidder.id] })"><span v-for="c in r.clues" :key="c.level" :class="c.level">{{ levelText[c.level] }} <b>{{ c.n }}</b></span></button><span v-else class="bid-zero">—</span></td>
            <td class="r num"><button v-if="r.progress.total" type="button" class="bid-link" @click="emit('go', 'bidders', { bidder: r.bidder.id })">{{ r.progress.done }} / {{ r.progress.total }}</button><span v-else class="bid-zero">—</span></td>
          </tr>
        </tbody>
      </table>
    </section>

    <section v-if="bidders.length > 1" class="bid-block">
      <h3 class="bid-title">两两关系<span class="n">{{ linked.length }} / {{ pairs.length }}</span></h3>
      <div v-if="triangle" class="tri" :style="{ gridTemplateColumns: `96px repeat(${bidders.length - 1}, minmax(0, 1fr))` }">
        <span />
        <span v-for="b in bidders.slice(1)" :key="b.id" class="hd" :title="b.name">{{ short(b.id) }}</span>
        <template v-for="(a, i) in bidders.slice(0, -1)" :key="a.id">
          <span class="hd" :title="a.name">{{ short(a.id) }}</span>
          <template v-for="(b, j) in bidders.slice(1)" :key="b.id">
            <span v-if="j < i" />
            <button v-else-if="countsOf(a.id, b.id).length" type="button" class="bid-link bid-lv" @click="emit('go', 'clues', { pair: [a.id, b.id] })"><span v-for="c in countsOf(a.id, b.id)" :key="c.level" :class="c.level">{{ levelText[c.level] }} <b>{{ c.n }}</b></span></button>
            <span v-else class="bid-zero">—</span>
          </template>
        </template>
      </div>
      <ul v-else class="pairs">
        <li v-for="p in linked" :key="p.a.id + p.b.id">
          <button type="button" class="bid-link who" :title="`${p.a.name} · ${p.b.name}`" @click="emit('go', 'clues', { pair: [p.a.id, p.b.id] })">{{ short(p.a.id) }} · {{ short(p.b.id) }}</button>
          <span class="bid-lv"><span v-for="c in p.counts" :key="c.level" :class="c.level">{{ levelText[c.level] }} <b>{{ c.n }}</b></span></span>
        </li>
        <li v-if="pairs.length > linked.length" class="rest">{{ linked.length ? '其余' : '全部' }} {{ pairs.length - linked.length }} 对未见线索</li>
      </ul>
    </section>
  </div>
</template>

<style scoped>
.bid-table td small { margin-top: 2px; }
.pairs { margin: 0; padding: 0; list-style: none; max-width: 560px; border-top: 1px solid var(--si-hair-strong); }
.pairs li { display: flex; align-items: center; gap: 24px; padding: 10px 0; border-bottom: 1px solid var(--si-hair-faint); font: var(--si-font-body); }
.pairs .who { min-width: 160px; color: var(--si-ink-1); }
.pairs .bid-lv { font: var(--si-font-meta); }
.pairs .rest { font: var(--si-font-meta); color: var(--si-ink-4); }
.tri { display: grid; border-top: 1px solid var(--si-hair-strong); }
.tri > * { display: flex; align-items: center; min-height: 38px; padding: 0 8px; border-bottom: 1px solid var(--si-hair-faint); font: var(--si-font-meta); }
.tri .hd { color: var(--si-ink-3); overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
</style>
