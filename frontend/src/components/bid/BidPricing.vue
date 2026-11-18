<script setup lang="ts">
import { computed, inject, ref } from 'vue'
import { ChevronRight } from '@lucide/vue'
import { bidApi, type Bidder, type BidPricing } from '@/api/bid'
import BidItemCard from './BidItemCard.vue'
import BidLede from './BidLede.vue'
import PriceStrip, { type PricePoint } from './charts/PriceStrip.vue'
import RatioCurve from './charts/RatioCurve.vue'
import { bidResultsKey, ceilingView, levelText, levelTone, markText, patternStep, pricingTypes, totalView, trimRatio, type LedePart } from './results'

// 报价分析（V34Q-25 第 8 条）：限价 → 结论 → 各家报价（计算与大小写就地展开）→ 总价规律 → 两两比值（只列有规律的对）→ 分项对齐表。
// 投标人按录入顺序，不提供按价格排序；总价旁标“最高”“最低”，比最低价高、偏离平均与异常低价由后端 Decimal 算好（V34Q-27）。
// 未填限价时不出现任何限价内容。
const props = defineProps<{ bidders: Bidder[]; target?: string }>()
const ctx = inject(bidResultsKey)!
const pricing = computed(() => ctx.data.value?.pricing)
const params = computed(() => ctx.data.value?.parameters)
const short = (id: string) => ctx.shorts.value[id] ?? '已移除'
const cols = computed(() => props.bidders.filter(b => pricing.value?.bidders[b.id]))
const issues = computed(() => (ctx.data.value?.items ?? []).filter(i => i.kind === 'issue' && pricingTypes.has(i.type) && i.review_status !== 'excluded'))
const issuesOf = (id: string) => issues.value.filter(i => i.bidder_ids.includes(id))
const expanded = ref<string | null>(null)
const unit = computed(() => (params.value?.currency ?? 'CNY') === 'CNY' ? '元' : params.value?.currency)
const ceiling = computed(() => {
  const v = params.value?.ceiling
  if (!v) return null
  const [int, dec] = v.split('.')
  return int!.replace(/\B(?=(\d{3})+(?!\d))/g, ',') + '.' + (dec ?? '').padEnd(2, '0').slice(0, 2)
})
const rows = computed(() => cols.value.map(b => {
  const p = pricing.value!.bidders[b.id]!
  return { bidder: b, p, total: totalView(p), ceiling: ceilingView(p), issues: issuesOf(b.id) }
}))
const cmp = computed(() => pricing.value?.compare ?? null)
const marked = (mark: 'highest' | 'lowest') => rows.value.filter(r => r.p.mark === mark).map(r => short(r.bidder.id)).join('、')
const patterns = computed(() => pricing.value?.total_patterns ?? [])
const editing = ref(''), amount = ref(''), error = ref(''), saving = ref(false)
const pending = computed(() => ctx.data.value?.conclusion.verify.filter(e => issues.value.some(i => i.id === e.item_id)) ?? [])
async function saveTotal(id: string) {
  if (saving.value) return
  saving.value = true; error.value = ''
  try {
    ctx.data.value = await bidApi.manualTotal(ctx.projectId, id, amount.value.trim() || null)
    editing.value = ''
  } catch { error.value = '请输入金额，例如 1234567.89' }
  finally { saving.value = false }
}
function editTotal(id: string) {
  editing.value = id; error.value = ''
  amount.value = pricing.value?.bidders[id]?.manual_total ? pricing.value.bidders[id]?.total ?? '' : ''
}
// 报价分布图：只画参加比较的各家（V34-B-06d）
const points = computed<PricePoint[]>(() => rows.value.filter(r => r.p.total && !r.p.not_compared)
  .map(r => ({ id: r.bidder.id, label: short(r.bidder.id), name: r.bidder.name, text: r.p.total!, mark: r.p.mark, low: !!r.p.low_price })))
