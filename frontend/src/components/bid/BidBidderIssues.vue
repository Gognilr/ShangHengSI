<script setup lang="ts">
import { computed, inject, ref } from 'vue'
import { ChevronRight } from '@lucide/vue'
import { ApiError } from '@/api/http'
import { bidApi, bidGroupLabel, bidLevelLabel, type Bidder, type BidLevel, type BidReview, type BidSignMark, type BidSigning } from '@/api/bid'
import SiSegmented from '@/components/ui/SiSegmented.vue'
import BidItemCard from './BidItemCard.vue'
import BidReviewBar from './BidReviewBar.vue'
import BidCellDrawer from './BidCellDrawer.vue'
import BidLede from './BidLede.vue'
import { bidResultsKey, progress, type LedePart } from './results'

// 各投标人（V34Q-25 第 6 条）：一页列全这一家要核的东西——问题清单｜涉及的串标线索｜需人工核对的要求｜签章页，
// 总览“核对进度”的每一项都能在这里找到并就地标注。
const props = defineProps<{ bidders: Bidder[] }>()
const current = defineModel<string>({ required: true })
const ctx = inject(bidResultsKey)!
const options = computed(() => props.bidders.map(b => ({ value: b.id, label: b.name.length > 14 ? b.name.slice(0, 14) + '…' : b.name })))
const name = computed(() => ctx.names.value[current.value] ?? '')
const mine = computed(() => (ctx.data.value?.items ?? []).filter(i => i.bidder_ids.includes(current.value)))
const issues = computed(() => mine.value.filter(i => i.kind === 'issue' && i.review_status !== 'excluded'))
const clues = computed(() => mine.value.filter(i => i.kind === 'clue' && i.review_status !== 'excluded'))
const excluded = computed(() => mine.value.filter(i => i.kind !== 'normal_dup' && i.review_status === 'excluded'))
const groups = computed(() => (['veto', 'general', 'hint'] as BidLevel[]).map(level => ({ level, items: issues.value.filter(i => i.level === level) })).filter(g => g.items.length))
const showExcluded = ref(false)

// 需人工核对的要求：人工核对行，以及本家无法判断或引文待核实的格（已排除的划线保留，不计数）
const reqs = computed(() => {
  const d = ctx.data.value
  if (!d) return []
  const cells = new Map(d.cells.filter(c => c.bidder_id === current.value).map(c => [c.requirement_id, c]))
  return d.requirements.flatMap(r => {
    const c = cells.get(r.id)
    const why = r.method === 'manual' ? '人工核对' : c?.verdict === 'unclear' ? '无法判断' : c?.cite === 'unverified' ? '引文待核实' : null
    if (!why || (r.method !== 'manual' && c?.verdict === 'manual')) return []
    const review: BidReview = c ? c : { review_status: 'pending', note: null }
    return [{ id: r.id, text: r.text, group: bidGroupLabel[r.group], why, review, excluded: review.review_status === 'excluded' }]
  })
})
const openReqs = computed(() => reqs.value.filter(r => !r.excluded))
const drawer = ref<string | null>(null)

const pages = computed(() => (ctx.data.value?.signing ?? []).filter(s => s.bidder_id === current.value))
const thumbs = computed(() => pages.value.filter(s => s.renderable && s.page))
const lines = computed(() => pages.value.filter(s => !(s.renderable && s.page)))
const marked = computed(() => pages.value.filter(s => s.mark).length)
const marks: { value: BidSignMark; label: string }[] = [{ value: 'stamped', label: '已盖章' }, { value: 'missing', label: '缺章' }, { value: 'unclear', label: '看不清' }]
const signError = ref('')
async function mark(s: BidSigning, value: BidSignMark) {
  const before = s.mark, next = before === value ? null : value
  s.mark = next; signError.value = ''
  try { await bidApi.sign(ctx.projectId, s, next) }
  catch (e) { s.mark = before; signError.value = e instanceof ApiError ? e.message : '标注没有保存，请重试' }
}
const prog = computed(() => progress(ctx.todos.value, current.value))
const lede = computed<LedePart[]>(() => {
  const veto = issues.value.filter(i => i.level === 'veto').length, other = issues.value.length - veto
  const parts: LedePart[] = [[[name.value, 'b'], '：', veto ? [`疑似否决项 ${veto} 条`, 'd'] : '未发现疑似否决项', other ? `，其他问题 ${other} 条` : '']]
  if (clues.value.length) parts.push([`涉及 ${clues.value.length} 条串标线索`])
  if (openReqs.value.length) parts.push([`${openReqs.value.length} 项要求需人工核对`])
  if (pages.value.length > marked.value) parts.push([`${pages.value.length - marked.value} 页签章页待标注`])
  return parts
})
</script>

