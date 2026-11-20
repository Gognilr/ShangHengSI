<script setup lang="ts">
import { computed, inject, ref } from 'vue'
import { ChevronRight } from '@lucide/vue'
import type { BidConclusionEntry } from '@/api/bid'
import BidCaseCard from './BidCaseCard.vue'
import BidCellDetail from './BidCellDetail.vue'
import BidItemCard from './BidItemCard.vue'
import BidLede from './BidLede.vue'
import { bidResultsKey } from './results'

// 问题清单（V35-E-01）：先列严重问题，再列“提示 n 条”（默认折叠）；唯一的操作是“不是问题”，可撤销。
// 新样式与主从两栏留给 F-05，这里只用已有的结果页骨架。
const props = defineProps<{ bidder?: string }>()
const ctx = inject(bidResultsKey)!
const c = computed(() => ctx.data.value?.conclusion)
const mine = (list: BidConclusionEntry[] = []) => props.bidder ? list.filter(e => e.bidder_ids.includes(props.bidder!)) : list
const severe = computed(() => mine(c.value?.severe))
const hint = computed(() => mine(c.value?.hint))
const dismissed = computed(() => mine(c.value?.dismissed))
const items = computed(() => new Map(ctx.data.value?.items.map(i => [i.id, i])))
const cases = computed(() => new Map(c.value?.cases.map(k => [k.id, k])))
const showHint = ref(false), showDismissed = ref(false), open = ref(''), busy = ref('')

function who(e: BidConclusionEntry) { return e.bidder_ids.map(id => ctx.shorts.value[id] ?? '已移除').join('、') }
function cell(e: BidConclusionEntry) {
  const [, rid, bid] = e.id.split(':')
  return e.id.startsWith('cell:') && rid && bid ? { rid, bid } : null
}
async function toggle(e: BidConclusionEntry, dismissed: boolean) {
  busy.value = e.id
  try { await ctx.dismiss(e.id, dismissed) } finally { busy.value = '' }
}
</script>

<template>
  <div class="bid-tab">
    <BidLede v-if="!bidder" :text="c?.lede" :sub="c?.sub" />
    <section v-for="group in [{ key: 'severe', title: '严重问题', list: severe }, { key: 'hint', title: '提示', list: hint }, { key: 'dismissed', title: '不是问题', list: dismissed }]"
             v-show="group.key === 'severe' || group.list.length" :key="group.key" class="bid-block">
      <button v-if="group.key !== 'severe'" type="button" class="bid-fold" :aria-expanded="group.key === 'hint' ? showHint : showDismissed"
              @click="group.key === 'hint' ? showHint = !showHint : showDismissed = !showDismissed">
        <ChevronRight :size="14" :stroke-width="1.75" class="chev" :class="{ turned: group.key === 'hint' ? showHint : showDismissed }" aria-hidden="true" />{{ group.title }} {{ group.list.length }} 条
      </button>
      <h3 v-else class="bid-title">严重问题<span class="n">{{ group.list.length }}</span></h3>
      <ul v-if="group.key === 'severe' || (group.key === 'hint' ? showHint : showDismissed)" class="bid-fine">
        <li v-for="e in group.list" :key="e.id">
          <div class="row">
            <button type="button" class="line" :aria-expanded="open === e.id" @click="open = open === e.id ? '' : e.id">
              <span class="bid-tier" :class="group.key === 'severe' ? 'severe' : 'hint'">{{ e.title }}</span>
              <span class="bid-meta">{{ e.category }}<template v-if="!bidder"> · {{ who(e) }}</template><template v-if="e.source === 'model'"> · 模型补查</template></span>
            </button>
            <el-button size="small" text :disabled="busy === e.id" @click="toggle(e, group.key !== 'dismissed')">{{ group.key === 'dismissed' ? '撤销' : '不是问题' }}</el-button>
          </div>
          <div v-if="open === e.id" class="detail">
            <BidCaseCard v-if="e.case_id && cases.get(e.case_id)" :item="cases.get(e.case_id)!" />
            <BidCellDetail v-else-if="cell(e)" :requirement-id="cell(e)!.rid" :bidder-id="cell(e)!.bid" />
            <BidItemCard v-else-if="e.item_id && items.has(e.item_id)" :item="items.get(e.item_id)!" bare />
            <p v-else class="bid-empty">签章页的标注见“各投标人”。</p>
          </div>
        </li>
      </ul>
      <p v-if="group.key === 'severe' && !group.list.length" class="bid-empty">没有严重问题。</p>
    </section>
  </div>
</template>

<style scoped>
.row { display: flex; align-items: flex-start; gap: 12px; }
.line { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; padding: 0; border: 0; background: transparent; text-align: left; font: var(--si-font-body); color: var(--si-ink-1); cursor: pointer; }
.detail { padding: 12px 0 4px 16px; }
</style>
