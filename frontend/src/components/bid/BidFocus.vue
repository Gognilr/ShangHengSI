<script setup lang="ts">
import { computed, inject, onBeforeUnmount, onMounted, ref } from 'vue'
import { ArrowLeft } from '@lucide/vue'
import { bidReviewLabel, type Bidder } from '@/api/bid'
import BidItemCard from './BidItemCard.vue'
import BidCellDetail from './BidCellDetail.vue'
import BidCellDrawer from './BidCellDrawer.vue'
import BidReviewBar from './BidReviewBar.vue'
import BidSignSheets from './BidSignSheets.vue'
import { bidResultsKey, stepGroupLabel, stepLine, type TodoStep } from './results'

// 逐条核对（V34Q-27 第二部分第 3 条）：左证据、右按钮；Y 核实、X 排除、J / K 下一条 / 上一条，核完自动跳到下一条待核。
// 进入时记下这一轮的各步（已核的步骤退回来仍能看到），各步的状态始终取页面数据。
defineProps<{ bidders: Bidder[] }>()
const at = defineModel<string | null>({ required: true })
const ctx = inject(bidResultsKey)!
const snap = new Map(ctx.steps.value.filter(s => !s.done || s.key === at.value).map(s => [s.key, s]))
const order = [...snap.keys()]
const finished = ref(order.length === 0)
const live = (key: string): TodoStep | undefined => ctx.steps.value.find(s => s.key === key) ?? snap.get(key)
const handled = (key: string) => { const s = ctx.steps.value.find(x => x.key === key); return !s || s.done }  // 不在清单里的为已排除
const step = computed(() => (at.value ? live(at.value) : undefined))
const index = computed(() => order.indexOf(at.value ?? ''))
const doneCount = computed(() => order.filter(handled).length)
const line = computed(() => (step.value ? stepLine(step.value, ctx.shorts.value) : { text: '', meta: '' }))
const short = (id: string) => ctx.shorts.value[id] ?? ''

// 本步当前状态（右栏显示，可改回待核对）
const status = computed(() => {
  const s = step.value
  if (!s) return ''
  if (s.kind === 'item') return s.item.review_status
  if (s.kind === 'cell') return s.cell.review_status
  return ''
})
const yesLabel = computed(() => step.value?.kind === 'manual' ? '各家都符合' : step.value?.kind === 'signing' ? '本家全部已盖章' : '核实')
const noLabel = computed(() => step.value?.kind === 'manual' ? '本条不适用' : step.value?.kind === 'signing' ? '' : '排除')

function go(i: number) { if (i >= 0 && i < order.length) { at.value = order[i]!; finished.value = false } }
function advance() {
  const i = index.value
  const k = [...order.slice(i + 1), ...order.slice(0, i + 1)].find(key => !handled(key))
  if (k) at.value = k
  else finished.value = true
}
async function decide(yes: boolean) {
  const s = step.value
  if (!s || (!yes && !noLabel.value)) return
  const status = yes ? 'verified' : 'excluded'
  if (s.kind === 'item') await ctx.reviewItem(s.item.id, { review_status: status })
  else if (s.kind === 'cell') await ctx.reviewCell(s.cell.requirement_id, s.cell.bidder_id, { review_status: status })
  else if (s.kind === 'manual') {
    const ids = yes ? s.bidder_ids.filter(id => !s.checked.includes(id)) : s.bidder_ids
    if (!(await ctx.reviewBatch([], ids.map(id => ({ requirement_id: s.requirement.id, bidder_id: id })), status))) return
  } else await ctx.markSigning(s.pages.filter(p => !p.mark), 'stamped')
  if (at.value && handled(at.value)) advance()
}
async function reset() {
  const s = step.value
  if (s?.kind === 'item') await ctx.reviewItem(s.item.id, { review_status: 'pending' })
  else if (s?.kind === 'cell') await ctx.reviewCell(s.cell.requirement_id, s.cell.bidder_id, { review_status: 'pending' })
}
const exit = () => { at.value = null }
const manualCell = (rid: string, bid: string) => ctx.data.value?.cells.find(c => c.requirement_id === rid && c.bidder_id === bid) ?? { review_status: 'pending' as const, note: null }
const manualId = computed(() => (step.value?.kind === 'manual' ? step.value.requirement.id : ''))
const drawer = ref<string | null>(null)

