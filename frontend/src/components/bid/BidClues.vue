<script setup lang="ts">
import { computed, inject, ref } from 'vue'
import { ChevronRight } from '@lucide/vue'
import BidCaseCard from './BidCaseCard.vue'
import BidItemCard from './BidItemCard.vue'
import BidLede from './BidLede.vue'
import { bidResultsKey } from './results'

// 串标线索：案件与其余线索，正常重复折叠（V35-E-01 去掉关系图与文件时间线）。
const ctx = inject(bidResultsKey)!
const showNormal = ref(false), showOther = ref(false)
const cases = computed(() => ctx.data.value?.conclusion.cases ?? [])
const caseItems = computed(() => new Set(cases.value.flatMap(c => c.evidence.map(e => e.item_id))))
const other = computed(() => (ctx.data.value?.items ?? []).filter(i => i.kind === 'clue' && !caseItems.value.has(i.id)))
const normal = computed(() => (ctx.data.value?.items ?? []).filter(i => i.kind === 'normal_dup'))
const lede = computed(() => {
  const strong = cases.value.filter(c => c.level === 'severe').length
  if (!cases.value.length) return '未形成疑似串通投标案件' + (other.value.length ? `，另有 ${other.value.length} 条线索` : '') + '。'
  return `发现 ${cases.value.length} 组疑似串通投标` + (strong ? `，其中 ${strong} 组有强证据` : '') + '。'
})
</script>

<template>
  <div class="bid-tab">
    <BidLede :text="lede" />
    <section v-if="cases.length" class="bid-block case-list">
      <BidCaseCard v-for="item in cases" :key="item.id" :item="item" />
    </section>
    <section v-if="other.length || normal.length" class="bid-block">
      <button v-if="other.length" type="button" class="bid-fold" :aria-expanded="showOther" @click="showOther = !showOther">
        <ChevronRight :size="14" :stroke-width="1.75" class="chev" :class="{ turned: showOther }" aria-hidden="true" />其余线索 {{ other.length }}
      </button>
      <div v-if="showOther" class="list"><BidItemCard v-for="item in other" :key="item.id" :item="item" /></div>
      <button v-if="normal.length" type="button" class="bid-fold" :aria-expanded="showNormal" @click="showNormal = !showNormal">
        <ChevronRight :size="14" :stroke-width="1.75" class="chev" :class="{ turned: showNormal }" aria-hidden="true" />正常重复 {{ normal.length }}
      </button>
      <div v-if="showNormal" class="list"><BidItemCard v-for="item in normal" :key="item.id" :item="item" /></div>
    </section>
  </div>
</template>

<style scoped>
.case-list { gap: 8px; }
.list { border-top: 1px solid var(--si-hair-strong); }
</style>
