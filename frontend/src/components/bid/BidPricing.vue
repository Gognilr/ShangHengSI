<script setup lang="ts">
import { computed, inject, ref } from 'vue'
import { bidApi, type Bidder, type BidConclusionEntry, type BidConclusionReview, type BidOpeningPriceInput, type BidPricing } from '@/api/bid'
import BidItemCard from './BidItemCard.vue'
import BidLede from './BidLede.vue'
import BidOpeningInput from './BidOpeningInput.vue'
import BidReviewReason from './BidReviewReason.vue'
import PriceStrip, { type PricePoint } from './charts/PriceStrip.vue'
import RatioCurve from './charts/RatioCurve.vue'
import { bidResultsKey, openingError, totalView, trimRatio } from './results'

// 报价分析（V34-B-06e，design/bid-pricing.md 与 bid-frontend-spec 第三节）：先结论、只摆异常、证据一点即开、明细默认收起。
// 顺序固定：结论句 → 报价位置条 → 各家报价（三列）→ 报价方面需你核实 → 修正后报价 → 明细（供复核）。
// 投标人按录入顺序，不排序；金额与结论文字都由后端给出（Decimal），这里不做计算。
const props = defineProps<{ bidders: Bidder[]; target?: string }>()
const ctx = inject(bidResultsKey)!
const pricing = computed(() => ctx.data.value?.pricing)
const summary = computed(() => ctx.data.value?.conclusion.pricing)
const params = computed(() => ctx.data.value?.parameters)
const short = (id: string) => ctx.shorts.value[id] ?? '已移除'
const unit = computed(() => (params.value?.currency ?? 'CNY') === 'CNY' ? '元' : params.value?.currency ?? '')
const ceiling = computed(() => {
  const v = params.value?.ceiling
  if (!v) return null
  const [int, dec] = v.split('.')
  return int!.replace(/\B(?=(\d{3})+(?!\d))/g, ',') + '.' + (dec ?? '').padEnd(2, '0').slice(0, 2)
})
const rows = computed(() => props.bidders.filter(b => pricing.value?.bidders[b.id]).map(b => {
  const p = pricing.value!.bidders[b.id]!
  const tags = summary.value?.tags[b.id] ?? []
  return { bidder: b, p, total: totalView(p), tags: tags.slice(0, 3), more: Math.max(0, tags.length - 3) }
}))
const cmp = computed(() => pricing.value?.compare ?? null)
// 悬停总价时补充显示（不藏关键信息：结论已在标签里）
function hover(p: BidPricing['bidders'][string]) {
  const parts = []
  if (p.above_lowest) parts.push(`比最低价高 ${p.above_lowest}（${p.above_lowest_pct}）`)
  if (p.vs_mean && !p.not_compared) parts.push(`偏离平均 ${p.vs_mean}`)
  if (p.not_compared) parts.push(p.not_compared)
  return parts.join('；')
}

// 唱标价：格内录入，保存后整页重算，期间只把本页变淡
const editing = ref(''), saving = ref(false), failed = ref('')
async function saveOpening(id: string, body: BidOpeningPriceInput) {
  if (saving.value) return
  saving.value = true; failed.value = ''
  try {
    ctx.data.value = await bidApi.openingPrice(ctx.projectId, id, body)
    editing.value = ''
  } catch (e) { failed.value = openingError(e, body) }
  finally { saving.value = false }
}
function edit(id: string) { editing.value = id; failed.value = ''; finding.value = '' }
// 找报价页：本地检查时记下的候选页
const finding = ref('')
const pages = (p: BidPricing['bidders'][string]) => (p.quote_pages ?? []).filter(q => ctx.renderable.value[q.file_id])
function openSource(p: BidPricing['bidders'][string]) {
  if (p.source?.file_id && p.source.page && ctx.renderable.value[p.source.file_id]) ctx.openPage(p.source.file_id, p.source.page)
}
const sourceText = (p: BidPricing['bidders'][string]) => p.source ? p.source.label + (p.source.page ? ` · 第 ${p.source.page} 页` : '') : ''

// 报价位置条：只画参加比较的各家
const points = computed<PricePoint[]>(() => rows.value.filter(r => r.p.total && !r.p.not_compared)
  .map(r => ({ id: r.bidder.id, label: short(r.bidder.id), name: r.bidder.name, text: r.p.total!, mark: r.p.mark, low: !!r.p.low_price })))