function onKey(e: KeyboardEvent) {
  if (e.ctrlKey || e.metaKey || e.altKey || drawer.value) return
  const t = e.target as HTMLElement | null
  if (t && (t.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(t.tagName))) return
  // 有对话框（原件大图、确认框）打开时不处理
  if ([...document.querySelectorAll<HTMLElement>('.el-overlay')].some(o => o.style.display !== 'none')) return
  const k = e.key.toLowerCase()
  if (k === 'y') void decide(true)
  else if (k === 'x') void decide(false)
  else if (k === 'j' || e.key === 'ArrowDown') go(index.value + 1)
  else if (k === 'k' || e.key === 'ArrowUp') go(index.value - 1)
  else if (e.key === 'Escape') exit()
  else return
  e.preventDefault()
}
onMounted(() => window.addEventListener('keydown', onKey))
onBeforeUnmount(() => window.removeEventListener('keydown', onKey))
</script>

<template>
  <section class="focus" aria-label="逐条核对">
    <header>
      <button type="button" class="back" @click="exit"><ArrowLeft :size="15" :stroke-width="1.75" aria-hidden="true" />返回清单</button>
      <span class="where num">第 {{ index + 1 }} / {{ order.length }} 条 · 已核 {{ doneCount }}</span>
      <span class="bar" aria-hidden="true"><i :style="{ width: order.length ? `${(doneCount / order.length) * 100}%` : '0' }" /></span>
    </header>

    <div v-if="finished" class="done">
      <p class="bid-lede">这一轮 {{ order.length }} 条已全部核对。</p>
      <button type="button" class="act primary" @click="exit">返回清单</button>
    </div>

    <div v-else-if="step" class="grid">
      <div class="left">
        <p class="overline" :class="step.group">{{ stepGroupLabel[step.group] }}</p>
        <h3 class="title">{{ line.text }}</h3>
        <p class="meta">{{ line.meta }}</p>

        <BidItemCard v-if="step.kind === 'item'" :key="step.key" :item="step.item" bare />
        <BidCellDetail v-else-if="step.kind === 'cell'" :key="step.key" :requirement-id="step.cell.requirement_id" :bidder-id="step.cell.bidder_id" no-bar />
        <ul v-else-if="step.kind === 'manual'" class="each">
          <li v-for="id in step.bidder_ids" :key="id">
            <span class="who" :title="ctx.names.value[id]">{{ short(id) }}</span>
            <BidReviewBar :review="manualCell(step.requirement.id, id)" @change="patch => ctx.reviewCell(manualId, id, patch)" />
            <button type="button" class="link" @click="drawer = id">看要求出处</button>
          </li>
        </ul>
        <BidSignSheets v-else :key="step.key" :pages="step.pages" />
      </div>

      <aside class="right">
        <button type="button" class="act primary big" @click="decide(true)">{{ yesLabel }}<kbd>Y</kbd></button>
        <button v-if="noLabel" type="button" class="act big" @click="decide(false)">{{ noLabel }}<kbd>X</kbd></button>
        <div class="nav">
          <button type="button" class="act" :disabled="index <= 0" @click="go(index - 1)">上一条<kbd>K</kbd></button>
          <button type="button" class="act" :disabled="index >= order.length - 1" @click="go(index + 1)">下一条<kbd>J</kbd></button>
        </div>
        <p v-if="status && status !== 'pending'" class="state">当前：{{ bidReviewLabel[status as 'verified'] }}<button type="button" class="link" @click="reset">改回待核对</button></p>
        <BidReviewBar v-if="step.kind === 'item'" no-states :review="step.item" @change="patch => ctx.reviewItem((step as TodoStep & { kind: 'item' }).item.id, patch)" />
        <BidReviewBar v-else-if="step.kind === 'cell'" no-states :review="step.cell" @change="patch => ctx.reviewCell((step as TodoStep & { kind: 'cell' }).cell.requirement_id, (step as TodoStep & { kind: 'cell' }).cell.bidder_id, patch)" />
        <p v-if="step.kind === 'item' && step.item.level === 'veto'" class="hint">否决性结论须核实，报告中才以肯定语气写出。</p>
        <p class="keys">Y 核实 · X 排除 · J 下一条 · K 上一条 · Esc 返回</p>
      </aside>
    </div>
    <BidCellDrawer v-if="drawer && step?.kind === 'manual'" :requirement-id="step.requirement.id" :bidder-id="drawer" @close="drawer = null" />
  </section>
