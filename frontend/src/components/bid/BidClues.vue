<script setup lang="ts">
import { computed, inject, ref } from 'vue'
import { ChevronRight, X } from '@lucide/vue'
import { bidLevelLabel, type Bidder, type BidLevel } from '@/api/bid'
import BidItemCard from './BidItemCard.vue'
import BidLede from './BidLede.vue'
import ClueGraph from './charts/ClueGraph.vue'
import FileTimeline from './charts/FileTimeline.vue'
import { bidResultsKey, levelText, type LedePart } from './results'

// 串标线索：结论 → 强 / 中 / 弱分组（已排除的不计数，折叠在末尾）→ 正常重复（折叠）。可按一对或一家筛选（来自总览）。
const props = defineProps<{ pair?: string[] | null; bidders: Bidder[] }>()
const emit = defineEmits<{ clear: []; pick: [ids: string[]] }>()
const ctx = inject(bidResultsKey)!
const showNormal = ref(false), showExcluded = ref(false)
const match = (ids: string[]) => !props.pair?.length || props.pair.every(id => ids.includes(id))
const all = computed(() => (ctx.data.value?.items ?? []).filter(i => i.kind === 'clue' && match(i.bidder_ids)))
const clues = computed(() => all.value.filter(i => i.review_status !== 'excluded'))
const excluded = computed(() => all.value.filter(i => i.review_status === 'excluded'))
const groups = computed(() => (['strong', 'medium', 'weak'] as BidLevel[]).map(level => ({ level, items: clues.value.filter(i => i.level === level) })).filter(g => g.items.length))
const normal = computed(() => (ctx.data.value?.items ?? []).filter(i => i.kind === 'normal_dup' && match(i.bidder_ids)))
const short = (id: string) => ctx.shorts.value[id] ?? ''
// 关系图（3 家及以上）与文件时间线（V34-B-06d）：不随筛选变化，图上点选即筛选
const nodes = computed(() => props.bidders.map(b => ({ id: b.id, label: short(b.id), name: b.name })))
const edges = computed(() => {
  const live = (ctx.data.value?.items ?? []).filter(i => i.kind === 'clue' && i.review_status !== 'excluded')
  return props.bidders.flatMap((a, k) => props.bidders.slice(k + 1).map(b => {
    const own = live.filter(i => i.bidder_ids.includes(a.id) && i.bidder_ids.includes(b.id))
    return { a: a.id, b: b.id, counts: (['strong', 'medium', 'weak'] as const).map(level => ({ level, n: own.filter(i => i.level === level).length })).filter(c => c.n) }
  }))
})
const timeline = computed(() => ctx.data.value?.timeline ?? [])
const lanes = computed(() => [{ id: null, label: '招标文件', name: '招标文件' }, ...props.bidders.map(b => ({ id: b.id as string | null, label: short(b.id), name: b.name }))])
const filterLabel = computed(() => props.pair?.map(short).join(' · '))
const lede = computed<LedePart[]>(() => {
  if (!clues.value.length) return [[props.pair?.length ? `${filterLabel.value} 之间未见串标线索` : '本地检查未发现串标线索']]
  const pairs = [...new Set(clues.value.map(i => i.bidder_ids.map(short).join(' · ')))]
  const part: LedePart = [`${clues.value.length} 条串标线索（`]
  groups.value.forEach((g, k) => part.push(k ? '、' : '', [`${levelText[g.level]} ${g.items.length}`, g.level === 'strong' ? 'd' : g.level === 'medium' ? 'w' : 'b']))
  part.push(`），涉及 ${pairs.slice(0, 4).join('、')}${pairs.length > 4 ? ' 等' : ''}`)
  const pending = clues.value.filter(i => i.review_status === 'pending').length
  return [part, [pending ? `${pending} 条待核对` : '已全部核对']]
})
</script>

<template>
  <div class="bid-tab">
    <BidLede :parts="lede">
      <span v-if="pair?.length" class="sub filter">只看 {{ filterLabel }}<button type="button" aria-label="取消筛选" @click="emit('clear')"><X :size="13" :stroke-width="1.75" />显示全部</button></span>
    </BidLede>
    <ClueGraph v-if="bidders.length >= 3" :nodes="nodes" :edges="edges" :selected="pair" @pick="ids => emit('pick', ids)" />
    <section v-for="g in groups" :key="g.level" class="bid-block">
      <h3 class="bid-title" :class="'bid-' + g.level">{{ bidLevelLabel[g.level] }}<span class="n">{{ g.items.length }}</span></h3>
      <div class="list"><BidItemCard v-for="item in g.items" :key="item.id" :item="item" /></div>
    </section>
    <section v-if="timeline.some(t => t.bidder_id)" class="bid-block">
      <h3 class="bid-title">文件时间线<span class="n">{{ timeline.length }}</span></h3>
      <FileTimeline :files="timeline" :rows="lanes" :opening="ctx.openingDate" />
    </section>
    <section v-if="excluded.length || normal.length" class="bid-block">
      <button v-if="excluded.length" type="button" class="bid-fold" :aria-expanded="showExcluded" @click="showExcluded = !showExcluded">
        <ChevronRight :size="14" :stroke-width="1.75" class="chev" :class="{ turned: showExcluded }" aria-hidden="true" />已排除 {{ excluded.length }}
      </button>
      <div v-if="showExcluded" class="list"><BidItemCard v-for="item in excluded" :key="item.id" :item="item" /></div>
      <button v-if="normal.length" type="button" class="bid-fold" :aria-expanded="showNormal" @click="showNormal = !showNormal">
        <ChevronRight :size="14" :stroke-width="1.75" class="chev" :class="{ turned: showNormal }" aria-hidden="true" />正常重复 {{ normal.length }}
      </button>
      <div v-if="showNormal" class="list"><BidItemCard v-for="item in normal" :key="item.id" :item="item" /></div>
    </section>
  </div>
</template>

<style scoped>
.list { border-top: 1px solid var(--si-hair-strong); }
.filter { display: flex; align-items: center; gap: 10px; }
.filter button { display: inline-flex; align-items: center; gap: 4px; padding: 1px 8px; border: 1px solid var(--si-hair-strong); border-radius: 6px; background: transparent; font: var(--si-font-label-sm); color: var(--si-ink-2); cursor: pointer; }
.filter button:hover { color: var(--si-ink-1); background: var(--si-hover-bg); }
</style>