const stripCaption = computed(() => {
  const c = cmp.value
  if (!c) return ''
  const parts = [`${c.n} 家总价在 ${c.lowest}～${c.highest} ${unit.value}之间，平均 ${c.mean}`]
  if (ceiling.value) parts.push(`限价 ${ceiling.value}`)
  const low = rows.value.filter(r => r.p.low_price).map(r => short(r.bidder.id))
  if (low.length) parts.push(`${low.join('、')}在异常低价区间`)
  return parts.join('；') + '。'
})

// 报价方面需你核实：与初审结论页同一数据，判定在任一处做两处同步
const entries = computed(() => new Map((ctx.data.value?.conclusion.verify ?? []).map(e => [e.id, e])))
const checks = computed(() => (summary.value?.verify ?? []).map(k => entries.value.get(k)).filter((e): e is BidConclusionEntry => !!e))
const items = computed(() => new Map((ctx.data.value?.items ?? []).map(i => [i.id, i])))
const open = ref<string | null>(null), busy = ref(false), reviewFailed = ref('')
const fact = (e: BidConclusionEntry) => e.case_id ? e.title : `${e.bidder_ids.map(short).join('、')}：${e.title}`
async function decide(patch: BidConclusionReview) {
  if (busy.value) return
  busy.value = true; reviewFailed.value = ''
  try { ctx.data.value = await bidApi.conclusionReview(ctx.projectId, patch) }
  catch { reviewFailed.value = patch.key }
  finally { busy.value = false }
}

const corrected = computed(() => rows.value.filter(r => r.p.corrected))

// 明细：两两比值只列构成规律的一对；分项对齐表只给异常格加警告墨色
type Pair = BidPricing['pairs'][number]
const patterned = computed(() => (pricing.value?.pairs ?? []).filter(p => p.label))
const cols = computed(() => rows.value.map(r => r.bidder))
const marked = computed(() => new Set(patterned.value.map(p => p.b)))
const pairText = (p: Pair) => p.label?.startsWith('分项单价比值恒定') ? `${p.n} 项比值都约为 ${trimRatio(p.mean_ratio)}` : p.label ?? '—'
</script>

