<script setup lang="ts">
import { computed, inject, ref } from 'vue'
import { bidGroupLabel, bidMethodLabel, type Bidder, type BidCell, type BidGroup } from '@/api/bid'
import BidCellDrawer from './BidCellDrawer.vue'
import { bidResultsKey } from './results'

// 要求响应矩阵（bid.md 2.4 第 2 项）：行为要求（否决与 ★ 在前），列为投标人，首列固定。
// 符号 ✓ ✕ ？ —：本地规则为实心、模型判断为空心，不用底纹染色（visual.md 7.5）。
const props = defineProps<{ bidders: Bidder[]; modelState: string | null }>()
const ctx = inject(bidResultsKey)!
const onlyProblems = ref(true)
const order: BidGroup[] = ['veto', 'substantive', 'qualification', 'pricing', 'validity', 'signing']
// 只缓存位置；取格子时经响应式数组读取，核对状态改动即时反映
const cellIndex = computed(() => new Map((ctx.data.value?.cells ?? []).map((c, i) => [`${c.requirement_id}:${c.bidder_id}`, i])))
const cellOf = (rid: string, bid: string) => { const i = cellIndex.value.get(`${rid}:${bid}`); return i === undefined ? undefined : ctx.data.value?.cells[i] }
const problem = (c?: BidCell) => !!c && c.review_status !== 'excluded' && (c.verdict === 'no' || c.verdict === 'missing' || c.verdict === 'unclear' || c.cite === 'unverified')
const all = computed(() => (ctx.data.value?.requirements ?? []).filter(r => order.includes(r.group))
  .sort((a, b) => order.indexOf(a.group) - order.indexOf(b.group) || a.seq - b.seq))
const rows = computed(() => all.value.filter(r => !onlyProblems.value || props.bidders.some(b => problem(cellOf(r.id, b.id)))))
const hidden = computed(() => all.value.length - rows.value.length)
const problemRows = computed(() => all.value.filter(r => props.bidders.some(b => problem(cellOf(r.id, b.id)))).length)
const manualRows = computed(() => all.value.filter(r => r.method === 'manual').length)

const symbols: Record<string, { s: string; label: string; tone: string }> = {
  yes: { s: '✓', label: '响应', tone: 'ok' }, no: { s: '✕', label: '疑似不响应', tone: 'bad' },
  missing: { s: '？', label: '未找到', tone: 'warn' }, unclear: { s: '？', label: '无法判断', tone: 'warn' },
}
function view(method: string, c?: BidCell) {
  if (method === 'manual' || c?.verdict === 'manual') return { s: '—', label: '人工核对', tone: 'manual', hollow: false }
  if (!c) return { s: '·', label: method === 'model' ? (props.modelState === 'running' ? '模型核对中' : '未核对') : '本地规则未能判断', tone: 'none', hollow: true }
  const v = symbols[c.verdict] ?? symbols.unclear!
  return { ...v, hollow: c.source === 'model' }
}
function title(method: string, c?: BidCell) {
  const v = view(method, c)
  const parts = [v.label, c ? (c.source === 'model' ? '模型判断' : '本地规则') : bidMethodLabel[method as 'local']]
  if (c?.cite === 'unverified') parts.push('引文待核实')
  if (c && c.review_status !== 'pending') parts.push(c.review_status === 'verified' ? '已核实' : '已排除')
  return parts.join(' · ')
}
const drawer = ref<{ rid: string; bid: string } | null>(null)
</script>

