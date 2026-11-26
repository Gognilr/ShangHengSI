<script setup lang="ts">
import SiNotice from '@/components/ui/SiNotice.vue'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Calculator, ChevronDown, ChevronRight, ListChecks, Sparkle } from '@lucide/vue'
import { ApiError } from '@/api/http'
import type { LocalFinding } from '@/api/localFindings'
import { runsApi, type Finding, type HandlingStatus, type RunDetail } from '@/api/runs'
import EmptyState from '@/components/EmptyState.vue'
import FindingCard from '@/components/FindingCard.vue'
import LocalCheckList from '@/components/result/LocalCheckList.vue'
import RailSummary from '@/components/result/RailSummary.vue'
import RequirementList from '@/components/result/RequirementList.vue'
import SiSegmented from '@/components/ui/SiSegmented.vue'
import ZoneHeader from '@/components/ui/ZoneHeader.vue'
import { CARRY_LABELS, CATEGORY_OPTIONS, HANDLING_OPTIONS, toOptions } from '@/utils/labels'
import { motionMs, useFx } from '@/utils/motion'

/**
 * 问题栏（V31-U3 3.4～3.6，澄明 8.4 ③）：顶部页签“意见 / 追问”与模型徽标；等级分段控件带计数；
 * “本机计算 · 结果确定”在上、可折叠（zone = 'local' 为展开），下面是“AI 建议 · 请核实”的问题卡片，最后是可折叠的逐条结论。
 */
export type Zone = 'ai' | 'local' | 'req'
const props = defineProps<{
  taskId: string
  run: RunDetail | null
  runLoading: boolean
  runError: string
  localOnly: boolean
  findings: Finding[]
  selectedId: string
  askEnabled: boolean
  discussEnabled?: boolean
  localLoader: () => Promise<{ items: LocalFinding[] }>
  fresh: boolean
  /** 顶部模型徽标 */
  modelName?: string
  /** 显示“追问”页签（点了切到提问视图） */
  askTab?: boolean
  /** 有上一稿时显示“变化 n”页签（V35-D-02）；null 为还在对比 */
  diffCount?: number | null
}>()
const severity = defineModel<string>('severity', { required: true })
const category = defineModel<string>('category', { required: true })
const handling = defineModel<string>('handling', { required: true })
const zone = defineModel<Zone>('zone', { required: true })
const emit = defineEmits<{
  select: [f: Finding]
  localSelect: [locs: string[]]
  updated: [f: Finding]
  ask: [prefill: string]
  discuss: [f: Finding]
  aiNotice: []
  askTab: []
  diffTab: []
}>()

const fx = useFx()
const categoryOptions = toOptions(CATEGORY_OPTIONS)
// 增量轮（V35-D-03，diff.md 4.3）：摘要“本轮新增 · 仍存在 · 已解决”可点击筛选；已解决不计入问题数，默认折叠在最后
const delta = computed(() => props.run?.scope === 'delta')
const carry = ref<'' | 'new' | 'persists' | 'resolved'>('')
const resolvedOpen = ref(false)
const isResolved = (f: Finding) => f.carry === 'resolved'
const carryOf = (f: Finding) => f.carry === 'unchanged' ? 'persists' : f.carry
const allLive = computed(() => (props.run?.findings ?? []).filter(f => !isResolved(f)))
const carryCounts = computed(() => {
  const list = props.run?.findings ?? []
  return (['new', 'persists', 'resolved'] as const).map(value => ({ value, label: CARRY_LABELS[value], n: list.filter(f => carryOf(f) === value).length }))
})
function pickCarry(value: 'new' | 'persists' | 'resolved') {
  carry.value = carry.value === value ? '' : value
  if (value === 'resolved') resolvedOpen.value = carry.value === 'resolved'
}
const live = computed(() => carry.value === 'resolved' ? [] : props.findings.filter(f => !isResolved(f) && (!carry.value || carryOf(f) === carry.value)))
const resolved = computed(() => carry.value && carry.value !== 'resolved' ? [] : props.findings.filter(isResolved))
const visible = computed(() => resolvedOpen.value ? [...live.value, ...resolved.value] : live.value)
watch(() => props.run?.id, () => { carry.value = ''; resolvedOpen.value = false })
// 处理状态计数切换（V31-U5F-03 问题 7）：计数随等级、类别筛选变化
const handlingCounts = computed(() => {
  const list = allLive.value.filter(f => (!severity.value || f.severity === severity.value) && (!category.value || f.category === category.value))
  const n = (s: string) => list.filter(f => f.handling_status === s).length
  return [{ value: '', label: `全部 ${list.length}` }, ...Object.entries(HANDLING_OPTIONS).map(([value, label]) => ({ value, label: `${label} ${n(value)}` }))]
})
const localCount = ref<number | null>(null)
// 等级分段（全部 / 高 / 中 / 低，带计数）
const severityOptions = computed(() => {
  const list = allLive.value
  const n = (v: string) => list.filter(f => f.severity === v).length
  return [{ value: '', label: `全部 ${list.length}` }, { value: 'high', label: `高 ${n('high')}` }, { value: 'medium', label: `中 ${n('medium')}` }, { value: 'low', label: `低 ${n('low')}` }]
})
const localOpen = computed(() => props.localOnly || zone.value === 'local')
function toggleLocal() { zone.value = zone.value === 'local' ? 'ai' : 'local' }
const reqOpen = ref(false)
const no = (f: Finding) => String((props.run?.findings.findIndex(item => item.id === f.id) ?? -1) + 1).padStart(2, '0')

