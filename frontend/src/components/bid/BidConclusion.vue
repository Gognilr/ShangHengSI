<script setup lang="ts">
import { computed, inject, nextTick, onBeforeUnmount, ref } from 'vue'
import { bidApi, type BidConclusionEntry, type BidConclusionReview } from '@/api/bid'
import { formatDateTime } from '@/utils/time'
import BidCaseCard from './BidCaseCard.vue'
import BidCellDetail from './BidCellDetail.vue'
import BidItemCard from './BidItemCard.vue'
import BidLede from './BidLede.vue'
import BidReviewReason from './BidReviewReason.vue'
import { bidResultsKey, useBidReviewKeys } from './results'

const emit = defineEmits<{ price: [id: string] }>()
const ctx = inject(bidResultsKey)!
const data = computed(() => ctx.data.value!.conclusion)
const items = computed(() => new Map(ctx.data.value!.items.map(i => [i.id, i])))
const open = ref<string | null>(null), selected = ref<string>(), reasonAt = ref<string>()
const highlight = ref(''), busy = ref(false), error = ref(''), failed = ref('')
const completed = ref<Record<string, { entry: BidConclusionEntry; text: string }>>({})
const linkage = ref<{ key: string; count: number }>()
const timers = new Map<string, ReturnType<typeof setTimeout>>()
const checks = computed(() => {
  const rows = [...data.value.verify]
  for (const [key, value] of Object.entries(completed.value)) {
    if (rows.some(r => r.id === key)) continue
    const index = originalPositions.get(key) ?? rows.length
    rows.splice(index, 0, value.entry)
  }
  return rows
})
const originalPositions = new Map<string, number>()
const done = computed(() => Object.entries(data.value.reviews).filter(([key]) => !completed.value[key]))
const references = computed(() => {
  const groups = new Map<string, BidConclusionEntry[]>()
  for (const e of data.value.reference) {
    if (!groups.has(e.category)) groups.set(e.category, [])
    groups.get(e.category)!.push(e)
  }
  return groups
})
const active = computed(() => data.value.verify.find(e => e.id === selected.value) ?? data.value.verify[0])
const verdict = { certain: '建议否决', verify: '需核实', reference: '参考' }
const names = (entry: BidConclusionEntry) => entry.bidder_ids.map(id => data.value.bidders.find(b => b.id === id)?.short).join('、')
const fact = (entry: BidConclusionEntry) => entry.case_id ? entry.title : `${names(entry)}：${entry.title}`
const evidence = (entry: BidConclusionEntry) => entry.item_id ? items.value.get(entry.item_id) : undefined
function toggle(key: string) { open.value = open.value === key ? null : key }
function clearTimer(key: string) { clearTimeout(timers.get(key)); timers.delete(key) }
onBeforeUnmount(() => timers.forEach(t => clearTimeout(t)))
async function locate(key: string) {
  if (!key.startsWith('case:')) open.value = key
  selected.value = key
  await nextTick()
  document.getElementById('bid-result-' + key)?.scrollIntoView({ block: 'nearest' })
  highlight.value = key
  clearTimer('highlight')
  timers.set('highlight', setTimeout(() => { highlight.value = '' }, 1200))
}
function reviewedText(status: string, reason?: string | null, detail?: string | null) {
  return status === 'verified' ? '已核实 · 属实' : `已核实 · 不属实（${detail || reason}）`
}
async function decide(patch: BidConclusionReview) {
  if (busy.value) return
  busy.value = true; error.value = ''; failed.value = ''; linkage.value = undefined
  const key = patch.key
  const entry = data.value.verify.find(e => e.id === key)
  if (entry && patch.status !== 'pending') {
    originalPositions.set(key, data.value.verify.findIndex(e => e.id === key))
    completed.value[key] = { entry, text: patch.status === 'verified' ? '已核实 · 属实' : '已核实 · 不属实' }
  }
  try {
    ctx.data.value = await bidApi.conclusionReview(ctx.projectId, patch)
    const saved = data.value.reviews[key]
    if (saved && completed.value[key]) {
      completed.value[key]!.text = reviewedText(saved.status, saved.reason_label, saved.detail)
      clearTimer(key)
      timers.set(key, setTimeout(() => { delete completed.value[key]; originalPositions.delete(key) }, 3000))
    } else {
      clearTimer(key)
      delete completed.value[key]
      originalPositions.delete(key)
    }
    if (saved?.linked.length) linkage.value = { key, count: saved.linked.length }
  } catch {
    delete completed.value[key]
    originalPositions.delete(key)
    failed.value = key
  } finally { busy.value = false; reasonAt.value = undefined }
}
async function sign(signed: boolean) {
  busy.value = true; error.value = ''; linkage.value = undefined
  try { ctx.data.value = await bidApi.conclusionSign(ctx.projectId, signed) }
  catch { error.value = '保存失败，请重试' }
  finally { busy.value = false }
}
useBidReviewKeys({
  active: () => !!active.value && !busy.value,
  move: direction => {
    const list = data.value.verify
    const at = list.findIndex(e => e.id === active.value?.id)
    const entry = list[Math.max(0, Math.min(list.length - 1, at + direction))]
    selected.value = entry?.id
    if (entry) document.getElementById('bid-verification-' + entry.id)?.focus()
  },
  yes: () => { if (active.value) void decide({ key: active.value.id, status: 'verified' }) },
  no: () => { reasonAt.value = active.value?.id },
  enter: () => { if (active.value) toggle(active.value.id) },
  escape: () => { open.value = null; reasonAt.value = undefined },
})
defineExpose({ locate })
</script>