<template>
  <div class="bid-tab">
    <SiSegmented v-if="bidders.length > 1" v-model="current" class="seg" :options="options" ariaLabel="投标人" />
    <BidLede :parts="lede" :sub="`核对进度 已核 ${prog.done} / 共 ${prog.total}`" />

    <section class="bid-block">
      <h3 class="bid-title">问题清单<span class="n">{{ issues.length }}</span></h3>
      <div v-for="g in groups" :key="g.level" class="group">
        <h4 :class="g.level">{{ bidLevelLabel[g.level] }}<span class="num">{{ g.items.length }}</span></h4>
        <BidItemCard v-for="item in g.items" :key="item.id" :item="item" hide-who />
      </div>
      <p v-if="!groups.length" class="bid-empty">本地检查与模型核对未发现这一家的不符合项。</p>
    </section>

    <section v-if="clues.length" class="bid-block">
      <h3 class="bid-title">涉及的串标线索<span class="n">{{ clues.length }}</span></h3>
      <div class="list"><BidItemCard v-for="item in clues" :key="item.id" :item="item" /></div>
    </section>

    <section class="bid-block">
      <h3 class="bid-title">需人工核对的要求<span class="n">{{ openReqs.length }}</span></h3>
      <ul v-if="reqs.length" class="reqs">
        <li v-for="r in reqs" :key="r.id" :class="{ excluded: r.excluded }">
          <button type="button" class="req" @click="drawer = r.id"><span class="meta">{{ r.group }} · {{ r.why }}</span><span class="text">{{ r.text }}</span></button>
          <BidReviewBar :review="r.review" @change="patch => ctx.reviewCell(r.id, current, patch)" />
        </li>
      </ul>
      <p v-else class="bid-empty">没有需要人工核对的要求。</p>
    </section>

    <section class="bid-block">
      <h3 class="bid-title">签章页<span class="n">{{ marked }} / {{ pages.length }}</span><span class="end">点缩略图看大图</span></h3>
      <p v-if="signError" class="error" role="alert">{{ signError }}</p>
      <div v-if="thumbs.length" class="sheets">
        <figure v-for="s in thumbs" :key="s.file_id + s.page" class="sheet" :class="s.mark">
          <button type="button" class="thumb" :aria-label="`查看 ${s.title} 大图`" @click="ctx.openPage(s.file_id, s.page!)">
            <img :src="bidApi.pageUrl(ctx.projectId, s.file_id, s.page!, 360)" :alt="`${s.file} 第 ${s.page} 页`" loading="lazy">
          </button>
          <figcaption>
            <span class="t">{{ s.title }}</span><span class="hint">{{ s.file }} · 第 {{ s.page }} 页</span>
            <span class="marks" role="radiogroup" :aria-label="`${s.title} 的签章`">
              <button v-for="m in marks" :key="m.value" type="button" role="radio" :aria-checked="s.mark === m.value" :class="[m.value, { on: s.mark === m.value }]" @click="mark(s, m.value)">{{ m.label }}</button>
            </span>
          </figcaption>
        </figure>
      </div>
      <ul v-if="lines.length" class="lines">
        <li v-for="s in lines" :key="s.file_id + s.loc">
          <span class="t">{{ s.title }}</span><span class="hint">{{ s.file }}<template v-if="s.page"> · 第 {{ s.page }} 页</template> · 到原件核对</span>
          <span class="marks" role="radiogroup" :aria-label="`${s.title} 的签章`">
            <button v-for="m in marks" :key="m.value" type="button" role="radio" :aria-checked="s.mark === m.value" :class="[m.value, { on: s.mark === m.value }]" @click="mark(s, m.value)">{{ m.label }}</button>
          </span>
        </li>
      </ul>
      <p v-if="!pages.length" class="bid-empty">没有在这一家的文件中找到投标函、授权委托书、报价表、承诺函等要求盖章签字的格式页；扫描页识别完成后会补上。</p>
    </section>

    <section v-if="excluded.length" class="bid-block">
      <button type="button" class="bid-fold" :aria-expanded="showExcluded" @click="showExcluded = !showExcluded">
        <ChevronRight :size="14" :stroke-width="1.75" class="chev" :class="{ turned: showExcluded }" aria-hidden="true" />已排除 {{ excluded.length }}
      </button>
      <div v-if="showExcluded" class="list"><BidItemCard v-for="item in excluded" :key="item.id" :item="item" hide-who /></div>
    </section>
    <BidCellDrawer v-if="drawer" :requirement-id="drawer" :bidder-id="current" @close="drawer = null" />
  </div>