const stripCaption = computed(() => {
  const c = cmp.value
  if (!c) return ''
  const parts = [`${c.n} 家总价在 ${c.lowest}～${c.highest} ${unit.value}之间` + (c.spread_pct ? `，最高比最低高 ${c.spread_pct}` : '')]
  const low = rows.value.filter(r => r.p.low_price).map(r => short(r.bidder.id))
  if (low.length) parts.push(`${low.join('、')}明显低于其余各家（警告色）`)
  if (ceiling.value) { const over = rows.value.filter(r => r.ceiling.tone === 'over').map(r => short(r.bidder.id)); parts.push(over.length ? `${over.join('、')}在限价线右侧` : '各家都在限价线以内') }
  const out = cols.value.filter(b => pricing.value!.bidders[b.id]!.not_compared).map(b => short(b.id))
  if (out.length) parts.push(`${out.join('、')}未参加比较`)
  return parts.join('；') + '。'
})

type Pair = BidPricing['pairs'][number]
const patterned = computed(() => (pricing.value?.pairs ?? []).filter(p => p.label))
const plain = computed(() => (pricing.value?.pairs ?? []).filter(p => !p.label))
const showPlain = ref(false)
function patternText(p: Pair) {
  return p.label?.startsWith('分项单价比值恒定') ? `${p.n} 项分项价格之比都约为 ${trimRatio(p.mean_ratio)}`
    : p.label?.startsWith('分项单价完全相同') ? `${p.n} 项分项价格完全相同` : p.label ?? ''
}
// 分项对齐表：构成规律的一对，在后一家的列头注明“与 X 比 ≈ k”
const colNote = computed(() => {
  const out: Record<string, string> = {}
  for (const p of patterned.value) {
    const note = p.label?.startsWith('分项单价比值恒定') ? `与${short(p.a)}比 ≈ ${trimRatio(p.mean_ratio)}` : `与${short(p.a)}有规律`
    out[p.b] = out[p.b] ? `${out[p.b]}；${note}` : note
  }
  return out
})
const lede = computed<LedePart[]>(() => {
  const parts: LedePart[] = []
  const missing = rows.value.filter(r => !r.p.total && !r.p.totals_found.length).length
  if (rows.value.length && missing === rows.value.length) return [[`${missing} 家未识别出投标总价，报价比较与规律检查未进行`]]
  if (missing) parts.push([`${missing} 家未识别出投标总价，未参加总价比较与规律检查`])
  if (ceiling.value) rows.value.filter(r => r.ceiling.tone === 'over').forEach(r => parts.push([`${short(r.bidder.id)}的报价`, [`超限价 ${r.ceiling.diff} ${unit.value}`, 'd']]))
  rows.value.filter(r => r.p.low_price).forEach(r => parts.push([`${short(r.bidder.id)}的报价比其余各家平均低 ${r.p.low_price!.below_pct}（`, ['异常低价', 'w'], '），请核实是否低于成本']))
  patterns.value.forEach(t => parts.push([`${t.ids.map(short).join('、')}的投标总价`, [patternStep(t), 'w'], '（中）']))
  patterned.value.forEach(p => parts.push([`${short(p.a)} · ${short(p.b)}：${patternText(p)}（`, [levelText[p.level!] ?? '', levelTone(p.level!) === 'strong' ? 'd' : 'w'], '）']))
  const calc = rows.value.filter(r => r.issues.some(i => i.type !== 'ceiling' && i.type !== 'low_price'))
  if (calc.length) parts.push([`${calc.map(r => short(r.bidder.id)).join('、')}的报价有计算或大小写问题`])
  if (!parts.length) parts.push([(ceiling.value ? '各家报价未超限价，' : '') + '计算与大小写未见问题，分项价格之间未见规律'])
  // 高低差放在超限价之后、其他问题之前
  const over = ceiling.value ? rows.value.filter(r => r.ceiling.tone === 'over').length : 0
  if (cmp.value?.spread_pct) parts.splice(over, 0, [`报价最高的${marked('highest')}比最低的${marked('lowest')}高 ${cmp.value.spread_pct}`])
  return parts
})
</script>