</template>

<style scoped>
.focus { display: flex; flex-direction: column; gap: 24px; }
header { display: flex; align-items: center; gap: 20px; padding-bottom: 12px; border-bottom: 1px solid var(--si-hair); }
.back { display: inline-flex; align-items: center; gap: 6px; padding: 0; border: 0; background: transparent; font: var(--si-font-label); color: var(--si-ink-2); cursor: pointer; }
.back:hover { color: var(--si-ink-1); }
.where { font: var(--si-font-meta); color: var(--si-ink-3); }
.bar { flex: 1; max-width: 320px; height: 4px; border-radius: 2px; background: var(--si-hair); overflow: hidden; }
.bar i { display: block; height: 100%; background: var(--si-ink-2); transition: width .2s; }
.grid { display: grid; grid-template-columns: minmax(0, 1fr) 240px; gap: 40px; align-items: start; }
.left { min-width: 0; }
.overline { margin: 0 0 4px; font: var(--si-font-label-sm); color: var(--si-ink-3); }
.overline.veto, .overline.strong { color: var(--si-danger-ink); }
.overline.medium { color: var(--si-warn-ink); }
.title { margin: 0; font: 600 17px/1.5 var(--si-font-sans); color: var(--si-ink-1); overflow-wrap: anywhere; white-space: pre-wrap; }
.meta { margin: 4px 0 20px; font: var(--si-font-meta); color: var(--si-ink-3); }
.right { position: sticky; top: 16px; display: flex; flex-direction: column; gap: 10px; }
.act { display: inline-flex; align-items: center; justify-content: center; gap: 8px; padding: 5px 12px; border: 1px solid var(--si-hair-strong); border-radius: 8px; background: transparent; font: var(--si-font-label); color: var(--si-ink-2); cursor: pointer; }
.act:hover:not(:disabled) { color: var(--si-ink-1); background: var(--si-hover-bg); }
.act:disabled { color: var(--si-ink-5); cursor: default; }
.act.primary { border-color: var(--si-ink-1); color: var(--si-ink-1); font-weight: 600; }
.act.big { padding: 9px 12px; }
.nav { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
kbd { padding: 0 5px; border: 1px solid var(--si-hair); border-radius: 4px; font: var(--si-font-code-sm); color: var(--si-ink-4); }
.state { margin: 4px 0 0; font: var(--si-font-meta); color: var(--si-ink-2); }
.link { margin-left: 8px; padding: 0; border: 0; background: transparent; font: var(--si-font-meta); color: var(--si-ink-2); text-decoration: underline; text-underline-offset: 3px; cursor: pointer; }
.hint { margin: 0; font: var(--si-font-meta); color: var(--si-ink-4); }
.keys { margin: 8px 0 0; font: var(--si-font-caption); color: var(--si-ink-4); }
.each { margin: 0; padding: 0; list-style: none; border-top: 1px solid var(--si-hair-strong); }
.each li { display: flex; align-items: center; gap: 20px; padding: 10px 0; border-bottom: 1px solid var(--si-hair-faint); }
.each .who { min-width: 96px; font: var(--si-font-label); color: var(--si-ink-1); }
.each .link { margin-left: auto; }
.done { display: flex; flex-direction: column; align-items: flex-start; gap: 16px; padding: 24px 0; }
</style>
