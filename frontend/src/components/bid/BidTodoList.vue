<script setup lang="ts">
import { computed, inject, ref } from 'vue'
import { ChevronRight } from '@lucide/vue'
import type { Bidder, BidReviewStatus } from '@/api/bid'
import { bidResultsKey, calcPending, confirmCalc, stepGroupLabel, stepLine, type StepGroup, type TodoStep } from './results'

// 待你处理（V34Q-27 第二部分第 2、4、5、6 条）：只列待核的事项，按 疑似否决 → 强线索 → 中线索 → 一般问题 → 人工核对 → 签章页。
// 每行一句话 + 就地“核实 / 排除”；人工核对一条要求一行、各家并排勾选；签章页一家一行、“本家全部已盖章”。点一行进入逐条核对。
const props = defineProps<{ bidders: Bidder[] }>()
const ctx = inject(bidResultsKey)!
const PER_GROUP = 5
const open = computed(() => ctx.steps.value.filter(s => !s.done))
const groups = computed(() => (Object.keys(stepGroupLabel) as StepGroup[])
  .map(g => ({ g, list: open.value.filter(s => s.group === g) })).filter(x => x.list.length))
const more = ref<Set<StepGroup>>(new Set())
const shown = (g: StepGroup, list: TodoStep[]) => more.value.has(g) ? list : list.slice(0, PER_GROUP)
const units = computed(() => ctx.todos.value.filter(t => !t.done).length)
const calc = computed(() => calcPending(ctx.data.value).length)
const line = (s: TodoStep) => stepLine(s, ctx.shorts.value)
const short = (id: string) => ctx.shorts.value[id] ?? ''

// 刚做的一步可撤销（行在核完后离开清单，误点时能找回）
const last = ref<{ text: string; undo: () => Promise<unknown> } | null>(null)
async function setItem(s: TodoStep, status: BidReviewStatus) {
  const text = `${status === 'verified' ? '已核实' : '已排除'}：${line(s).text}`
  if (s.kind === 'item') {
    const id = s.item.id
    await ctx.reviewItem(id, { review_status: status })
    last.value = { text, undo: () => ctx.reviewItem(id, { review_status: 'pending' }) }
  } else if (s.kind === 'cell') {
    const { requirement_id: rid, bidder_id: bid } = s.cell
    await ctx.reviewCell(rid, bid, { review_status: status })
    last.value = { text, undo: () => ctx.reviewCell(rid, bid, { review_status: 'pending' }) }
  }
}
function toggle(s: TodoStep & { kind: 'manual' }, bid: string) {
  last.value = null
  return ctx.reviewCell(s.requirement.id, bid, { review_status: s.checked.includes(bid) ? 'pending' : 'verified' })
}
async function allOk(s: TodoStep & { kind: 'manual' }) {
  const refs = s.bidder_ids.filter(id => !s.checked.includes(id)).map(id => ({ requirement_id: s.requirement.id, bidder_id: id }))
  if (await ctx.reviewBatch([], refs, 'verified')) last.value = { text: `各家都符合：${s.requirement.text}`, undo: () => ctx.reviewBatch([], refs, 'pending') }
}
async function allStamped(s: TodoStep & { kind: 'signing' }) {
  const pages = s.pages.filter(p => !p.mark)
  await ctx.markSigning(pages, 'stamped')
  last.value = { text: `${short(s.bidder_id)}：${pages.length} 页标为已盖章`, undo: () => ctx.markSigning(pages, null) }
}
async function undo() { const u = last.value; last.value = null; await u?.undo() }
</script>