<template>
  <div class="bid-tab">
    <p v-if="!pricing" class="bid-empty">报价检查还没有结果。</p>
    <template v-else>
      <div class="top">
        <BidLede :parts="lede" />
        <p v-if="ceiling || cmp" class="bid-meta">
          <template v-if="ceiling">最高限价 <b>{{ ceiling }}</b> {{ unit }}</template><template v-if="ceiling && cmp"><i class="sep" aria-hidden="true">·</i></template>
          <template v-if="cmp">参加比较 {{ cmp.n }} 家：最高 <b>{{ cmp.highest }}</b><i class="sep" aria-hidden="true">·</i>最低 <b>{{ cmp.lowest }}</b><i class="sep" aria-hidden="true">·</i>平均 <b>{{ cmp.mean }}</b> {{ unit }}</template>
        </p>
      </div>
      <PriceStrip v-if="cmp && points.length >= 2" :points="points" :mean="cmp.mean" :ceiling="ceiling" :unit="unit ?? ''" :caption="stripCaption" />

      <section class="bid-block quote-section" :class="{ saving }">
        <h3 class="bid-title">各家报价</h3>
        <table class="bid-table">
          <colgroup><col><col style="width:196px"><col style="width:132px"><col style="width:84px"><col v-if="ceiling" style="width:190px"><col style="width:132px"><col style="width:104px"><col style="width:76px"></colgroup>
          <thead><tr><th>投标人（按录入顺序）</th><th class="r">投标总价</th><th class="r">比最低价高</th><th class="r" title="与参加比较各家平均总价之差">偏离平均</th><th v-if="ceiling">与限价</th><th class="r">报价表合计</th><th>计算与大小写</th><th class="r">报价表分项</th></tr></thead>
          <tbody>
            <template v-for="r in rows" :key="r.bidder.id">
              <tr :id="'bid-price-' + r.bidder.id" :class="{ 'bid-target': target === r.bidder.id }">
                <td class="name" :title="r.bidder.name">{{ r.bidder.name }}</td>
                <td class="r num"><template v-if="r.total.text">{{ r.total.text }}</template><span v-else class="bid-zero">未识别</span>
                  <span v-if="r.p.mark" class="bid-mark">{{ markText[r.p.mark] }}</span><span v-if="r.p.low_price" class="bid-mark w">异常低价</span>
                  <small v-if="r.total.multi.length" :title="'出现的总价：' + r.total.multi.join('、')">{{ r.total.multi.length }} 个总价</small>
                  <small v-if="r.p.manual_total">唱标价 · 人工录入</small>
                  <template v-if="editing === r.bidder.id">
                    <el-input v-model="amount" size="small" aria-label="唱标价" placeholder="小写金额" @keyup.enter="saveTotal(r.bidder.id)" @keyup.esc="editing = ''" />
                    <el-button size="small" text :disabled="saving" @click="saveTotal(r.bidder.id)">保存</el-button>
                    <el-button size="small" text :disabled="saving" @click="editing = ''">取消</el-button>
                    <small v-if="error" class="quote-error" role="alert">{{ error }}</small>
                  </template>
                  <el-button v-else size="small" text @click="editTotal(r.bidder.id)">{{ r.p.manual_total ? '修改' : '录入' }}</el-button>
                </td>
                <td class="r num"><template v-if="r.p.above_lowest">{{ r.p.above_lowest }}<small>{{ r.p.above_lowest_pct }}</small></template>
                  <span v-else-if="r.p.not_compared" class="bid-zero note" :title="r.p.not_compared">未参加比较</span><span v-else class="bid-zero">—</span></td>
                <td class="r num"><span v-if="r.p.vs_mean && !r.p.not_compared" :class="{ 'low': r.p.low_price }">{{ r.p.vs_mean }}</span><span v-else class="bid-zero">—</span></td>
                <td v-if="ceiling"><span :class="{ 'bid-over': r.ceiling.tone === 'over', 'bid-zero': r.ceiling.tone === 'none' }">{{ r.ceiling.text }}</span><span v-if="r.ceiling.diff" class="num bid-diff" :class="{ 'bid-over': r.ceiling.tone === 'over' }">{{ r.ceiling.diff }}</span></td>
                <td class="r num"><template v-if="r.p.table?.total">{{ r.p.table.total }}</template><span v-else class="bid-zero">—</span></td>
                <td>
                  <button v-if="r.issues.length" type="button" class="bid-link calc bad" :aria-expanded="expanded === r.bidder.id" @click="expanded = expanded === r.bidder.id ? null : r.bidder.id">
                    ✕ {{ r.issues.length }} 条<ChevronRight :size="13" :stroke-width="1.75" class="chev" :class="{ turned: expanded === r.bidder.id }" aria-hidden="true" /></button>
                  <span v-else-if="r.p.table || r.total.text" class="calc ok">✓</span><span v-else class="bid-zero">—</span>
                </td>
                <td class="r num"><template v-if="r.p.table">{{ r.p.table.rows }} 项</template><span v-else class="bid-zero">—</span></td>
              </tr>
              <tr v-if="expanded === r.bidder.id" class="expand"><td :colspan="ceiling ? 8 : 7"><BidItemCard v-for="item in r.issues" :key="item.id" :item="item" hide-who readonly /></td></tr>
            </template>
          </tbody>
        </table>
      </section>

      <section v-if="pending.length" class="bid-block">
        <h3 class="bid-title">报价方面需你核实<span class="n">{{ pending.length }}</span></h3>
        <ul class="bid-fine"><li v-for="e in pending" :key="e.id"><button class="bid-link bid-tier verify" type="button" @click="ctx.focus(e.id)">{{ e.title }}</button></li></ul>
      </section>
      <section v-if="patterns.length" class="bid-block">
        <h3 class="bid-title">总价规律<span class="n">{{ patterns.length }}</span></h3>
        <ul class="found">
          <li v-for="t in patterns" :key="t.ids.join()"><span class="who" :title="t.ids.map(i => ctx.names.value[i]).join(' · ')">{{ t.ids.map(short).join(' · ') }}</span>
            <span :class="'bid-' + levelTone(t.level)">投标总价{{ patternStep(t) }}，{{ levelText[t.level] }}</span>
            <span class="cv">第四十条（四），见串标线索</span></li>
        </ul>
      </section>

      <section v-if="pricing.pairs.length" class="bid-block">
        <h3 class="bid-title">两两分项比值<span class="n">{{ patterned.length }} / {{ pricing.pairs.length }}</span></h3>
        <ul v-if="patterned.length" class="found">
          <li v-for="p in patterned" :key="p.a + p.b"><span class="who" :title="`${ctx.names.value[p.a]} · ${ctx.names.value[p.b]}`">{{ short(p.a) }} · {{ short(p.b) }}</span>
            <span :class="'bid-' + levelTone(p.level!)">{{ patternText(p) }}，{{ levelText[p.level!] }}</span>
            <span class="cv num">变异系数 {{ p.cv }}</span></li>
        </ul>
        <div v-if="patterned.length" class="curves">
          <RatioCurve :items="pricing.items" :pairs="patterned" :labels="ctx.shorts.value" />
        </div>
        <p v-else class="bid-empty">各对之间未见分项价格规律。</p>
        <button v-if="plain.length" type="button" class="bid-fold" :aria-expanded="showPlain" @click="showPlain = !showPlain">
          <ChevronRight :size="14" :stroke-width="1.75" class="chev" :class="{ turned: showPlain }" aria-hidden="true" />{{ patterned.length ? `其余 ${plain.length} 对未见规律` : `查看 ${plain.length} 对的比值` }}
        </button>
        <table v-if="showPlain" class="bid-table plain">
          <thead><tr><th>两家</th><th class="r">对齐分项</th><th class="r">参与比较</th><th class="r">平均比值</th><th class="r">变异系数</th></tr></thead>
          <tbody>
            <tr v-for="p in plain" :key="p.a + p.b">
              <td :title="`${ctx.names.value[p.a]} · ${ctx.names.value[p.b]}`">{{ short(p.a) }} · {{ short(p.b) }}</td>
              <td class="r num">{{ p.aligned }}<small>{{ p.aligned_by === 'row' ? '按行序' : '按品名' }}</small></td>
              <td class="r num"><template v-if="p.n">{{ p.n }}</template><span v-else class="bid-zero">不足 5 项</span></td>
              <td class="r num"><template v-if="p.mean_ratio">{{ trimRatio(p.mean_ratio) }}</template><span v-else class="bid-zero">—</span></td>
              <td class="r num"><template v-if="p.cv">{{ p.cv }}</template><span v-else class="bid-zero">—</span></td>
            </tr>
          </tbody>
        </table>
      </section>

      <section v-if="pricing.items.length" class="bid-block">
        <h3 class="bid-title">分项对齐<span class="n">{{ pricing.items.length }}</span></h3>
        <div class="wide">
          <table class="bid-table items">
            <thead><tr><th>分项</th><th v-for="b in cols" :key="b.id" class="r" :title="b.name">{{ short(b.id) }}<small v-if="colNote[b.id]" class="note">{{ colNote[b.id] }}</small></th></tr></thead>
            <tbody>
              <tr v-for="(it, i) in pricing.items" :key="i">
                <td>{{ it.name || '（无品名）' }}<small v-if="it.fixed">给定</small></td>
                <td v-for="b in cols" :key="b.id" class="r num" :class="{ hl: colNote[b.id] && !it.fixed }"><template v-if="it.prices[b.id]">{{ it.prices[b.id] }}</template><span v-else class="bid-zero">—</span></td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
    </template>
  </div>
