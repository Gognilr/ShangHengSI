<script setup lang="ts">
import { computed, inject, ref } from 'vue'
import { ChevronRight, X } from '@lucide/vue'
import type { Bidder } from '@/api/bid'
import BidCaseCard from './BidCaseCard.vue'
import BidItemCard from './BidItemCard.vue'
import BidLede from './BidLede.vue'
import ClueGraph from './charts/ClueGraph.vue'
import FileTimeline from './charts/FileTimeline.vue'
import { bidResultsKey, type LedePart } from './results'

// 串标线索作为证据页，复用结论中的案件。参考线索集中折叠，判定入口留在初审结论。
const props = defineProps<{ pair?: string[] | null; bidders: Bidder[] }>()
const emit = defineEmits<{ clear: []; pick: [ids: string[]] }>()
const ctx = inject(bidResultsKey)!
const showNormal = ref(false), showExcluded = ref(false), showReference = ref(false)
const match = (ids: string[]) => !props.pair?.length || props.pair.every(id => ids.includes(id))
const all = computed(() => (ctx.data.value?.items ?? []).filter(i => i.kind === 'clue' && match(i.bidder_ids)))
const allCases = computed(() => ctx.data.value?.conclusion.cases ?? [])
const cases = computed(() => allCases.value.filter(c => match(c.bidder_ids)))
const caseItems = computed(() => new Set(allCases.value.flatMap(c => c.evidence.map(e => e.item_id))))
const excluded = computed(() => all.value.filter(i => i.review_status === 'excluded' || i.reference_reason))
const reference = computed(() => all.value.filter(i => i.review_status !== 'excluded' && !i.reference_reason && !caseItems.value.has(i.id)))
const normal = computed(() => (ctx.data.value?.items ?? []).filter(i => i.kind === 'normal_dup' && match(i.bidder_ids)))
const short = (id: string) => ctx.shorts.value[id] ?? ''
// 关系图（3 家及以上）与文件时间线（V34-B-06d）：不随筛选变化，图上点选即筛选
const nodes = computed(() => props.bidders.map(b => ({ id: b.id, label: short(b.id), name: b.name })))
const edges = computed(() => {
  const live = ctx.data.value?.items ?? []
  return props.bidders.flatMap((a, k) => props.bidders.slice(k + 1).map(b => {
    const own = live.filter(i => caseItems.value.has(i.id) && i.bidder_ids.includes(a.id) && i.bidder_ids.includes(b.id))
    return { a: a.id, b: b.id, counts: (['strong', 'medium', 'weak'] as const).map(level => ({ level, n: own.filter(i => i.level === level).length })).filter(c => c.n) }
  }))
})
const timeline = computed(() => ctx.data.value?.timeline ?? [])
const lanes = computed(() => [{ id: null, label: '招标文件', name: '招标文件' }, ...props.bidders.map(b => ({ id: b.id as string | null, label: short(b.id), name: b.name }))])
const filterLabel = computed(() => props.pair?.map(short).join(' · '))
const lede = computed<LedePart[]>(() => {
  if (!cases.value.length) {
    const intro = props.pair?.length ? `${filterLabel.value} 之间未形成疑似串通投标案件` : '未形成疑似串通投标案件'
    return [[intro + (reference.value.length ? `，另有 ${reference.value.length} 条参考线索` : '')]]
  }
  return [[`发现 ${cases.value.length} 组疑似串通投标`]]
})
</script>

<template>
  <div class="bid-tab">
    <BidLede :parts="lede">
      <span v-if="pair?.length" class="sub filter">只看 {{ filterLabel }}<button type="button" aria-label="取消筛选" @click="emit('clear')"><X :size="13" :stroke-width="1.75" />显示全部</button></span>
    </BidLede>
    <button v-if="ctx.data.value?.conclusion.verify.some(e => e.case_id)" type="button" class="bid-fold" @click="ctx.focus()">需你核实</button>
    <ClueGraph v-if="bidders.length >= 3" :nodes="nodes" :edges="edges" :selected="pair" @pick="ids => emit('pick', ids)" />
    <section v-if="cases.length" class="bid-block case-list">
      <BidCaseCard v-for="item in cases" :key="item.id" :item="item" readonly />
    </section>
    <section v-if="timeline.some(t => t.bidder_id)" class="bid-block">
      <h3 class="bid-title">文件时间线<span class="n">{{ timeline.length }}</span></h3>
      <FileTimeline :files="timeline" :rows="lanes" :opening="ctx.openingDate" />
    </section>
    <section v-if="reference.length || excluded.length || normal.length" class="bid-block">
      <button v-if="reference.length" type="button" class="bid-fold" :aria-expanded="showReference" @click="showReference = !showReference">
        <ChevronRight :size="14" :stroke-width="1.75" class="chev" :class="{ turned: showReference }" aria-hidden="true" />参考线索 {{ reference.length }}
      </button>
      <div v-if="showReference" class="list"><BidItemCard v-for="item in reference" :key="item.id" :item="item" readonly /></div>
      <button v-if="excluded.length" type="button" class="bid-fold" :aria-expanded="showExcluded" @click="showExcluded = !showExcluded">
        <ChevronRight :size="14" :stroke-width="1.75" class="chev" :class="{ turned: showExcluded }" aria-hidden="true" />已排除 {{ excluded.length }}
      </button>
      <div v-if="showExcluded" class="list"><BidItemCard v-for="item in excluded" :key="item.id" :item="item" readonly /></div>
      <button v-if="normal.length" type="button" class="bid-fold" :aria-expanded="showNormal" @click="showNormal = !showNormal">
        <ChevronRight :size="14" :stroke-width="1.75" class="chev" :class="{ turned: showNormal }" aria-hidden="true" />正常重复 {{ normal.length }}
      </button>
      <div v-if="showNormal" class="list"><BidItemCard v-for="item in normal" :key="item.id" :item="item" readonly /></div>
    </section>
  </div>
</template>

<style scoped>
.case-list { gap: 8px; }
.list { border-top: 1px solid var(--si-hair-strong); }
.filter { display: flex; align-items: center; gap: 10px; }
.filter button { display: inline-flex; align-items: center; gap: 4px; padding: 1px 8px; border: 1px solid var(--si-hair-strong); border-radius: 6px; background: transparent; font: var(--si-font-label-sm); color: var(--si-ink-2); cursor: pointer; }
.filter button:hover { color: var(--si-ink-1); background: var(--si-hover-bg); }
</style>
