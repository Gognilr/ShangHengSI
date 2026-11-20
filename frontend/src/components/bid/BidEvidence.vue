<script setup lang="ts">
import { computed, inject, ref, watch } from 'vue'
import { ChevronDown, ChevronRight, ChevronUp, Info } from '@lucide/vue'
import { ApiError } from '@/api/http'
import { bidApi, bidGroupLabel, type BidCellDetail, type BidConclusionEntry, type BidSignMark } from '@/api/bid'
import SimilarMap from './charts/SimilarMap.vue'
import BidPageShot from './BidPageShot.vue'
import { bidResultsKey, bidTypeLabel, entrySteps, sameSpans, type EvidenceStep } from './results'

// 证据栏（澄明 8.5 右栏）：单家的问题显示原件页图并框出命中处；两家之间的线索左右并排，相同处用琥珀色标出，右上角逐处翻看。
const props = defineProps<{ entry: BidConclusionEntry }>()
const ctx = inject(bidResultsKey)!
const data = computed(() => ctx.data.value!)
const at = ref(0), normalOpen = ref(false)
const cell = ref<BidCellDetail>(), cellError = ref('')
const kind = computed(() => props.entry.id.startsWith('cell:') ? 'cell' : props.entry.id.startsWith('sign:') ? 'sign' : 'item')
const kase = computed(() => props.entry.case_id ? data.value.conclusion.cases.find(c => c.id === props.entry.case_id) : undefined)
const item = computed(() => props.entry.item_id ? data.value.items.find(i => String(i.id) === String(props.entry.item_id)) : undefined)

// 条款事项：读条款原文、判断与这一家的对应片段
watch(() => props.entry.id, async id => {
  at.value = 0; normalOpen.value = false; cell.value = undefined; cellError.value = ''
  if (!id.startsWith('cell:')) return
  const [, rid, bid] = id.split(':')
  try { const d = await bidApi.cell(ctx.projectId, rid!, bid!); if (props.entry.id === id) cell.value = d }
  catch (e) { cellError.value = e instanceof ApiError ? e.message : '证据读取失败' }
}, { immediate: true })

const steps = computed<EvidenceStep[]>(() => {
  if (kind.value === 'sign') return (props.entry.places ?? []).slice(0, 1).map(p => ({ label: '签章页', sides: [{ bidderId: p.bidder_id, fileId: p.file_id, page: p.page, loc: p.loc }] }))
  if (kind.value === 'cell') {
    const bid = props.entry.bidder_ids[0]!
    return (cell.value?.places ?? []).map(p => ({
      label: '对应片段',
      sides: [{ bidderId: bid, fileId: p.file_id, page: p.page, text: `${p.before ? p.before + '\n' : ''}\u0000${p.text}\u0000${p.after ? '\n' + p.after : ''}`,
        loc: props.entry.places?.find(x => x.file_id === p.file_id && x.page === p.page)?.loc ?? null }],
    }))
  }
  return entrySteps(props.entry, data.value)
})
const step = computed(() => steps.value[Math.min(at.value, steps.value.length - 1)])
const title = computed(() => {
  if (kind.value === 'cell') return '条款与原文'
  if (kind.value === 'sign') return '签章页'
  if (kase.value || (step.value?.sides.length ?? 0) > 1) return '两家相同内容'
  return item.value ? bidTypeLabel[item.value.type] ?? '原件页' : '原件页'
})
const requirement = computed(() => props.entry.requirement_id ? data.value.requirements.find(r => String(r.id) === String(props.entry.requirement_id)) : undefined)
const basis = computed(() => {
  const a = props.entry.article
  if (!a) return ''
  return props.entry.category === '串通' || props.entry.category === '串标线索' ? `《招标投标法实施条例》第四十条${a}` : a.startsWith('第') ? `《招标投标法实施条例》${a}` : a
})
const normal = computed(() => kase.value?.normal ?? (item.value?.normal ? [item.value.normal] : []))
const sign = computed(() => {
  const p = props.entry.places?.[0]
  return p ? data.value.signing.find(s => s.file_id === p.file_id && (s.page ?? null) === (p.page ?? null)) : undefined
})
const marks: { value: BidSignMark; label: string }[] = [{ value: 'stamped', label: '已盖章' }, { value: 'missing', label: '缺章' }, { value: 'unclear', label: '看不清' }]
function where(fileId: string | null, page: number | null) { return (fileId ? ctx.fileNames.value[fileId] ?? '文件' : '文件') + (page ? ` · 第 ${page} 页` : '') }
function canShow(fileId: string | null, page: number | null) { return !!fileId && !!page && !!ctx.renderable.value[fileId] }
const verdictLabel: Record<string, string> = { yes: '响应', no: '疑似不响应', missing: '本机未找到', unclear: '无法判断', manual: '人工核对' }
/** 条款片段：\0 包住命中的那一段 */
function hitParts(text: string) { return text.split('\u0000').map((t, i) => [t, i === 1] as [string, boolean]) }
</script>