<template>
  <div class="bid-tab conclusion">
    <BidLede :text="data.lede" :sub="data.sub ?? undefined" />
    <table class="bid-table decision-table">
      <colgroup><col class="status-col"><col class="name-col"><col><col class="price-col"></colgroup>
      <thead><tr><th>结论</th><th>投标人</th><th>理由</th><th class="r">投标总价</th></tr></thead>
      <tbody>
        <tr v-for="b in data.bidders" :key="b.id">
          <td><span v-if="b.tier" class="bid-tier" :class="b.tier">{{ verdict[b.tier] }}</span><span v-else class="pass">✓ 未发现问题</span></td>
          <td class="name" :title="b.name">{{ b.name }}</td>
          <td><template v-for="(r, i) in b.reasons.slice(0, 3)" :key="r.id"><span v-if="i">；</span><button class="bid-link" type="button" @click="locate(r.id)">{{ r.title }}</button></template><template v-if="b.reasons.length > 3">等 {{ b.reasons.length }} 项</template></td>
          <td class="r num"><button type="button" class="bid-link" :class="{ 'bid-empty': !b.total }" @click="emit('price', b.id)">{{ b.total ?? '未识别' }}</button><small v-if="b.manual_total">唱标价 · 人工录入</small></td>
        </tr>
      </tbody>
    </table>

    <section v-if="data.cases.length" aria-labelledby="bid-cases">
      <h3 id="bid-cases" class="bid-title">疑似串通投标<span class="n">{{ data.cases.length }}</span></h3>
      <div class="cases">
        <div v-for="c in data.cases" :id="'bid-result-' + c.id" :key="c.id" :class="{ 'bid-target': highlight === c.id }">
          <BidCaseCard :item="c" :busy="busy" @decide="decide" />
          <p v-if="failed === c.id" class="save-error" role="alert">保存失败，请重试</p>
        </div>
      </div>
    </section>

    <section id="bid-verify" aria-labelledby="bid-verify-title">
      <h3 id="bid-verify-title" class="bid-title">需你核实<span class="n">{{ data.verify.length }}</span><span v-if="data.verify.length > 10" class="end">超过 10 条，已按严重程度排序</span></h3>
      <p v-if="!checks.length" class="bid-empty">没有需要核实的事项。</p>
      <ol class="bid-fine">
        <li v-for="(e, i) in checks" :id="'bid-verification-' + e.id" :key="e.id" class="check-row" tabindex="-1" @focus="selected = e.id">
          <div :id="e.case_id ? undefined : 'bid-result-' + e.id" class="check-line" :class="{ 'bid-target': highlight === e.id && !e.case_id }">
            <span class="num">{{ i + 1 }}</span>
            <span v-if="completed[e.id]" class="pass result-text">✓ {{ completed[e.id]?.text }}</span>
            <template v-else>
              <span class="bid-tier verify">{{ fact(e) }}</span>
              <span class="bid-meta">{{ e.article && e.case_id ? '第四十条' + e.article : e.article }}</span>
              <div class="row-actions">
                <button type="button" class="bid-link" :aria-expanded="open === e.id" @click="toggle(e.id)">查看 ›</button>
                <el-button size="small" :disabled="busy" @click="decide({ key: e.id, status: 'verified' })">Y</el-button>
                <BidReviewReason label="N" :disabled="busy" :triggered="reasonAt === e.id" @closed="reasonAt = undefined" @decide="(reason, detail, remember) => decide({ key: e.id, status: 'excluded', reason, detail, remember })" />
              </div>
            </template>
          </div>
          <p v-if="failed === e.id" class="save-error" role="alert">保存失败，请重试</p>
          <p v-if="linkage?.key === e.id" class="bid-meta">同一来源的 {{ linkage.count }} 条已一并移入参考事项 <button type="button" class="bid-link" @click="decide({ key: e.id, status: 'pending' })">撤销</button></p>
          <template v-if="open === e.id">
            <button v-if="e.case_id" class="bid-link" type="button" @click="locate(e.id)">查看案件证据链 ›</button>
            <BidCellDetail v-else-if="e.requirement_id" :requirement-id="e.requirement_id" :bidder-id="e.bidder_ids[0]!" no-bar />
            <BidItemCard v-else-if="evidence(e)" :item="evidence(e)!" bare />
          </template>
        </li>
      </ol>
      <p v-if="linkage && !completed[linkage.key]" class="bid-meta">同一来源的 {{ linkage.count }} 条已一并移入参考事项 <button type="button" class="bid-link" @click="decide({ key: linkage.key, status: 'pending' })">撤销</button></p>
    </section>

    <section v-if="data.certain.length">
      <div class="sign-row bid-meta">
        <span v-if="data.signed" class="pass">✓ 已签认（{{ formatDateTime(data.signed) }}）</span>
        <span v-else>确凿项 {{ data.certain.length }} 条已由本机证据确定。评标委员会阅后签认即可，有异议可在对应条目点“有异议”。</span>
        <el-button size="small" :text="!!data.signed" :disabled="busy" @click="sign(!data.signed)">{{ data.signed ? '撤销' : '签认确凿项' }}</el-button>
      </div>
      <p v-if="error" class="save-error" role="alert">{{ error }}</p>
      <ul class="bid-fine">
        <li v-for="e in data.certain.filter(r => !r.case_id)" :id="'bid-result-' + e.id" :key="e.id" class="check-row" :class="{ 'bid-target': highlight === e.id }">
          <div class="certain-line">
            <button class="bid-link bid-tier certain" type="button" :aria-expanded="open === e.id" @click="toggle(e.id)">{{ fact(e) }}</button>
            <BidReviewReason label="有异议" :disabled="busy" @decide="(reason, detail, remember) => decide({ key: e.id, status: 'excluded', reason, detail, remember })" />
          </div>
          <p v-if="failed === e.id" class="save-error" role="alert">保存失败，请重试</p>
          <template v-if="open === e.id">
            <BidCellDetail v-if="e.requirement_id" :requirement-id="e.requirement_id" :bidder-id="e.bidder_ids[0]!" no-bar />
            <BidItemCard v-else-if="evidence(e)" :item="evidence(e)!" bare />
          </template>
        </li>
      </ul>
    </section>

    <section>
      <details>
        <summary class="bid-fold">参考事项 {{ data.reference.length }} 条（不影响结论）</summary>
        <div v-for="[category, rows] in references" :key="category">
          <h4>{{ category }} · {{ rows.length }}</h4>
          <ul class="bid-fine"><li v-for="r in rows" :key="r.id">{{ fact(r) }}<span v-if="evidence(r)?.normal"> · {{ evidence(r)?.normal }}</span></li></ul>
        </div>
      </details>
      <details v-if="done.length">
        <summary class="bid-fold">已核实 {{ done.length }} 条</summary>
        <ul class="bid-fine">
          <li v-for="[key, v] in done" :key="key">
            {{ fact(v.entry) }} · {{ reviewedText(v.status, v.reason_label, v.detail) }}
            <button type="button" class="bid-link" :disabled="busy" @click="decide({ key, status: 'pending' })">撤销</button>
            <p v-if="failed === key" class="save-error" role="alert">保存失败，请重试</p>
          </li>
        </ul>
      </details>
    </section>

    <section v-if="data.site.length">
      <details>
        <summary class="bid-meta">需在现场核验原件的要求 {{ data.site.length }} 条，已写入导出报告的“现场核验清单”。 <span class="bid-link">查看</span></summary>
        <ul class="bid-fine"><li v-for="r in data.site" :key="r.id">{{ r.seq }}. {{ r.text }}</li></ul>
      </details>
    </section>

    <footer class="bid-foot">
      <span>系统只给出建议；否决与串通投标的认定由评标委员会作出。</span>
      <span v-if="data.second_calls">二次核验 {{ data.second_calls }} 次，均经本机脱敏与出口复检。</span>
    </footer>
  </div>