<template>
  <div class="bid-tab" :class="{ saving }">
    <p v-if="!pricing || !summary" class="bid-empty">报价检查还没有结果。</p>
    <template v-else>
      <BidLede :text="summary.lede" :sub="ceiling ? `最高限价 ${ceiling} ${unit}` : undefined" />
      <PriceStrip v-if="cmp && points.length >= 2" :points="points" :mean="cmp.mean" :ceiling="ceiling" :unit="unit" :caption="stripCaption" />

      <table class="bid-table quotes">
        <colgroup><col class="name-col"><col class="price-col"><col></colgroup>
        <thead><tr><th>投标人</th><th class="r">投标总价</th><th>结论</th></tr></thead>
        <tbody>
          <template v-for="r in rows" :key="r.bidder.id">
            <tr :id="'bid-price-' + r.bidder.id" :class="{ 'bid-target': target === r.bidder.id }">
              <td class="name" :title="r.bidder.name">{{ r.bidder.name }}</td>
              <td class="r num total">
                <BidOpeningInput v-if="editing === r.bidder.id" :initial="r.p.opening" :busy="saving" :failed="failed" @save="body => saveOpening(r.bidder.id, body)" @cancel="editing = ''" />
                <template v-else>
                  <span class="line">
                    <span v-if="r.total.text" :title="hover(r.p) || undefined">{{ r.total.text }}</span><span v-else class="bid-empty">未识别</span>
                    <el-button size="small" text @click="edit(r.bidder.id)">{{ r.p.manual_total ? '修改' : r.total.text ? '录入' : '填写唱标价' }}</el-button>
                    <el-button v-if="!r.p.total && pages(r.p).length" size="small" text :aria-expanded="finding === r.bidder.id" @click="finding = finding === r.bidder.id ? '' : r.bidder.id">找报价页</el-button>
                  </span>
                  <small v-if="r.p.manual_total">唱标价 · 人工录入<template v-if="r.p.file_total"> · 文件总价 {{ r.p.file_total }}</template></small>
                  <small v-else-if="r.total.multi.length">出现 {{ r.total.multi.length }} 个总价：{{ r.total.multi.join('、') }}</small>
                  <small v-else-if="r.p.source && r.total.text"><button v-if="r.p.source.file_id && r.p.source.page && ctx.renderable.value[r.p.source.file_id]" type="button" class="bid-link" @click="openSource(r.p)">{{ sourceText(r.p) }}</button><template v-else>{{ sourceText(r.p) }}</template></small>
                </template>
              </td>
              <td><div class="tags">
                <template v-if="r.tags.length"><span v-for="t in r.tags" :key="t.key + t.text" class="bid-tier" :class="t.tier">{{ t.text }}</span><span v-if="r.more" class="bid-meta">+{{ r.more }}</span></template>
                <span v-else-if="r.total.text" class="pass">✓</span><span v-else class="bid-zero">—</span>
              </div></td>
            </tr>
            <tr v-if="finding === r.bidder.id" class="expand">
              <td colspan="3">
                <p class="bid-meta">含“开标一览表 / 投标函 / 报价 / 大写”字样的页，按命中多少排序：</p>
                <ul class="pages"><li v-for="q in pages(r.p)" :key="q.file_id + q.page"><button type="button" class="bid-link" @click="ctx.openPage(q.file_id, q.page)">{{ ctx.fileNames.value[q.file_id] ?? '文件' }} · 第 {{ q.page }} 页</button></li></ul>
              </td>
            </tr>
          </template>
        </tbody>
      </table>

      <section v-if="checks.length" aria-labelledby="bid-price-verify">
        <h3 id="bid-price-verify" class="bid-title">报价方面需你核实<span class="n">{{ checks.length }}</span></h3>
        <ol class="bid-fine">
          <li v-for="(e, i) in checks" :key="e.id">
            <div class="check-line">
              <span class="num">{{ i + 1 }}</span>
              <span class="bid-tier verify">{{ fact(e) }}</span>
              <span class="bid-meta">{{ e.article && e.case_id ? '第四十条' + e.article : e.article }}</span>
              <div class="row-actions">
                <button v-if="e.case_id" type="button" class="bid-link" @click="ctx.focus(e.id)">查看 ›</button>
                <button v-else type="button" class="bid-link" :aria-expanded="open === e.id" @click="open = open === e.id ? null : e.id">查看 ›</button>
                <el-button size="small" :disabled="busy" @click="decide({ key: e.id, status: 'verified' })">Y</el-button>
                <BidReviewReason label="N" :disabled="busy" @decide="(reason, detail, remember) => decide({ key: e.id, status: 'excluded', reason, detail, remember })" />
              </div>
            </div>
            <p v-if="reviewFailed === e.id" class="save-error" role="alert">保存失败，请重试</p>
            <BidItemCard v-if="open === e.id && e.item_id && items.get(e.item_id)" :item="items.get(e.item_id)!" bare />
          </li>
        </ol>
      </section>

      <section v-if="corrected.length" aria-labelledby="bid-price-fixed">
        <h3 id="bid-price-fixed" class="bid-title">修正后报价</h3>
        <ul class="bid-fine">
          <li v-for="r in corrected" :key="r.bidder.id">
            {{ short(r.bidder.id) }}：修正后总价 <span class="num">{{ r.p.corrected!.total }}</span>（{{ r.p.corrected!.basis }}，原为 <span class="num">{{ r.p.corrected!.from }}</span>）
            <span class="bid-meta">供评标委员会参考，须投标人确认；修正原则以招标文件为准。</span>
            <p v-if="r.p.corrected!.over_ceiling" class="bid-tier verify">修正后{{ r.p.corrected!.over_ceiling }}</p>
          </li>
        </ul>
      </section>

      <details class="detail">
        <summary class="bid-fold">明细（供复核）</summary>
        <h4>总价比较</h4>
        <table class="bid-table compact">
          <thead><tr><th>投标人</th><th class="r">比最低价高</th><th class="r">偏离平均</th><th class="r">报价表合计</th><th class="r">报价表分项</th></tr></thead>
          <tbody>
            <tr v-for="r in rows" :key="r.bidder.id">
              <td class="name" :title="r.bidder.name">{{ short(r.bidder.id) }}</td>
              <td class="r num"><template v-if="r.p.above_lowest">{{ r.p.above_lowest }}<small>{{ r.p.above_lowest_pct }}</small></template><span v-else-if="r.p.not_compared" class="bid-empty">{{ r.p.not_compared }}</span><span v-else class="bid-zero">—</span></td>
              <td class="r num"><template v-if="r.p.vs_mean && !r.p.not_compared">{{ r.p.vs_mean }}</template><span v-else class="bid-zero">—</span></td>
              <td class="r num"><template v-if="r.p.table?.total">{{ r.p.table.total }}</template><span v-else class="bid-zero">—</span></td>
              <td class="r num"><template v-if="r.p.table">{{ r.p.table.rows }} 项</template><span v-else class="bid-empty">未识别</span></td>
            </tr>
          </tbody>
        </table>
        <template v-if="pricing.items.length">
          <h4>分项对齐 · {{ pricing.items.length }}</h4>
          <div class="wide">
            <table class="bid-table compact">
              <thead><tr><th>分项</th><th v-for="b in cols" :key="b.id" class="r" :title="b.name">{{ short(b.id) }}</th></tr></thead>
              <tbody>
                <tr v-for="(it, i) in pricing.items" :key="i">
                  <td>{{ it.name || '（无品名）' }}<small v-if="it.fixed">给定</small></td>
                  <td v-for="b in cols" :key="b.id" class="r num" :class="{ odd: marked.has(b.id) && !it.fixed }"><template v-if="it.prices[b.id]">{{ it.prices[b.id] }}</template><span v-else class="bid-zero">—</span></td>
                </tr>
              </tbody>
            </table>
          </div>
        </template>
        <template v-if="pricing.pairs.length">
          <h4>两两分项比值 · {{ pricing.pairs.length }}</h4>
          <table class="bid-table compact pairs">
            <thead><tr><th>两家</th><th class="r">对齐分项</th><th class="r">平均比值</th><th class="r">变异系数</th><th>说明</th></tr></thead>
            <tbody>
              <tr v-for="p in pricing.pairs" :key="p.a + p.b">
                <td :title="`${ctx.names.value[p.a]} · ${ctx.names.value[p.b]}`">{{ short(p.a) }} · {{ short(p.b) }}</td>
                <td class="r num">{{ p.aligned }}<small>{{ p.aligned_by === 'row' ? '按行序' : '按品名' }}</small></td>
                <td class="r num"><template v-if="p.mean_ratio">{{ trimRatio(p.mean_ratio) }}</template><span v-else class="bid-zero">—</span></td>
                <td class="r num"><template v-if="p.cv">{{ p.cv }}</template><span v-else class="bid-zero">—</span></td>
                <td :class="{ odd: p.label }">{{ p.label ? pairText(p) : (p.n ? '' : '不足 5 项') }}<span v-if="!p.label && p.n" class="pass">✓</span></td>
              </tr>
            </tbody>
          </table>
          <div v-if="patterned.length" class="curves"><RatioCurve :items="pricing.items" :pairs="patterned" :labels="ctx.shorts.value" /></div>
        </template>
      </details>
    </template>
  </div>