<template>
  <section class="ev si-card" aria-label="证据">
    <header class="ev-h">
      <b>证据 · {{ title }}</b>
      <template v-if="steps.length > 1">
        <span class="count num">第 {{ at + 1 }} / {{ steps.length }} 处</span>
        <span class="nav">
          <button type="button" class="ibtn" :disabled="at <= 0" aria-label="上一处" @click="at--"><ChevronUp :size="16" :stroke-width="1.75" /></button>
          <button type="button" class="ibtn" :disabled="at >= steps.length - 1" aria-label="下一处" @click="at++"><ChevronDown :size="16" :stroke-width="1.75" /></button>
        </span>
      </template>
    </header>

    <!-- 事项本身与依据 -->
    <div class="what">
      <p class="what-t" :class="entry.level">{{ entry.title }}</p>
      <p v-if="requirement" class="basis"><span class="k">招标文件</span>{{ bidGroupLabel[requirement.group] }} 第 {{ requirement.seq }} 条：{{ requirement.text }}</p>
      <p v-if="basis" class="basis"><span class="k">依据</span>{{ basis }}</p>
    </div>

    <!-- 条款事项：判断与理由 -->
    <template v-if="kind === 'cell'">
      <p v-if="cellError" class="err" role="alert">{{ cellError }}</p>
      <div v-else-if="cell?.cell" class="verdict">
        <span class="si-badge" :class="cell.cell.source === 'model' ? 'is-ai' : 'is-brand'">{{ cell.cell.source === 'model' ? '模型补查 · 请核实' : '本机计算' }}</span>
        <b>{{ verdictLabel[cell.cell.verdict] ?? cell.cell.verdict }}</b>
        <span v-if="cell.cell.cite === 'unverified'" class="warn">引文未能在片段中核实，请对照原件</span>
        <p v-if="cell.cell.reason" class="reason">{{ cell.cell.reason }}</p>
      </div>
      <p v-else-if="cell && !cell.places.length" class="empty">这一家的文件中没有找到对应片段，请到原件中核对。</p>
      <p v-else-if="!cell" class="empty" role="status">正在读取…</p>
    </template>

    <template v-if="step">
      <p v-if="step.label && kind === 'item'" class="fact">{{ step.label }}</p>
      <p v-if="step.value" class="same"><span class="k">{{ step.sides.length > 1 || step.pair ? '相同的内容' : '原文' }}</span><mark>{{ step.value }}</mark></p>

      <!-- 两段原文并排：相同处高亮 -->
      <div v-if="step.pair" class="pair">
        <article v-for="(s, i) in step.sides" :key="i" class="paper">
          <div class="paper-h"><span class="code">{{ ctx.codes.value[s.bidderId] }}</span><span class="nm" :title="ctx.names.value[s.bidderId]">{{ ctx.shorts.value[s.bidderId] }}</span><span class="wh">{{ where(s.fileId, s.page) }}</span>
            <button v-if="canShow(s.fileId, s.page)" type="button" class="link" @click="ctx.openPage(s.fileId!, s.page!, s.loc)">原件页</button></div>
          <p class="paper-b"><template v-for="([t, same], k) in sameSpans(s.text ?? '', step.sides[1 - i]?.text ?? '')" :key="k"><mark v-if="same">{{ t }}</mark><template v-else>{{ t }}</template></template></p>
        </article>
      </div>

      <!-- 原件页：一家一列，两家并排 -->
      <div v-else-if="step.sides.length" class="cols" :class="{ two: step.sides.length > 1 }">
        <article v-for="(s, i) in step.sides" :key="i" class="col">
          <div class="paper-h"><span class="code">{{ ctx.codes.value[s.bidderId] }}</span><span class="nm" :title="ctx.names.value[s.bidderId]">{{ ctx.shorts.value[s.bidderId] }}</span><span class="wh">{{ where(s.fileId, s.page) }}</span></div>
          <BidPageShot v-if="canShow(s.fileId, s.page)" :project-id="ctx.projectId" :file-id="s.fileId!" :page="s.page!" :loc="s.loc" :alt="where(s.fileId, s.page)" @zoom="ctx.openPage(s.fileId!, s.page!, s.loc)" />
          <p v-if="s.text" class="paper-b sheet"><template v-for="([t, hit], k) in hitParts(s.text)" :key="k"><mark v-if="hit">{{ t }}</mark><span v-else class="dim">{{ t }}</span></template></p>
          <p v-else-if="s.excerpt" class="paper-b sheet" :class="{ small: canShow(s.fileId, s.page) }">{{ s.excerpt }}</p>
          <p v-else-if="!canShow(s.fileId, s.page)" class="empty">这份文件不能显示页面图，请到原件中查看。</p>
        </article>
      </div>

      <table v-if="step.prices" class="prices">
        <thead><tr><th>分项</th><th v-for="s in step.sides.slice(0, 2)" :key="s.bidderId" class="r">{{ ctx.shorts.value[s.bidderId] }}</th></tr></thead>
        <tbody><tr v-for="(p, i) in step.prices" :key="i"><td>{{ p.name }}</td><td class="r num">{{ p.left }}</td><td class="r num">{{ p.right }}</td></tr></tbody>
      </table>
      <SimilarMap v-if="step.similar?.map?.segs.length" class="map" :map="step.similar.map" :a="step.similar.bidder_ids[0]!" :b="step.similar.bidder_ids[1]!" :labels="ctx.shorts.value" :file-names="ctx.fileNames.value" />
    </template>
    <p v-else-if="kind === 'item'" class="empty">这一条没有可显示的原件位置。</p>

    <!-- 签章页：在这里改标注 -->
    <div v-if="kind === 'sign' && sign" class="marks" role="radiogroup" :aria-label="`${sign.title} 的签章`">
      <span class="k">核对结果</span>
      <button v-for="m in marks" :key="m.value" type="button" role="radio" :aria-checked="sign.mark === m.value" :class="[m.value, { on: sign.mark === m.value }]" @click="ctx.markSigning([sign], sign.mark === m.value ? null : m.value)">{{ m.label }}</button>
    </div>

    <p v-if="kase && (kase.baseline.sentences || kase.baseline.images)" class="note"><Info :size="14" :stroke-width="1.75" />已剔除与招标文件相同的句子 {{ kase.baseline.sentences ?? 0 }} 句、图片 {{ kase.baseline.images ?? 0 }} 张。相同处用琥珀色标出。</p>
    <template v-if="normal.length">
      <button type="button" class="fold" :aria-expanded="normalOpen" @click="normalOpen = !normalOpen"><ChevronRight :size="14" :stroke-width="1.75" :class="{ turned: normalOpen }" />常见的正常原因</button>
      <ul v-if="normalOpen" class="normal"><li v-for="r in normal" :key="r">{{ r }}</li></ul>
    </template>
  </section>
