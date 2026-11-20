<script setup lang="ts">
import { computed, inject, ref } from 'vue'
import type { BidCase } from '@/api/bid'
import { ChevronRight } from '@lucide/vue'
import BidItemCard from './BidItemCard.vue'
import { bidResultsKey } from './results'

defineProps<{ item: BidCase }>()
const ctx = inject(bidResultsKey)!
const open = ref<number | null>(null)
const normal = ref(false)
const items = computed(() => new Map(ctx.data.value?.items.map(i => [i.id, i])))
</script>

<template>
  <article class="bid-case">
    <header>
      <h4 class="bid-tier" :class="item.level">{{ item.title }}</h4>
      <span class="bid-tier-label" :class="item.level">{{ item.level === 'severe' ? '强证据' : '中等证据' }}</span>
    </header>
    <ol class="chain">
      <li v-for="(e, index) in item.evidence" :key="e.item_id + ':' + e.row">
        <div class="fact">
          <span class="num">{{ index + 1 }}</span>
          <span>{{ e.fact }}<small v-if="item.bidder_ids.length > 2"> · {{ e.bidder_ids.map(id => ctx.shorts.value[id]).join('、') }}</small></span>
          <button type="button" class="bid-link" :aria-expanded="open === index" @click="open = open === index ? null : index">查看 ›</button>
        </div>
        <BidItemCard v-if="open === index && items.has(e.item_id)" :item="items.get(e.item_id)!" :row-index="e.row" bare />
      </li>
    </ol>
    <div class="case-actions">
      <span v-if="item.baseline.sentences || item.baseline.images" class="bid-meta">已剔除：与招标文件相同的句子 {{ item.baseline.sentences ?? 0 }} 句、图片 {{ item.baseline.images ?? 0 }} 张</span>
      <button v-if="item.normal.length" type="button" class="bid-fold" :aria-expanded="normal" @click="normal = !normal"><ChevronRight class="chev" :class="{ turned: normal }" :size="14" />常见的正常原因</button>
    </div>
    <ul v-if="normal" class="bid-fine"><li v-for="reason in item.normal" :key="reason">{{ reason }}</li></ul>
  </article>
</template>

<style scoped>
.bid-case { border: 1px solid var(--si-hair); border-radius: var(--si-radius-card); padding: 16px 20px; background: transparent; }
header, .case-actions { display: flex; align-items: baseline; gap: 8px; }
header { justify-content: space-between; }
h4 { margin: 0; font: var(--si-font-h4); overflow-wrap: anywhere; }
.bid-tier-label { flex: none; font: var(--si-font-label-sm); }
.chain { margin: 5px 0; padding: 0; list-style: none; }
.chain li { border-bottom: 1px solid var(--si-hair-faint); }
.fact { display: grid; grid-template-columns: 24px minmax(0, 1fr) auto; align-items: baseline; gap: 8px; padding: 1px 0; font: var(--si-font-meta); overflow-wrap: anywhere; }
.fact .num, .fact small { color: var(--si-ink-3); }
.case-actions { flex-wrap: wrap; }
.case-actions .bid-fold { margin-top: 0; }
</style>