</template>

<style scoped>
.bid-tab.saving { opacity: .55; transition: opacity .15s; }
.quotes { table-layout: fixed; }
.name-col { width: 26%; }
.price-col { width: 30%; }
.quotes td { vertical-align: top; }
.quotes .name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.total .line { display: inline-flex; align-items: baseline; justify-content: flex-end; flex-wrap: wrap; gap: 4px; }
.total .el-button { margin-inline-start: 0; }
.total small { display: block; }
.tags { display: flex; flex-wrap: wrap; column-gap: 8px; row-gap: 2px; }
.pass { color: var(--si-ink-2); }
tr.expand > td { padding: 0 0 8px 0; background: transparent; }
.pages { display: flex; flex-wrap: wrap; gap: 4px 16px; margin: 4px 0 0; padding: 0; list-style: none; }
.check-line { display: grid; grid-template-columns: 24px minmax(0, 1fr) minmax(0, auto) auto; gap: 8px; align-items: baseline; }
.check-line .num { color: var(--si-ink-3); }
.row-actions { display: flex; align-items: baseline; gap: 10px; }
.save-error { margin: 4px 0; color: var(--si-danger-ink); font: var(--si-font-meta); }
.detail summary { cursor: pointer; display: list-item; }
h4 { margin: 16px 0 4px; font: var(--si-font-h4); }
.compact { max-width: 880px; }
.wide { overflow-x: auto; }
.odd { color: var(--si-warn-ink); }
.curves { display: flex; flex-direction: column; gap: 20px; margin-top: 16px; max-width: 880px; }
@media (max-width: 1100px) {
  .check-line { grid-template-columns: 20px minmax(0, 1fr) auto; }
  .check-line > .bid-meta { grid-column: 2; grid-row: 2; }
}
</style>
