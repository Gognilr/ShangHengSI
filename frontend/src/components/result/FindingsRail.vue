<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { ListChecks } from '@lucide/vue'
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
import { CATEGORY_OPTIONS, HANDLING_OPTIONS, toOptions } from '@/utils/labels'
import { motionMs, useFx } from '@/utils/motion'

/** 问题栏（V31-U3 3.4～3.6）：压缩结论 + “AI 建议 | 本地计算 | 逐条结论”三个分区，各自滚动、切换时保持位置。 */
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
}>()

const fx = useFx()
const categoryOptions = toOptions(CATEGORY_OPTIONS)
// 处理状态计数切换（V31-U5F-03 问题 7）：计数随等级、类别筛选变化
const handlingCounts = computed(() => {
  const list = (props.run?.findings ?? []).filter(f => (!severity.value || f.severity === severity.value) && (!category.value || f.category === category.value))
  const n = (s: string) => list.filter(f => f.handling_status === s).length
  return [{ value: '', label: `全部 ${list.length}` }, ...Object.entries(HANDLING_OPTIONS).map(([value, label]) => ({ value, label: `${label} ${n(value)}` }))]
})
const localCount = ref<number | null>(null)
const zoneOptions = computed(() => [
  { value: 'ai' as const, label: props.run?.status === 'failed' ? 'AI 建议 · 未取得' : `AI 建议 ${props.run?.findings.length ?? 0}` },
  { value: 'local' as const, label: `本地计算 ${localCount.value ?? '—'}` },
  { value: 'req' as const, label: '逐条结论' },
])
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
  const list = props.findings
  if (!list.length) return
  const at = list.findIndex(f => f.id === props.selectedId)
  const next = list[at < 0 ? (delta > 0 ? 0 : list.length - 1) : Math.min(list.length - 1, Math.max(0, at + delta))]
  if (next && next.id !== props.selectedId) emit('select', next)
}
async function handle(status: HandlingStatus) {
  const current = props.findings.find(f => f.id === props.selectedId)
  if (!current || keyBusy) return
  keyBusy = true
  const list = props.findings, at = list.indexOf(current)
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
    const list = props.findings, at = list.findIndex(x => x.id === f.id)
    const next = [...list.slice(at + 1), ...list.slice(0, at)].find(x => x.handling_status === 'pending')
    emit('select', next ?? f) // 选中同一条即收起
  }, motionMs('struct'))
}
function onKey(e: KeyboardEvent) {
  if (e.ctrlKey || e.metaKey || e.altKey || e.shiftKey || e.isComposing) return
  if (props.localOnly || zone.value !== 'ai' || !props.run || props.runLoading || !rail.value?.offsetParent) return
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
const localEl = ref<HTMLElement>()
const reqEl = ref<HTMLElement>()
// 切换轮次时各分区回到顶部
watch(() => props.run?.id, () => { for (const el of [aiEl.value, localEl.value, reqEl.value]) if (el) el.scrollTop = 0 })
// 画布点了标注：问题栏滚到该卡片
watch(() => props.selectedId, async (id) => {
  if (!id) return
  await nextTick()
  aiEl.value?.querySelector<HTMLElement>(`[data-finding="${CSS.escape(id)}"]`)?.scrollIntoView({ block: 'nearest', behavior: fx.value === 'off' ? 'auto' : 'smooth' })
})
</script>

<template>
  <aside ref="rail" class="rail">
    <el-alert v-if="runError" type="error" show-icon :closable="false" :title="runError" class="alert" />
    <template v-if="run">
      <el-alert v-if="run.status === 'failed'" type="error" show-icon :closable="false" :title="run.error_message || '审核失败'" class="alert" />
      <el-alert v-else-if="run.status === 'partial' && run.error_message" type="warning" show-icon :closable="false" :title="run.error_message" class="alert" />
    </template>
    <div v-if="runLoading" class="loading"><el-skeleton :rows="6" animated /></div>
    <template v-else>
      <RailSummary v-model:severity="severity" :run="run" :local-only="localOnly" @ai-notice="emit('aiNotice')" />
      <div v-if="!localOnly && run" class="zone-switch"><SiSegmented v-model="zone" :options="zoneOptions" ariaLabel="问题分区" /></div>

      <div v-show="!localOnly && zone === 'ai'" ref="aiEl" class="zone">
        <EmptyState v-if="run?.status === 'failed'" title="未取得有效 AI 结论" description="模型审核未完成，请重新审核。本地校验结果可切换到“本地计算”查看。" />
        <template v-else-if="run">
          <ZoneHeader kind="ai" :count="findings.length">
            <el-select aria-label="类别" v-model="category" clearable placeholder="类别" size="small" class="filter"><el-option v-for="o in categoryOptions" :key="o.value" :label="o.label" :value="o.value" /></el-select>
          </ZoneHeader>
          <div class="handling-switch"><SiSegmented v-model="handling" :options="handlingCounts" size="small" ariaLabel="按处理状态筛选" /></div>
          <p v-if="props.run?.status !== 'failed' && findings.length" class="keys hint">快捷键：<kbd>↑</kbd><kbd>↓</kbd> 切换 · <kbd>A</kbd> 采纳 · <kbd>I</kbd> 忽略</p>
          <TransitionGroup name="si-list" tag="div" class="cards" :appear="stagger">
            <FindingCard v-for="(f, i) in findings" :key="f.id" :data-finding="f.id" :finding="f" :no="no(f)" :selected="f.id === selectedId" :ask-enabled="askEnabled" :discuss-enabled="discussEnabled"
              :style="stagger && i < 12 ? { transitionDelay: `${i * 30}ms` } : undefined"
              @select="emit('select', $event)" @updated="emit('updated', $event)" @handled="onHandled" @ask="emit('ask', $event)" @discuss="emit('discuss', $event)" />
          </TransitionGroup>
          <EmptyState v-if="!findings.length" :icon="ListChecks"
            :title="run.findings.length ? '没有符合筛选条件的问题' : '本次没有列出问题条目'"
            :description="run.findings.length ? '调整上方的等级、类别或处理状态筛选后再看。' : '结果由 AI 生成，仅供参考，请以人工复核为准。'" />
        </template>
      </div>
      <div v-show="localOnly || zone === 'local'" ref="localEl" class="zone">
        <LocalCheckList :loader="localLoader" :load-key="taskId" @count="localCount = $event" @select="emit('localSelect', $event)" />
      </div>
      <div v-show="!localOnly && zone === 'req'" ref="reqEl" class="zone">
        <EmptyState v-if="run?.status === 'failed'" title="未取得逐条审核结论" description="本次审核失败，请重新审核后查看。" />
        <template v-else-if="run">
          <ZoneHeader kind="ai" :count="run.requirement_results.length" />
          <RequirementList :items="run.requirement_results" :ask-enabled="askEnabled" @ask="emit('ask', $event)" />
        </template>
      </div>
    </template>
  </aside>
</template>

<style scoped>
.rail { display: flex; flex-direction: column; min-height: 0; height: 100%; border: 0; border-left: 1px solid var(--si-hair); border-radius: 0; background: transparent; overflow: hidden; }
.alert { flex: none; margin: 12px 16px 0; width: auto; }
.loading { padding: 16px; }
.zone-switch { flex: none; padding: 10px 16px 0; }
.zone-switch :deep(.si-segmented) { display: flex; width: 100%; }
.zone-switch :deep(.si-segmented button) { flex: 1; }
.zone { flex: 1; min-height: 0; overflow-y: auto; padding: 8px 16px 16px; }
.filter { width: 104px; }
.handling-switch { margin: 4px 0 8px; }
.handling-switch :deep(.si-segmented) { display: flex; width: 100%; }
.handling-switch :deep(.si-segmented button) { flex: 1; }
.keys { margin: 0 0 4px; }
.keys kbd { margin: 0 2px; padding: 0 5px; border: 1px solid var(--si-hair-strong); border-radius: 4px; background: var(--si-surface-2); font: var(--si-font-code-sm); }
.cards { position: relative; display: flex; flex-direction: column; gap: 8px; margin-top: 4px; }
</style>