// 刚完成的轮次第一次打开：完整特效下卡片依次上浮（每张 30ms，最多 12 张）
const stagger = ref(false)
onMounted(() => {
  if (!props.fresh || fx.value !== 'full') return
  stagger.value = true
  setTimeout(() => { stagger.value = false }, 1200)
})

// 键盘处理（V321-E-05）：↑↓（或 K/J）切换问题，A 采纳，I 忽略；处理后自动跳到下一条待处理。
// 在输入框、下拉、弹窗里不响应；讨论栏打开时问题栏隐藏，同样不响应。
const rail = ref<HTMLElement>()
let keyBusy = false
function inField(target: EventTarget | null) {
  const el = target as HTMLElement | null
  return !!el && (el.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName) || !!el.closest('.si-segmented, .el-dialog, .el-drawer, .el-message-box, .el-select-dropdown'))
}
function step(delta: number) {
  const list = visible.value
  if (!list.length) return
  const at = list.findIndex(f => f.id === props.selectedId)
  const next = list[at < 0 ? (delta > 0 ? 0 : list.length - 1) : Math.min(list.length - 1, Math.max(0, at + delta))]
  if (next && next.id !== props.selectedId) emit('select', next)
}
async function handle(status: HandlingStatus) {
  const current = live.value.find(f => f.id === props.selectedId)
  if (!current || keyBusy) return
  keyBusy = true
  const list = live.value, at = list.indexOf(current)
  try {
    // 已是该状态时不重复保存，只跳到下一条待处理
    if (current.handling_status !== status) emit('updated', await runsApi.patchFinding(current.id, { handling_status: status }))
    const next = [...list.slice(at + 1), ...list.slice(0, at)].find(f => f.handling_status === 'pending')
    if (next) emit('select', next)
  } catch (e) { ElMessage.error(e instanceof ApiError ? e.message : '保存失败，请重试') }
  finally { keyBusy = false }
}
// 卡片上点了“采纳 / 忽略”：与快捷键 A / I 一致，展开下一条待处理；没有待处理时收起本条（V33-C-02）。
// 稍等分段控件滑到新位置再切换，让用户看到点中的结果
let handledTimer = 0
function onHandled(f: Finding) {
  window.clearTimeout(handledTimer)
  handledTimer = window.setTimeout(() => {
    if (props.selectedId !== f.id) return
    const list = live.value, at = list.findIndex(x => x.id === f.id)
    const next = [...list.slice(at + 1), ...list.slice(0, at)].find(x => x.handling_status === 'pending')
    emit('select', next ?? f) // 选中同一条即收起
  }, motionMs('struct'))
}
function onKey(e: KeyboardEvent) {
  if (e.ctrlKey || e.metaKey || e.altKey || e.shiftKey || e.isComposing) return
  if (props.localOnly || !props.run || props.runLoading || !rail.value?.offsetParent) return
  if (inField(e.target) || document.body.classList.contains('el-popup-parent--hidden')) return
  const key = e.key.toLowerCase()
  if (key === 'arrowdown' || key === 'j') { e.preventDefault(); step(1) }
  else if (key === 'arrowup' || key === 'k') { e.preventDefault(); step(-1) }
  else if (key === 'a') { e.preventDefault(); void handle('adopted') }
  else if (key === 'i') { e.preventDefault(); void handle('ignored') }
}
onMounted(() => document.addEventListener('keydown', onKey))
onBeforeUnmount(() => { document.removeEventListener('keydown', onKey); window.clearTimeout(handledTimer) })