</template>

<style scoped>
.seg { align-self: flex-start; }
.list, .reqs, .lines { border-top: 1px solid var(--si-hair-strong); }
.group + .group { margin-top: 12px; }
.group h4 { display: flex; align-items: center; gap: 8px; margin: 0; padding: 8px 0; border-bottom: 1px solid var(--si-hair-strong); font: var(--si-font-label-sm); color: var(--si-ink-3); }
.group h4 .num { color: var(--si-ink-4); }
h4.veto { color: var(--si-danger-ink); }
h4.general { color: var(--si-warn-ink); }
.reqs, .lines { margin: 0; padding: 0; list-style: none; }
.reqs li { display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: center; gap: 24px; padding: 10px 0; border-bottom: 1px solid var(--si-hair-faint); }
.req { display: flex; flex-direction: column; gap: 2px; min-width: 0; padding: 0; border: 0; background: transparent; text-align: left; cursor: pointer; }
.req .meta { font: var(--si-font-meta); color: var(--si-ink-3); }
.req .text { display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; font: var(--si-font-body); color: var(--si-ink-1); }
.req:hover .text { text-decoration: underline; text-decoration-color: var(--si-hair-strong); text-underline-offset: 3px; }
.reqs li.excluded .text { color: var(--si-ink-4); text-decoration: line-through; }
.reqs :deep(.note) { min-width: 160px; max-width: 220px; }
.hint { font: var(--si-font-meta); color: var(--si-ink-3); }
.error { margin: 0 0 8px; color: var(--si-danger-ink); font: var(--si-font-meta); }
.sheets { display: grid; grid-template-columns: repeat(auto-fill, minmax(176px, 1fr)); gap: 20px; margin-bottom: 16px; }
.sheet { margin: 0; display: flex; flex-direction: column; gap: 8px; }
.thumb { display: block; box-sizing: border-box; height: 232px; padding: 0; border: 1px solid var(--si-hair); border-radius: var(--si-radius-card); background: var(--si-paper); overflow: hidden; cursor: zoom-in; }
.thumb img { width: 100%; height: 100%; object-fit: contain; object-position: top; display: block; }
.sheet.missing .thumb { border-color: var(--si-danger); }
figcaption { display: flex; flex-direction: column; gap: 4px; }
figcaption .hint { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.t { font: var(--si-font-label); color: var(--si-ink-1); }
.lines li { display: flex; align-items: center; gap: 16px; padding: 10px 0; border-bottom: 1px solid var(--si-hair-faint); }
.lines .t { min-width: 120px; }
.lines .marks { margin: 0 0 0 auto; }
.marks { display: inline-flex; margin-top: 4px; border: 1px solid var(--si-hair-strong); border-radius: 8px; overflow: hidden; align-self: flex-start; }
.marks button { padding: 3px 10px; border: 0; background: transparent; font: var(--si-font-label-sm); color: var(--si-ink-3); cursor: pointer; }
.marks button + button { border-left: 1px solid var(--si-hair); }
.marks button:hover { color: var(--si-ink-1); background: var(--si-hover-bg); }
.marks button.on { font-weight: 600; color: var(--si-ink-1); background: var(--si-surface-2); }
.marks button.missing.on { color: var(--si-danger-ink); }
.marks button.unclear.on { color: var(--si-warn-ink); }
</style>
