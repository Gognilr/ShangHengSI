<script setup lang="ts">
import { inject, ref } from 'vue'
import { ChevronRight } from '@lucide/vue'
import { bidApi, type BidItem, type BidPlace } from '@/api/bid'
import BidReviewBar from './BidReviewBar.vue'
import SimilarMap from './charts/SimilarMap.vue'
import { bidResultsKey, bidTypeLabel, levelTone, sameSpans } from './results'

// 一条问题或线索：标题行（等级色点、类型、条文项、涉及投标人、核对状态），展开为证据、并排对照与核对栏
// bare：只显示证据（逐条核对左栏），标题与核对按钮由外层给出
const props = defineProps<{ item: BidItem; hideWho?: boolean; bare?: boolean; readonly?: boolean; rowIndex?: number }>()
const ctx = inject(bidResultsKey)!
const open = ref(false)
const sectionLabel: Record<string, string> = { tech: '技术参数章节，已降一档', price: '报价章节，已升一档' }
const statusMark: Record<string, string> = { verified: '已核实', excluded: '已排除' }

// 条文项：线索存“（四）”，问题存“第五十一条（五）”或“（三）”
function article() {
  const a = props.item.article ?? ''
  return props.item.kind === 'clue' ? `第四十条${a}` : a.startsWith('第') ? `参照${a}` : `参照第五十一条${a}`
}
function who() { return props.item.bidder_ids.map(id => ctx.names.value[id] ?? '已移除的投标人').join(' · ') }
function place(p: BidPlace) { return (ctx.fileNames.value[p.file_id] ?? '文件') + (p.page ? ` 第 ${p.page} 页` : '') }
function pageOf(list: BidPlace[]) { return list.find(p => p.page && ctx.renderable.value[p.file_id]) }
</script>

<template>
  <article class="item" :class="{ excluded: item.review_status === 'excluded', bare }">
    <button v-if="!bare" type="button" class="head" :aria-expanded="open" @click="open = !open">
      <ChevronRight :size="14" :stroke-width="1.75" class="chev" :class="{ turned: open }" aria-hidden="true" />
      <span class="main">
        <span class="title" :class="levelTone(item.level)">{{ item.title }}</span>
        <span class="tags">{{ bidTypeLabel[item.type] ?? item.type }}<template v-if="item.source === 'model'"> · 模型</template><template v-if="item.article"> · {{ article() }}</template><template v-if="!hideWho"><i aria-hidden="true">|</i><span class="who">{{ who() }}</span></template></span>
      </span>
      <span v-if="statusMark[item.review_status]" class="status" :class="item.review_status">{{ statusMark[item.review_status] }}</span>
    </button>
    <div v-if="open || bare" class="detail">
      <SimilarMap v-if="rowIndex === undefined && item.type === 'similar' && item.map?.segs.length" class="map" :map="item.map" :a="item.bidder_ids[0]!" :b="item.bidder_ids[1]!" :labels="ctx.shorts.value" :file-names="ctx.fileNames.value" />
      <dl v-for="(row, index) in rowIndex === undefined ? item.rows : item.rows.slice(rowIndex, rowIndex + 1)" :key="index">
        <dt>{{ row.label }}<template v-if="row.count"> · {{ row.count }} 处</template>
          <template v-if="row.chars"> · 约 {{ row.chars }} 字 · {{ row.sentences }} 句<template v-if="row.section && sectionLabel[row.section]">（{{ sectionLabel[row.section] }}）</template></template></dt>
        <dd v-if="row.value" class="value">{{ row.value }}</dd>
        <dd v-if="row.width" class="hint num">{{ row.width }} × {{ row.height }}</dd>
        <dd v-for="(file, bidder) in row.files ?? {}" :key="'f' + bidder" class="hint">{{ ctx.names.value[bidder] }}：{{ file }}</dd>
        <dd v-if="row.places" class="places" :class="{ pair: rowIndex !== undefined && Object.keys(row.places).length > 1 }">
          <div v-for="(list, bidder) in row.places" :key="bidder" class="locations">
            <div v-for="(p, i) in list" :key="i">
              <span class="hint">{{ ctx.names.value[bidder] }} · {{ place(p) }}<button v-if="p.page && ctx.renderable.value[p.file_id]" type="button" class="link" @click="ctx.openPage(p.file_id, p.page!)">查看原件页</button></span>
              <span v-if="p.excerpt" class="excerpt">{{ p.excerpt }}</span>
            </div>
            <img v-if="rowIndex !== undefined && pageOf(list)" class="proof-page" loading="lazy"
              :src="bidApi.pageUrl(ctx.projectId, pageOf(list)!.file_id, pageOf(list)!.page!, 240)"
              :alt="`${ctx.names.value[bidder]}的证据页`">
          </div>
        </dd>
        <!-- 并排对照：两张纸面，相同文字标出 -->
        <dd v-if="row.left && row.right" class="pair">
          <div v-for="[side, other] in [[row.left, row.right], [row.right, row.left]]" :key="side.bidder_id" class="sheet">
            <span class="hint">{{ ctx.names.value[side.bidder_id] }} · {{ [...new Set(side.places.map(place))].join('、') }}
              <button v-if="pageOf(side.places)" type="button" class="link" @click="ctx.openPage(pageOf(side.places)!.file_id, pageOf(side.places)!.page!)">查看原件页</button></span>
            <p class="text"><template v-for="([t, same], k) in sameSpans(side.text, other.text)" :key="k"><mark v-if="same">{{ t }}</mark><template v-else>{{ t }}</template></template></p>
          </div>
        </dd>
        <dd v-if="row.items?.length" class="prices">
          <table>
            <thead><tr><th>分项</th><th v-for="b in item.bidder_ids.slice(0, 2)" :key="b" class="num" :title="ctx.names.value[b]">{{ ctx.shorts.value[b] }}</th></tr></thead>
            <tbody><tr v-for="(it, i) in row.items" :key="i"><td>{{ it.name }}</td><td class="num">{{ it.left }}</td><td class="num">{{ it.right }}</td></tr></tbody>
          </table>
        </dd>
      </dl>
      <p v-if="item.normal && rowIndex === undefined" class="hint">常见的正常原因：{{ item.normal }}</p>
      <BidReviewBar v-if="!bare && !readonly" class="bar" :review="item" :veto="item.level === 'veto'" @change="patch => ctx.reviewItem(item.id, patch)" />
    </div>
  </article>
