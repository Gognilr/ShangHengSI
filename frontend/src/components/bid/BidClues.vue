<script setup lang="ts">
import { computed, inject, ref } from 'vue'
import { ChevronRight, X } from '@lucide/vue'
import { bidLevelLabel, type BidLevel } from '@/api/bid'
import BidItemCard from './BidItemCard.vue'
import { bidResultsKey } from './results'

// 串标线索页签：强 / 中 / 弱分组；可按一对或一家筛选（来自总览）；“正常重复”单独一组、默认折叠
const props = defineProps<{ pair?: string[] | null }>()
const emit = defineEmits<{ clear: [] }>()
const ctx = inject(bidResultsKey)!
const showNormal = ref(false)
const match = (ids: string[]) => !props.pair?.length || props.pair.every(id => ids.includes(id))
const clues = computed(() => (ctx.data.value?.items ?? []).filter(i => i.kind === 'clue' && match(i.bidder_ids)))
const groups = computed(() => (['strong', 'medium', 'weak'] as BidLevel[]).map(level => ({ level, items: clues.value.filter(i => i.level === level) })).filter(g => g.items.length))
const normal = computed(() => (ctx.data.value?.items ?? []).filter(i => i.kind === 'normal_dup' && match(i.bidder_ids)))
const filterLabel = computed(() => props.pair?.map(id => ctx.names.value[id] ?? '').join(' · '))
</script>

<template>
  <div class="clues">
    <p v-if="pair?.length" class="filter">只看 {{ filterLabel }} 的线索<button type="button" aria-label="取消筛选" @click="emit('clear')"><X :size="14" :stroke-width="1.75" />显示全部</button></p>
    <section v-for="g in groups" :key="g.level" class="group">
      <h3 class="si-overline" :class="g.level">{{ bidLevelLabel[g.level] }}<span class="num">{{ g.items.length }}</span></h3>
      <BidItemCard v-for="item in g.items" :key="item.id" :item="item" />
    </section>
    <p v-if="!groups.length" class="hint">{{ pair?.length ? '这几家之间没有串标线索。' : '本地检查未发现串标线索。' }}没有列出，不等于没有问题，请看总览的覆盖说明。</p>
    <section v-if="normal.length" class="group">
      <button type="button" class="si-overline toggle" :aria-expanded="showNormal" @click="showNormal = !showNormal">
        <ChevronRight :size="14" :stroke-width="1.75" class="chev" :class="{ turned: showNormal }" aria-hidden="true" />正常重复<span class="num">{{ normal.length }}</span>
        <span class="aside">招标范本、厂家资料等，一般不是线索</span>
      </button>
      <template v-if="showNormal"><BidItemCard v-for="item in normal" :key="item.id" :item="item" /></template>
    </section>
  </div>
</template>

<style scoped>
.clues { display: flex; flex-direction: column; gap: 28px; }
.filter { display: flex; align-items: center; gap: 12px; margin: 0; font: var(--si-font-body); color: var(--si-ink-2); }
.filter button { display: inline-flex; align-items: center; gap: 4px; padding: 2px 8px; border: 1px solid var(--si-hair-strong); border-radius: 6px; background: transparent; font: var(--si-font-label-sm); color: var(--si-ink-2); cursor: pointer; }
.group h3, .toggle { display: flex; align-items: center; gap: 8px; margin: 0 0 4px; padding-bottom: 8px; border-bottom: 1px solid var(--si-hair-strong); }
.group h3 .num, .toggle .num { color: var(--si-ink-4); }
h3.strong { color: var(--si-danger-ink); }
h3.medium { color: var(--si-warn-ink); }
.toggle { width: 100%; border: 0; border-bottom: 1px solid var(--si-hair-strong); background: transparent; cursor: pointer; text-align: left; }
.aside { margin-left: auto; font: var(--si-font-meta); letter-spacing: 0; color: var(--si-ink-4); }
.chev { flex: none; color: var(--si-ink-4); transition: transform .15s; }
.turned { transform: rotate(90deg); }
.hint { margin: 0; font: var(--si-font-body); color: var(--si-ink-3); }
</style>