</template>

<style scoped>
.decision-table { table-layout: fixed; }
.decision-table td { padding-block: 4px; vertical-align: middle; }
.decision-table th { padding-block: 2px 4px; }
.status-col { width: 112px; }
.name-col { width: 24%; }
.price-col { width: 20%; }
.decision-table .name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.decision-table button { text-align: inherit; overflow-wrap: anywhere; }
.cases { display: grid; gap: 8px; }
.check-row { scroll-margin-block: 12px; }
.check-line { display: grid; grid-template-columns: 24px minmax(0, 1fr) minmax(0, auto) auto; gap: 8px; align-items: baseline; }
.check-line .num { color: var(--si-ink-3); }
.row-actions, .sign-row, .certain-line { display: flex; align-items: baseline; gap: 10px; }
.certain-line button:first-child { text-align: left; }
.sign-row, .certain-line { justify-content: space-between; }
.pass { color: var(--si-ink-2); }
.result-text { grid-column: 2 / -1; }
.save-error { margin: 4px 0; color: var(--si-danger-ink); font: var(--si-font-meta); }
details summary { cursor: pointer; display: list-item; }
h4 { margin: 10px 0 3px; font: var(--si-font-h4); }
@media (max-width: 1100px) {
  .decision-table th, .decision-table td { padding-inline-end: 8px; }
  .check-line { grid-template-columns: 20px minmax(0, 1fr) auto; }
  .check-line > .bid-meta { grid-column: 2; grid-row: 2; }
}
</style>
