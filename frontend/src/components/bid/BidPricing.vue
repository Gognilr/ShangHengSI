<script setup lang="ts">
import { computed, inject } from 'vue'
import type { Bidder } from '@/api/bid'
import BidItemCard from './BidItemCard.vue'
import { bidResultsKey, levelTone, pricingTypes } from './results'

// 报价分析（bid.md 2.4 第 5 项）：各家总价与限价、分项对齐表、两两比值及离散程度。按录入顺序，不排序、不标最低价。
const props = defineProps<{ bidders: Bidder[] }>()
const ctx = inject(bidResultsKey)!
const pricing = computed(() => ctx.data.value?.pricing)
const params = computed(() => ctx.data.value?.parameters)
const issues = computed(() => (ctx.data.value?.items ?? []).filter(i => i.kind === 'issue' && pricingTypes.has(i.type)))
const cols = computed(() => props.bidders.filter(b => pricing.value?.bidders[b.id]))
const name = (id: string) => ctx.names.value[id] ?? '已移除的投标人'
function ceilingText(over: string | null | undefined) {
  if (over == null) return params.value?.ceiling ? '—' : '未填最高限价'
  return over.startsWith('-') ? `低于限价 ${over.slice(1)}` : over === '0.00' ? '等于限价' : `▲ 超限价 ${over}`
}
</script>

<template>
  <div class="pricing">
    <p v-if="!pricing" class="hint">报价检查还没有结果。</p>
    <template v-else>
      <p class="meta">最高限价 <b class="num">{{ params?.ceiling ?? '未填写' }}</b><template v-if="params?.ceiling"> {{ params.currency ?? 'CNY' }}</template> · 金额按 Decimal 计算、四舍五入到分 · 投标人按录入顺序，不排序</p>
      <section>
        <h3 class="sub">各家报价</h3>
        <table>
          <thead><tr><th>投标人</th><th class="r">投标总价</th><th class="r">报价表合计</th><th>与限价</th><th>报价表</th></tr></thead>
          <tbody>
            <tr v-for="b in cols" :key="b.id">
              <td class="name">{{ b.name }}</td>
              <td class="r num"><template v-if="pricing.bidders[b.id]!.total">{{ pricing.bidders[b.id]!.total }}</template>
                <span v-else-if="pricing.bidders[b.id]!.totals_found.length > 1" class="hi">{{ pricing.bidders[b.id]!.totals_found.join(' / ') }}</span><span v-else class="muted">未识别</span></td>
              <td class="r num">{{ pricing.bidders[b.id]!.table?.total ?? '—' }}</td>
              <td :class="{ hi: ceilingText(pricing.bidders[b.id]!.over_ceiling).startsWith('▲') }">{{ ceilingText(pricing.bidders[b.id]!.over_ceiling) }}</td>
              <td class="muted">{{ pricing.bidders[b.id]!.table ? `${pricing.bidders[b.id]!.table!.name || '报价表'} · ${pricing.bidders[b.id]!.table!.rows} 项` : '未识别出报价表' }}</td>
            </tr>
          </tbody>
        </table>
      </section>
      <section v-if="issues.length">
        <h3 class="sub">报价计算与大小写<span>{{ issues.length }} 条</span></h3>
        <BidItemCard v-for="item in issues" :key="item.id" :item="item" />
      </section>
      <section v-if="pricing.pairs.length">
        <h3 class="sub">两两分项比值<span>比值的变异系数越小，说明两家分项价格越像按同一比例算出</span></h3>
        <table>
          <thead><tr><th>两家</th><th class="r">对齐分项</th><th class="r">参与比较</th><th class="r">平均比值</th><th class="r">变异系数</th><th>规律</th></tr></thead>
          <tbody>
            <tr v-for="p in pricing.pairs" :key="p.a + p.b">
              <td>{{ name(p.a) }} · {{ name(p.b) }}</td>
              <td class="r num">{{ p.aligned }}<small class="muted"> {{ p.aligned_by === 'row' ? '按行序' : '按品名' }}</small></td>
              <td class="r num">{{ p.n ?? '—' }}</td>
              <td class="r num">{{ p.mean_ratio ?? '—' }}</td>
              <td class="r num">{{ p.cv ?? '—' }}</td>
              <td><span v-if="p.label" :class="levelTone(p.level!)">{{ p.label }}</span><span v-else class="muted">{{ p.n ? '未见规律' : '对齐不足 5 项' }}</span></td>
            </tr>
          </tbody>
        </table>
      </section>
      <section v-if="pricing.items.length">
        <h3 class="sub">分项对齐<span>以{{ name(pricing.base ?? '') }}的报价表为基准，最多 300 项；“给定”为各家同价的分项，不参与比值</span></h3>
        <div class="scroll">
          <table>
            <thead><tr><th>分项</th><th v-for="b in cols" :key="b.id" class="r" :title="b.name">{{ b.name }}</th></tr></thead>
            <tbody>
              <tr v-for="(it, i) in pricing.items" :key="i">
                <td>{{ it.name || '（无品名）' }}<small v-if="it.fixed" class="muted"> 给定</small></td>
                <td v-for="b in cols" :key="b.id" class="r num">{{ it.prices[b.id] ?? '—' }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
    </template>
  </div>
</template>

<style scoped>
.pricing { display: flex; flex-direction: column; gap: 32px; }
.meta { margin: 0; font: var(--si-font-meta); color: var(--si-ink-3); }
.meta b { color: var(--si-ink-1); font-weight: 600; }
.sub { display: flex; align-items: baseline; gap: 10px; margin: 0 0 8px; font: var(--si-font-h4); color: var(--si-ink-1); }
.sub span { font: var(--si-font-meta); color: var(--si-ink-4); }
.scroll { overflow-x: auto; max-height: 520px; }
table { width: 100%; border-collapse: collapse; font: var(--si-font-body); }
th { position: sticky; top: 0; padding: 8px 12px 8px 0; border-bottom: 1px solid var(--si-hair-strong); background: var(--si-bg-canvas); font: var(--si-font-label-sm); color: var(--si-ink-3); text-align: left; white-space: nowrap; max-width: 180px; overflow: hidden; text-overflow: ellipsis; }
td { padding: 9px 12px 9px 0; border-bottom: 1px solid var(--si-hair-faint); color: var(--si-ink-2); }
.r { text-align: right; }
.name { color: var(--si-ink-1); font-weight: 500; }
.hi { color: var(--si-danger-ink); font-weight: 600; }
.strong { color: var(--si-danger-ink); }
.medium { color: var(--si-warn-ink); }
.muted { color: var(--si-ink-4); font: var(--si-font-meta); }
.hint { margin: 0; font: var(--si-font-body); color: var(--si-ink-3); }
</style>