</template>

<style scoped>
.quote-section.saving { opacity: .65; }
.quote-section .quote-error { color: var(--si-danger-ink); }
.quote-section .el-button + .el-button { margin-inline-start: 0; }
@media (max-width: 1120px) {
  .quote-section .bid-table { table-layout: fixed; }
  .quote-section col { width: auto !important; }
  .quote-section th, .quote-section td { padding-inline-end: 6px; white-space: normal; overflow-wrap: anywhere; }
}
.top { display: flex; flex-direction: column; gap: 8px; }
.act { padding: 3px 10px; border: 1px solid var(--si-hair-strong); border-radius: 8px; background: transparent; font: var(--si-font-label-sm); color: var(--si-ink-2); cursor: pointer; }
.act:hover { color: var(--si-ink-1); background: var(--si-hover-bg); }
.calc { display: inline-flex; align-items: center; gap: 4px; font: var(--si-font-meta); }
.calc.bad { color: var(--si-danger-ink); }
.calc.ok { color: var(--si-ink-3); }
.calc .chev { transition: transform .15s; }
.calc .chev.turned { transform: rotate(90deg); }
tr.expand > td { padding: 0 0 8px 20px; background: transparent; }
.found { margin: 0; padding: 0; list-style: none; border-top: 1px solid var(--si-hair-strong); }
.found li { display: flex; align-items: baseline; gap: 24px; padding: 10px 0; border-bottom: 1px solid var(--si-hair-faint); font: var(--si-font-body); }
.found .who { min-width: 160px; color: var(--si-ink-1); font-weight: 500; }
.found .cv { margin-left: auto; font: var(--si-font-meta); color: var(--si-ink-4); }
.plain { margin-top: 8px; max-width: 760px; }
.curves { display: flex; flex-direction: column; gap: 20px; margin-top: 16px; max-width: 880px; }
.wide { overflow-x: auto; }
.items th .note { color: var(--si-warn-ink); }
.bid-table td .note { font: var(--si-font-caption); }
.bid-table td .low { color: var(--si-warn-ink); }
.sep { margin: 0 8px; font-style: normal; color: var(--si-ink-5); }
.items td.hl { color: var(--si-ink-1); }
</style>