const aiEl = ref<HTMLElement>()
// 切换轮次时回到顶部
watch(() => props.run?.id, () => { if (aiEl.value) aiEl.value.scrollTop = 0; reqOpen.value = false })
// 画布点了标注：问题栏滚到该卡片
watch(() => props.selectedId, async (id) => {
  if (!id) return
  await nextTick()
  aiEl.value?.querySelector<HTMLElement>(`[data-finding="${CSS.escape(id)}"]`)?.scrollIntoView({ block: 'nearest', behavior: fx.value === 'off' ? 'auto' : 'smooth' })
})
</script>

<template>
  <aside ref="rail" class="rail">
    <div class="rp-tabs">
      <span class="tab on">意见</span>
      <button v-if="diffCount !== undefined" type="button" class="tab" @click="emit('diffTab')">变化<span class="tab-n num">{{ diffCount ?? '…' }}</span></button>
      <button v-if="askTab" type="button" class="tab" @click="emit('askTab')">追问</button>
      <span v-if="modelName" class="ai-tag"><Sparkle :size="12" :stroke-width="2" />{{ modelName }}</span>
    </div>
    <SiNotice v-if="runError" type="error" :closable="false" :title="runError" class="alert" />
    <template v-if="run">
      <SiNotice v-if="run.status === 'failed'" type="error" :closable="false" :title="run.error_message || '审核失败'" class="alert" />
      <SiNotice v-else-if="run.status === 'partial' && run.error_message" type="warning" :closable="false" :title="run.error_message" class="alert" />
    </template>
    <div v-if="runLoading" class="loading"><el-skeleton :rows="6" animated /></div>
    <div v-else ref="aiEl" class="zone">
      <div v-if="!localOnly && run && run.status !== 'failed'" class="sev-switch"><SiSegmented v-model="severity" :options="severityOptions" ariaLabel="按等级筛选" /></div>
      <RailSummary :run="run" :local-only="localOnly" @ai-notice="emit('aiNotice')" />

      <!-- 本机计算 · 结果确定：在上，可折叠 -->
      <section class="calc" :class="{ open: localOpen }">
        <button v-if="!localOnly" type="button" class="calc-h" :aria-expanded="localOpen" @click="toggleLocal">
          <span class="calc-ic"><Calculator :size="14" :stroke-width="1.75" /></span><b>本机计算 · 结果确定</b>
          <span class="calc-n num">{{ localCount ?? '—' }} 项</span>
          <component :is="localOpen ? ChevronDown : ChevronRight" class="calc-chev" :size="15" :stroke-width="1.75" />
        </button>
        <div v-show="localOpen" class="calc-body"><LocalCheckList :loader="localLoader" :load-key="taskId" @count="localCount = $event" @select="emit('localSelect', $event)" /></div>
      </section>

      <template v-if="!localOnly">
        <EmptyState v-if="run?.status === 'failed'" title="未取得有效 AI 结论" description="模型审核未完成，请重新审核。本机计算的结果在上方。" />
        <template v-else-if="run">
          <div v-if="delta" class="carry-switch" role="group" aria-label="按本轮状态筛选">
            <template v-for="(c, i) in carryCounts" :key="c.value"><span v-if="i" class="dot" aria-hidden="true">·</span>
              <button type="button" class="carry-btn" :class="[`cb-${c.value}`, { on: carry === c.value }]" :aria-pressed="carry === c.value" @click="pickCarry(c.value)">{{ c.label }} <b class="num">{{ c.n }}</b></button></template>
          </div>
          <ZoneHeader kind="ai" :count="live.length">
            <el-select aria-label="类别" v-model="category" clearable placeholder="类别" size="small" class="filter"><el-option v-for="o in categoryOptions" :key="o.value" :label="o.label" :value="o.value" /></el-select>
          </ZoneHeader>
          <div class="handling-switch"><SiSegmented v-model="handling" :options="handlingCounts" size="small" ariaLabel="按处理状态筛选" /></div>
          <TransitionGroup name="si-list" tag="div" class="cards" :appear="stagger">
            <FindingCard v-for="(f, i) in live" :key="f.id" :data-finding="f.id" :finding="f" :no="no(f)" :selected="f.id === selectedId" :ask-enabled="askEnabled" :discuss-enabled="discussEnabled"
              :style="stagger && i < 8 ? { transitionDelay: `${i * 45}ms` } : undefined"
              @select="emit('select', $event)" @updated="emit('updated', $event)" @handled="onHandled" @ask="emit('ask', $event)" @discuss="emit('discuss', $event)" />
          </TransitionGroup>
          <section v-if="resolved.length" class="resolved">
            <button type="button" class="calc-h" :aria-expanded="resolvedOpen" @click="resolvedOpen = !resolvedOpen">
              <b>已解决</b><span class="calc-n num">{{ resolved.length }} 条</span>
              <component :is="resolvedOpen ? ChevronDown : ChevronRight" class="calc-chev" :size="15" :stroke-width="1.75" />
            </button>
            <div v-if="resolvedOpen" class="cards">
              <FindingCard v-for="f in resolved" :key="f.id" :data-finding="f.id" :finding="f" :no="no(f)" :selected="f.id === selectedId" :ask-enabled="askEnabled" :discuss-enabled="discussEnabled"
                @select="emit('select', $event)" @updated="emit('updated', $event)" @handled="onHandled" @ask="emit('ask', $event)" @discuss="emit('discuss', $event)" />
            </div>
          </section>
          <EmptyState v-if="!live.length && !resolved.length" :icon="ListChecks"
            :title="run.findings.length ? '没有符合筛选条件的问题' : '本次没有列出问题条目'"
            :description="run.findings.length ? '调整上方的等级、类别或处理状态筛选后再看。' : '结果由 AI 生成，仅供参考，请以人工复核为准。'" />
          <p v-if="visible.length" class="keys hint">快捷键：<kbd>↑</kbd><kbd>↓</kbd> 切换<span v-if="live.length"> · <kbd>A</kbd> 采纳 · <kbd>I</kbd> 忽略</span></p>
          <section v-if="run.requirement_results.length" class="req">
            <button type="button" class="calc-h" :aria-expanded="reqOpen" @click="reqOpen = !reqOpen">
              <b>逐条结论</b><span class="calc-n num">{{ run.requirement_results.length }} 条</span>
              <component :is="reqOpen ? ChevronDown : ChevronRight" class="calc-chev" :size="15" :stroke-width="1.75" />
            </button>
            <p v-if="reqOpen && run.coverage?.delta" class="hint req-note">增量复审沿用第 {{ run.coverage.delta.base_run_no }} 轮的逐条结论，只更新涉及变化的要求</p>
            <RequirementList v-if="reqOpen" :items="run.requirement_results" :ask-enabled="askEnabled" @ask="emit('ask', $event)" />
          </section>
        </template>
      </template>
    </div>
  </aside>