</template>

<style scoped>
.ev { padding: 16px 18px 18px; min-width: 0; }
.ev-h { display: flex; align-items: center; gap: 12px; margin-bottom: 12px; }
.ev-h b { font: var(--si-font-h3); color: var(--si-ink-1); }
.count { font: var(--si-font-meta); color: var(--si-ink-3); }
.nav { display: inline-flex; gap: 4px; margin-left: auto; }
.ibtn { display: grid; place-items: center; width: 30px; height: 30px; padding: 0; border: 1px solid var(--si-hair); border-radius: 8px; background: var(--si-surface-1); color: var(--si-ink-2); cursor: pointer; transition: background-color var(--si-d2) var(--si-ease-out); }
.ibtn:hover:not(:disabled) { background: var(--si-hover-bg); color: var(--si-ink-1); }
.ibtn:active:not(:disabled) { transform: scale(.97); }
.ibtn:disabled { color: var(--si-ink-5); cursor: default; }
.what { display: flex; flex-direction: column; gap: 6px; padding: 12px 14px; margin-bottom: 14px; border-radius: 12px; background: var(--si-card-2); border: 1px solid var(--si-hair-faint); }
.what-t { margin: 0; font: var(--si-font-body); font-weight: 600; color: var(--si-ink-1); overflow-wrap: anywhere; }
.what-t.severe { color: var(--si-danger-ink); }
.basis { margin: 0; font: var(--si-font-meta); color: var(--si-ink-2); overflow-wrap: anywhere; white-space: pre-wrap; }
.k { display: inline-block; margin-right: 8px; font: var(--si-font-badge); color: var(--si-ink-4); }
.verdict { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 10px; margin-bottom: 14px; font: var(--si-font-body); color: var(--si-ink-1); }
.reason { flex-basis: 100%; margin: 0; font: var(--si-font-meta); color: var(--si-ink-2); white-space: pre-wrap; }
.warn { font: var(--si-font-meta); color: var(--si-warn-ink); }
.fact { margin: 0 0 8px; font: var(--si-font-label); color: var(--si-ink-2); }
.same { margin: 0 0 10px; font: var(--si-font-body); color: var(--si-ink-1); overflow-wrap: anywhere; }
mark { background: color-mix(in srgb, var(--si-warn) 26%, transparent); color: inherit; border-radius: 2px; box-shadow: inset 0 -2px 0 var(--si-warn); }
.pair, .cols { display: grid; grid-template-columns: minmax(0, 1fr); gap: 12px; padding: 12px; border-radius: 12px; background: var(--si-sunken); }
.pair, .cols.two { grid-template-columns: repeat(2, minmax(0, 1fr)); }
.paper { background: var(--si-paper); border-radius: 6px; box-shadow: var(--si-paper-shadow); overflow: hidden; min-width: 0; }
.col { display: flex; flex-direction: column; gap: 8px; min-width: 0; }
.paper-h { display: flex; align-items: center; gap: 8px; min-width: 0; padding: 8px 12px; font: var(--si-font-caption); color: var(--si-ink-3); }
.paper .paper-h { border-bottom: 1px solid var(--si-hair-faint); }
.col .paper-h { padding: 0 2px; }
.code { flex: none; display: grid; place-items: center; width: 22px; height: 22px; border-radius: 6px; background: var(--si-m-bid-soft); color: var(--si-m-bid); font: var(--si-font-code-sm); font-weight: 600; }
.nm { flex: none; font: var(--si-font-label-sm); color: var(--si-ink-1); }
.wh { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.link { flex: none; margin-left: auto; padding: 0; border: 0; background: none; font: var(--si-font-caption); color: var(--si-blue-ink); cursor: pointer; }
.link:hover { text-decoration: underline; }
.paper-b { margin: 0; padding: 14px 16px; font: var(--si-font-body); line-height: 1.85; color: var(--si-paper-ink); white-space: pre-wrap; overflow-wrap: anywhere; max-height: 420px; overflow: auto; }
.sheet { background: var(--si-paper); border-radius: 6px; box-shadow: var(--si-paper-shadow); }
.sheet.small { padding: 10px 12px; font: var(--si-font-meta); color: var(--si-paper-ink); }
.dim { color: var(--si-paper-ink-2); }
.empty { margin: 8px 0; font: var(--si-font-meta); color: var(--si-ink-3); }
.err { margin: 0 0 12px; font: var(--si-font-meta); color: var(--si-danger-ink); }
.prices { width: 100%; margin-top: 12px; border-collapse: collapse; font: var(--si-font-meta); color: var(--si-ink-2); }
.prices th, .prices td { padding: 6px 12px 6px 0; border-bottom: 1px solid var(--si-hair-faint); text-align: left; font-weight: 400; }
.prices th { color: var(--si-ink-3); }
.prices .r { text-align: right; }
.map { margin-top: 14px; }
.marks { display: flex; align-items: center; gap: 0; margin-top: 14px; }
.marks .k { margin-right: 12px; }
.marks button { padding: 5px 12px; border: 1px solid var(--si-hair-strong); background: var(--si-surface-1); font: var(--si-font-label-sm); color: var(--si-ink-3); cursor: pointer; }
.marks button + button { border-left: 0; }
.marks button:nth-of-type(1) { border-radius: 8px 0 0 8px; }
.marks button:last-child { border-radius: 0 8px 8px 0; }
.marks button.on { color: var(--si-ink-1); font-weight: 600; background: var(--si-surface-2); }
.marks button.missing.on { color: var(--si-danger-ink); background: var(--si-danger-soft); }
.marks button.unclear.on { color: var(--si-warn-ink); background: var(--si-warn-soft); }
.note { display: flex; align-items: center; gap: 6px; margin: 12px 0 0; font: var(--si-font-caption); color: var(--si-ink-3); }
.fold { display: inline-flex; align-items: center; gap: 4px; margin-top: 10px; padding: 0; border: 0; background: none; font: var(--si-font-meta); color: var(--si-ink-3); cursor: pointer; }
.fold:hover { color: var(--si-ink-1); }
.fold svg { transition: transform var(--si-d2) var(--si-ease-out); }
.fold .turned { transform: rotate(90deg); }
.normal { margin: 6px 0 0; padding-left: 18px; font: var(--si-font-meta); color: var(--si-ink-2); }
</style>