<template>
  <div class="bid-tab">
    <p class="bid-lede"><template v-if="problemRows">{{ all.length }} 条要求中，<span class="d">{{ problemRows }} 条有</span>疑似不响应、未找到或无法判断的格</template><template v-else-if="all.length">{{ all.length }} 条要求中没有疑似不响应、未找到或无法判断的格</template><template v-else>要求清单中没有可逐家判断的要求</template>{{ manualRows ? `；另有 ${manualRows} 条需人工核对` : '' }}。</p>
    <section class="bid-block">
      <div class="tools">
        <label class="toggle"><el-switch v-model="onlyProblems" size="small" />只看有问题的行</label>
        <span v-if="onlyProblems && hidden" class="hint num">已隐藏 {{ hidden }} 行</span>
      </div>
      <div v-if="rows.length" class="scroll">
        <table>
          <thead><tr><th class="req">要求</th><th v-for="b in bidders" :key="b.id" class="col" :title="b.name">{{ ctx.shorts.value[b.id] }}</th></tr></thead>
          <tbody>
            <tr v-for="r in rows" :key="r.id">
              <th class="req" scope="row"><span class="group">{{ bidGroupLabel[r.group] }} · {{ bidMethodLabel[r.method] }}</span><span class="text">{{ r.text }}</span></th>
              <td v-for="b in bidders" :key="b.id">
                <button type="button" class="cell" :class="{ excluded: cellOf(r.id, b.id)?.review_status === 'excluded' }" :title="title(r.method, cellOf(r.id, b.id))" :aria-label="`${b.name}：${title(r.method, cellOf(r.id, b.id))}`" @click="drawer = { rid: r.id, bid: b.id }">
                  <i class="sym" :class="[view(r.method, cellOf(r.id, b.id)).tone, view(r.method, cellOf(r.id, b.id)).hollow ? 'hollow' : 'solid']">{{ view(r.method, cellOf(r.id, b.id)).s }}</i>
                  <sup v-if="cellOf(r.id, b.id)?.cite === 'unverified'" class="mark">待核实</sup>
                  <sup v-else-if="cellOf(r.id, b.id)?.review_status === 'verified'" class="mark">已核实</sup>
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-else class="bid-empty">{{ all.length ? '关闭“只看有问题的行”可查看全部要求。' : '要求清单中没有可逐家判断的要求。' }}</p>
      <p v-if="rows.length" class="legend"><span><i class="sym solid ok">✓</i>本地规则</span><span><i class="sym hollow ok">✓</i>模型判断</span><span>✓ 响应　✕ 疑似不响应　？ 未找到或无法判断　— 人工核对</span><span>点格子看依据</span></p>
    </section>
    <BidCellDrawer v-if="drawer" :requirement-id="drawer.rid" :bidder-id="drawer.bid" @close="drawer = null" />
  </div>
</template>

<style scoped>
.tools { display: flex; align-items: center; gap: 16px; margin-bottom: 12px; }
.toggle { display: inline-flex; align-items: center; gap: 8px; font: var(--si-font-label); color: var(--si-ink-2); cursor: pointer; }
.hint { margin: 0; font: var(--si-font-meta); color: var(--si-ink-4); }
.legend { display: flex; flex-wrap: wrap; gap: 6px 18px; margin: 12px 0 0; font: var(--si-font-caption); color: var(--si-ink-4); }
.legend > span { display: inline-flex; align-items: center; gap: 6px; }
.legend .sym { width: 16px; height: 16px; font-size: 10px; }
.scroll { overflow-x: auto; border-top: 1px solid var(--si-hair-strong); }
table { border-collapse: collapse; min-width: 100%; font: var(--si-font-body); }
th, td { border-bottom: 1px solid var(--si-hair-faint); }
thead th { padding: 10px 8px; font: var(--si-font-label-sm); color: var(--si-ink-3); text-align: center; background: var(--si-bg-canvas); }
th.req { position: sticky; left: 0; z-index: 1; min-width: 320px; max-width: 460px; padding: 10px 16px 10px 0; text-align: left; font-weight: normal; background: var(--si-bg-canvas); }
thead th.req { z-index: 2; }
.col { min-width: 112px; max-width: 160px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.group { display: block; font: var(--si-font-meta); color: var(--si-ink-4); }
.text { display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; color: var(--si-ink-1); }
td { padding: 0; text-align: center; }
.cell { position: relative; width: 100%; min-height: 52px; border: 0; background: transparent; cursor: pointer; }
.cell:hover { background: var(--si-hover-bg); }
.cell.excluded { opacity: .4; }
.sym { display: inline-grid; place-items: center; width: 22px; height: 22px; border-radius: 50%; font: 600 13px/1 var(--si-font-sans); font-style: normal; box-sizing: border-box; }
.sym.solid.ok { background: var(--si-ink-2); color: var(--si-surface-1); }
.sym.solid.bad { background: var(--si-danger); color: var(--si-white); }
.sym.solid.warn { background: var(--si-warn); color: var(--si-white); }
.sym.hollow { border: 1.5px solid currentColor; background: transparent; }
.sym.hollow.ok { color: var(--si-ink-2); }
.sym.hollow.bad { color: var(--si-danger-ink); }
.sym.hollow.warn { color: var(--si-warn-ink); }
.sym.manual { color: var(--si-ink-3); }
.sym.none { color: var(--si-ink-4); border-style: dashed; }
.mark { position: absolute; top: 6px; right: 6px; font: var(--si-font-caption); color: var(--si-ink-3); }
</style>