</template>

<style scoped>
.rail { display: flex; flex-direction: column; min-height: 0; height: 100%; border: 1px solid var(--si-hair); border-radius: var(--si-radius-card); background: var(--si-surface-1); box-shadow: var(--si-shadow-1); overflow: hidden; }
.rp-tabs { flex: none; display: flex; align-items: center; gap: 18px; height: 48px; padding: 0 16px; border-bottom: 1px solid var(--si-hair-faint); }
.tab { position: relative; display: inline-flex; align-items: center; height: 48px; padding: 0; border: 0; background: none; color: var(--si-ink-3); font: var(--si-font-label); cursor: pointer; }
.tab:hover { color: var(--si-ink-1); }
.tab.on { color: var(--si-ink-1); font-weight: 600; cursor: default; }
.tab-n { margin-left: 6px; padding: 0 6px; border-radius: 999px; background: var(--si-surface-2); color: var(--si-ink-3); font: var(--si-font-label-sm); }
.tab.on::after { content: ''; position: absolute; left: 0; right: 0; bottom: -1px; height: 2px; border-radius: 2px; background: var(--si-brand-2); }
.ai-tag { display: inline-flex; align-items: center; gap: 5px; height: 24px; margin-left: auto; padding: 0 10px; border-radius: 999px; background: var(--si-ai-soft); color: var(--si-ai-ink); font: var(--si-font-badge); }
.ai-tag svg { color: var(--si-ai); }
.alert { flex: none; margin: 12px 16px 0; width: auto; }
.loading { padding: 16px; }
.zone { flex: 1; min-height: 0; overflow-y: auto; padding: 12px 16px 16px; display: flex; flex-direction: column; gap: 10px; }
.sev-switch :deep(.si-segmented) { display: flex; width: 100%; }
.sev-switch :deep(.si-segmented button) { flex: 1; }
.calc { border: 1px solid var(--si-hair-faint); border-radius: 12px; background: var(--si-card-2); }
.calc-h { display: flex; align-items: center; gap: 8px; width: 100%; padding: 10px 12px; border: 0; background: none; color: var(--si-ink-1); font: var(--si-font-label); text-align: left; cursor: pointer; }
.calc-h:focus-visible { outline: 2px solid var(--si-focus); outline-offset: -2px; border-radius: 12px; }
.calc-ic { display: grid; place-items: center; width: 24px; height: 24px; border-radius: 7px; background: var(--si-blue-tint); color: var(--si-brand-2); }
.calc-n { margin-left: auto; color: var(--si-ink-3); font: var(--si-font-caption); }
.calc-chev { color: var(--si-ink-4); }
.calc-body { padding: 0 12px 10px; }
.req { border-top: 1px solid var(--si-hair-faint); padding-top: 4px; }
.req .calc-h { padding: 8px 0; }
.filter { width: 104px; }
.handling-switch :deep(.si-segmented) { display: flex; width: 100%; }
.handling-switch :deep(.si-segmented button) { flex: 1; }
.keys { margin: 4px 0 0; }
.keys kbd { margin: 0 2px; padding: 0 5px; border: 1px solid var(--si-hair-strong); border-radius: 4px; background: var(--si-surface-2); font: var(--si-font-code-sm); }
.carry-switch { display: flex; align-items: center; gap: 4px; flex-wrap: wrap; }
.carry-switch .dot { color: var(--si-ink-4); }
.carry-btn { height: 26px; padding: 0 10px; border: 1px solid transparent; border-radius: 999px; background: none; color: var(--si-ink-2); font: var(--si-font-label-sm); cursor: pointer; }
.carry-btn b { font-weight: 600; color: var(--si-ink-1); }
.carry-btn:hover { background: var(--si-surface-2); }
.carry-btn.on.cb-new { border-color: var(--si-brand-line); background: var(--si-blue-tint); color: var(--si-blue-ink); }
.carry-btn.on.cb-persists { border-color: var(--si-warn-line); background: var(--si-warn-soft); color: var(--si-warn-ink); }
.carry-btn.on.cb-resolved { border-color: var(--si-ok); background: var(--si-ok-soft); color: var(--si-ok-ink); }
.resolved { border-top: 1px solid var(--si-hair-faint); padding-top: 4px; }
.resolved .calc-h { padding: 8px 0; }
.req-note { margin: 0 0 6px; }
.cards { position: relative; display: flex; flex-direction: column; gap: 10px; }
</style>