</template>

<style scoped>
.item { border-bottom: 1px solid var(--si-hair-faint); }
.head { display: flex; align-items: flex-start; gap: 10px; width: 100%; padding: 12px 0; border: 0; background: transparent; cursor: pointer; text-align: left; font: var(--si-font-body); color: var(--si-ink-1); }
.chev { flex: none; margin-top: 4px; color: var(--si-ink-4); transition: transform .15s; }
.turned { transform: rotate(90deg); }
.main { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
.title { overflow-wrap: anywhere; }
.title.strong { color: var(--si-danger-ink); }
.title.medium { color: var(--si-warn-ink); }
.tags { font: var(--si-font-meta); color: var(--si-ink-3); }
.tags i { margin: 0 8px; font-style: normal; color: var(--si-ink-5); }
.who { color: var(--si-ink-2); }
.status { flex: none; margin-top: 2px; font: var(--si-font-label-sm); color: var(--si-ink-3); }
.status.verified { color: var(--si-ink-1); }
.excluded .title, .excluded .tags { color: var(--si-ink-4); text-decoration: line-through; text-decoration-color: var(--si-ink-5); }
.detail { padding: 0 0 16px 24px; }
.bare { border-bottom: 0; }
.bare .detail { padding: 0; }
dl { margin: 0 0 10px; }
dt { font: var(--si-font-label); color: var(--si-ink-2); }
dd { margin: 4px 0 0; font: var(--si-font-body); color: var(--si-ink-1); display: flex; flex-direction: column; gap: 2px; }
.value { font-weight: 600; }
.excerpt { display: block; color: var(--si-ink-2); overflow-wrap: anywhere; }
.locations { min-width: 0; overflow-wrap: anywhere; }
.proof-page { display: block; max-width: min(100%, 180px); margin-top: 6px; border: 1px solid var(--si-hair); }
.link { margin-left: 8px; padding: 0; border: 0; background: transparent; font: var(--si-font-meta); color: var(--si-ink-2); text-decoration: underline; text-decoration-color: var(--si-hair-strong); text-underline-offset: 3px; cursor: pointer; }
.link:hover { color: var(--si-ink-1); }
.pair { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.sheet { min-width: 0; padding: 12px 14px; border: 1px solid var(--si-hair); border-radius: var(--si-radius-card); background: var(--si-paper); }
.sheet .text { margin: 6px 0 0; font: var(--si-font-body); color: var(--si-paper-ink); white-space: pre-wrap; overflow-wrap: anywhere; }
mark { background: var(--si-hl-medium); color: inherit; border-radius: 2px; }
.prices table { border-collapse: collapse; font: var(--si-font-meta); color: var(--si-ink-2); }
.prices th, .prices td { padding: 4px 16px 4px 0; border-bottom: 1px solid var(--si-hair-faint); text-align: left; font-weight: normal; }
.prices th { color: var(--si-ink-3); }
.prices .num { text-align: right; }
.hint { font: var(--si-font-meta); color: var(--si-ink-3); }
.map { margin-bottom: 16px; max-width: 880px; }
.bar { margin-top: 12px; padding-top: 12px; border-top: 1px dashed var(--si-hair); }
</style>