<template>
  <section class="bid-block">
    <h3 class="bid-title">待你处理<span class="n">{{ open.length }}</span>
      <span class="end">
        <button v-if="calc" type="button" class="act" @click="confirmCalc(ctx)">确认本机计算的 {{ calc }} 条</button>
        <button v-if="open.length" type="button" class="act primary" @click="ctx.focus()">逐条核对<ChevronRight :size="14" :stroke-width="1.75" aria-hidden="true" /></button>
      </span>
    </h3>
    <p v-if="open.length" class="sum num">{{ units }} 项待核，合并为 {{ open.length }} 行：人工核对一条要求一行、各家并排，签章页一家一行</p>
    <p v-if="last" class="undo" role="status">{{ last.text }}<button type="button" class="link" @click="undo">撤销</button></p>
    <p v-if="!open.length" class="bid-empty">全部事项已核对。可以导出 Word 报告。</p>
    <div v-for="x in groups" :key="x.g" class="group">
      <h4 :class="x.g">{{ stepGroupLabel[x.g] }}<span class="num">{{ x.list.length }}</span></h4>
      <ul>
        <li v-for="s in shown(x.g, x.list)" :key="s.key" :class="s.kind">
          <button type="button" class="what" :title="'逐条核对这一条'" @click="ctx.focus(s.key)">
            <span class="text">{{ line(s).text }}</span><span class="meta">{{ line(s).meta }}</span>
          </button>
          <span v-if="s.kind === 'item' || s.kind === 'cell'" class="acts">
            <button type="button" class="act" @click="setItem(s, 'verified')">核实</button>
            <button type="button" class="act" @click="setItem(s, 'excluded')">排除</button>
          </span>
          <span v-else-if="s.kind === 'manual'" class="acts">
            <span class="checks" role="group" :aria-label="`${s.requirement.text} 各家核对`">
              <label v-for="id in s.bidder_ids" :key="id" :title="ctx.names.value[id]"><input type="checkbox" :checked="s.checked.includes(id)" @change="toggle(s, id)">{{ short(id) }}</label>
            </span>
            <button type="button" class="act" @click="allOk(s)">各家都符合</button>
          </span>
          <span v-else class="acts">
            <button type="button" class="act" @click="ctx.focus(s.key)">逐页看</button>
            <button type="button" class="act" @click="allStamped(s)">本家全部已盖章</button>
          </span>
        </li>
      </ul>
      <button v-if="x.list.length > PER_GROUP && !more.has(x.g)" type="button" class="bid-fold" @click="more = new Set([...more, x.g])">
        <ChevronRight :size="14" :stroke-width="1.75" class="chev" aria-hidden="true" />其余 {{ x.list.length - PER_GROUP }} 条
      </button>
    </div>
  </section>
</template>

<style scoped>
.end { display: inline-flex; gap: 8px; }
.act { display: inline-flex; align-items: center; gap: 2px; padding: 3px 10px; border: 1px solid var(--si-hair-strong); border-radius: 8px; background: transparent; font: var(--si-font-label-sm); color: var(--si-ink-2); cursor: pointer; white-space: nowrap; }
.act:hover { color: var(--si-ink-1); background: var(--si-hover-bg); }
.act.primary { border-color: var(--si-ink-1); color: var(--si-ink-1); font-weight: 600; }
.sum { margin: -6px 0 10px; font: var(--si-font-meta); color: var(--si-ink-3); }
.undo { margin: 0 0 8px; font: var(--si-font-meta); color: var(--si-ink-3); }
.link { margin-left: 8px; padding: 0; border: 0; background: transparent; font: inherit; color: var(--si-ink-1); text-decoration: underline; text-underline-offset: 3px; cursor: pointer; }
.group + .group { margin-top: 12px; }
h4 { display: flex; align-items: center; gap: 8px; margin: 0; padding: 8px 0; border-bottom: 1px solid var(--si-hair-strong); font: var(--si-font-label-sm); color: var(--si-ink-3); }
h4 .num { color: var(--si-ink-4); }
h4.veto, h4.strong { color: var(--si-danger-ink); }
h4.medium { color: var(--si-warn-ink); }
ul { margin: 0; padding: 0; list-style: none; }
li { display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: center; gap: 24px; padding: 9px 0; border-bottom: 1px solid var(--si-hair-faint); }
.what { display: flex; flex-direction: column; gap: 2px; min-width: 0; padding: 0; border: 0; background: transparent; text-align: left; cursor: pointer; }
.what .text { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font: var(--si-font-body); color: var(--si-ink-1); }
.what .meta { font: var(--si-font-meta); color: var(--si-ink-3); }
.what:hover .text { text-decoration: underline; text-decoration-color: var(--si-hair-strong); text-underline-offset: 3px; }
.acts { display: inline-flex; align-items: center; gap: 8px; }
.checks { display: inline-flex; gap: 12px; margin-right: 4px; }
.checks label { display: inline-flex; align-items: center; gap: 4px; font: var(--si-font-label-sm); color: var(--si-ink-2); cursor: pointer; white-space: nowrap; }
.checks input { accent-color: var(--si-ink-1); margin: 0; }
</style>
