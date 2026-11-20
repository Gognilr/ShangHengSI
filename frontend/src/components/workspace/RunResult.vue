<script setup lang="ts">
import { useAuthStore } from '@/stores/auth'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { confirmOnce } from '@/utils/confirmOnce'
import { MessageSquare, RefreshCw } from '@lucide/vue'
import { ApiError } from '@/api/http'
import { assistantApi, confirmationTask, type Session } from '@/api/assistant'
import { reportApi } from '@/api/report'
import { runsApi, type Finding, type RunDetail, type RunListItem } from '@/api/runs'
import type { CanvasAnnotation } from '@/components/ConfirmCanvas.vue'
import FindingCard from '@/components/FindingCard.vue'
import ExportButton from '@/components/result/ExportButton.vue'
import FindingsRail, { type Zone } from '@/components/result/FindingsRail.vue'
import ReceiptStrip from '@/components/result/ReceiptStrip.vue'
import ResultCanvas from '@/components/result/ResultCanvas.vue'
import SplitHandle, { readStoredWidth } from '@/components/SplitHandle.vue'
import { RUN_STATUS_TAG, docTypeLabel, runStatusLabel, sourceLabel, stanceLabel } from '@/utils/labels'
import { formatDateTime } from '@/utils/time'

/**
 * 工作台里的审核结果（V32-B-01）：左原文、右问题清单，复用审核结果页的画布与问题栏；
 * 轮次、导出、问题处理都按 run id / finding id 读写，与审核任务相同，只有节点与原件改读会话接口。
 * 本组件只管一个会话的结果；发起新一轮、会话级的状态由父级 ReviewPane 管。
 */
/** discussId：正在讨论的问题；讨论替换右侧问题栏，问题栏只隐藏不卸载（V321-E-02）。讨论区内容由工作台经插槽 discussion 提供。 */
const props = defineProps<{ session: Session; rounds: RunListItem[]; selectedRun: string; fresh: boolean; discussId?: string }>()
const emit = defineEmits<{ selectRun: [id: string]; newRound: []; discuss: [anchor: { id: string; no: string; title: string; runNo: number }]; closeDiscuss: []
  /** 读到该轮详情：父级据此在顶部工具条显示“已完成 · 用时” */
  loaded: [run: RunDetail]
  /** 工具条“追问”：切到提问视图 */
  ask: [] }>()

const task = computed(() => confirmationTask(props.session))
// 节点与原件走会话接口（审核任务的 /tasks/{id}/files/… 只认 kind=review）
const nodeLoader = (_id: string, fid: string) => assistantApi.nodes(props.session.id, fid)
const rawLoader = (_id: string, fid: string) => assistantApi.raw(props.session.id, fid)
const run = ref<RunDetail | null>(null)
const runLoading = ref(false)
const runError = ref('')
const exporting = ref('')
const savedTick = ref(0)
const annotationNotice = ref('')
let runVersion = 0

function message(e: unknown) {
  return e instanceof ApiError ? e.status === 404 ? '审核轮次不存在或已被删除' : `${e.message}，请刷新页面后重试` : '加载失败，请刷新页面后重试'
}
function resetView() {
  selectedId.value = ''; rawHighlight.value = []; zone.value = 'ai'
  fSeverity.value = ''; fCategory.value = ''; fHandling.value = ''
}
async function loadRun(id: string) {
  const version = ++runVersion
  runLoading.value = true; runError.value = ''; annotationNotice.value = ''; run.value = null
  resetView()
  if (!id) { runLoading.value = false; return }
  try {
    const detail = await runsApi.get(id)
    if (version === runVersion) { run.value = detail; emit('loaded', detail) }
  } catch (e) { if (version === runVersion) runError.value = message(e) }
  finally { if (version === runVersion) runLoading.value = false }
}

// ---- 本地校验 ----
let localPromise: Promise<unknown> | null = null
const localItems = ref<Awaited<ReturnType<typeof assistantApi.localFindings>>['items']>([])
function localLoader() {
  const promise = assistantApi.localFindings(props.session.id)
  localPromise = promise
  void promise.then(res => { if (localPromise === promise) localItems.value = res.items }).catch(() => { /* 列表自己显示错误 */ })
  return promise
}
watch(() => `${props.session.id}:${props.session.material_version}`, () => { localPromise = null; localItems.value = [] })

// ---- 筛选、分区与选中（与审核结果页一致）----
const fSeverity = ref(''), fCategory = ref(''), fHandling = ref('')
const zone = ref<Zone>('ai')
const selectedId = ref('')
const rawHighlight = ref<string[]>([])
const findings = computed<Finding[]>(() => (run.value?.findings ?? []).filter(f =>
  (!fSeverity.value || f.severity === fSeverity.value)
  && (!fCategory.value || f.category === fCategory.value)
  && (!fHandling.value || f.handling_status === fHandling.value)))
const noOf = (id: string) => String((run.value?.findings.findIndex(f => f.id === id) ?? -1) + 1).padStart(2, '0')
const annotations = computed<CanvasAnnotation[]>(() => findings.value.filter(f => f.locs.length)
  .map(f => ({ id: f.id, no: noOf(f.id), severity: f.severity, loc: f.locs[0]!, excerpt: f.quote_excerpt })))
const localLocs = computed(() => zone.value === 'local' ? localItems.value.flatMap(l => l.locs) : [])

watch(() => props.selectedRun, id => { void loadRun(id) }, { immediate: true })

const canvas = ref<InstanceType<typeof ResultCanvas>>()
// 讨论回复的原文依据在左侧画布定位（V321-E-13）
function locateLoc(loc: string) { void canvas.value?.locate(loc) }
function onSelect(f: Finding) {
  if (selectedId.value === f.id) { selectedId.value = ''; return }
  selectedId.value = f.id
  rawHighlight.value = [...f.locs]
  if (f.locs[0]) void canvas.value?.locate(f.locs[0], f.id)
}
function onCanvasSelect(id: string) {
  const f = run.value?.findings.find(item => item.id === id)
  if (!f) return
  zone.value = 'ai'; selectedId.value = id; rawHighlight.value = [...f.locs]
  if (discussing.value && discussEnabled.value) onDiscuss(f)  // 讨论中点了另一处标注：右栏切到该问题
}
function onLocalSelect(locs: string[]) {
  selectedId.value = ''; rawHighlight.value = [...locs]
  if (locs[0]) void canvas.value?.locate(locs[0])
}
function onAiNotice() {
  zone.value = 'local'
  const first = localItems.value.find(l => l.check_code === 'L-21' && l.locs.length)
  if (first) onLocalSelect(first.locs)
}
// 继续讨论：交给工作台切到提问视图并锚定该问题（V32-B-02）；审核中或原件已删除时不开放
const auth = useAuthStore()
const discussEnabled = computed(() => auth.can('use.assistant') && !props.session.review_running && !props.session.original_deleted && !!run.value && ['completed', 'partial'].includes(run.value.status))
function onDiscuss(f: Finding) {
  if (!run.value) return
  selectedId.value = f.id; rawHighlight.value = [...f.locs]
  if (f.locs[0]) void canvas.value?.locate(f.locs[0], f.id)
  cardOpen.value = false
  emit('discuss', { id: f.id, no: noOf(f.id), title: f.title, runNo: run.value.run_no })
}
// 讨论右栏：顶部是该问题的卡片（默认收起，点开看全文），下面是对话
const discussed = computed(() => props.discussId ? run.value?.findings.find(f => f.id === props.discussId) ?? null : null)
const discussing = computed(() => !!discussed.value)
const cardOpen = ref(false)
// 换了轮次、问题不在本轮时退出讨论
watch(() => [props.discussId, run.value?.id] as const, ([fid]) => { if (fid && run.value && !discussed.value) emit('closeDiscuss') })
function onUpdated(updated: Finding) {
  if (run.value) run.value = { ...run.value, findings: run.value.findings.map(f => f.id === updated.id ? updated : f) }
}

// ---- 导出（与审核结果页相同的确认与提示）----
const exportReason = computed(() => runLoading.value ? '正在加载该轮次，请稍候'
  : !run.value ? '请先加载审核轮次'
    : ['queued', 'running', 'blocked'].includes(run.value.status) ? '该轮次尚未完成，无法导出' : '')
function exportError(e: unknown) {
  ElMessage.error(e instanceof ApiError ? e.status === 404 ? '会话不存在或已被删除' : e.message : '导出失败，请稍后重试')
}
async function exportReport() {
  const runId = run.value?.id
  if (!runId || exporting.value || exportReason.value) return
  exporting.value = '审核报告'
  try {
    const result = await reportApi.run(runId)
    if (result === 'cancelled') return
    if (result === 'downloaded') ElMessage.success('报告已下载')
    savedTick.value++
  } catch (e) { exportError(e) } finally { exporting.value = '' }
}
async function exportAnnotated(seq: number) {
  if (!run.value || exporting.value || exportReason.value) return
  if (!['completed', 'partial'].includes(run.value.status) || !task.value.files.some(file => file.seq === seq && file.annotatable)) return
  const runId = run.value.id
  if (!await confirmOnce('annotated', '批注版含内部审核意见，请勿直接发给对方。', '导出批注版', '导出批注版')) return
  exporting.value = '批注版'
  try {
    const { saveResult, total, unanchored } = await reportApi.annotated(runId, seq)
    if (saveResult === 'cancelled' || run.value?.id !== runId) return
    annotationNotice.value = unanchored > 0 ? `共 ${total} 条批注，其中 ${unanchored} 条未能精确定位，已挂在所在段落开头。` : ''
    if (!unanchored) ElMessage.success(`已生成批注版，共 ${total} 条批注`)
    savedTick.value++
  } catch (e) { exportError(e) } finally { exporting.value = '' }
}

// ---- 收尾（V321-E-05）：待处理归零后顶部给出下一步，不让人自己找导出入口 ----
const doneRun = computed(() => !!run.value && ['completed', 'partial'].includes(run.value.status))
const handled = computed(() => {
  const list = run.value?.findings ?? []
  return { total: list.length, pending: list.filter(f => f.handling_status === 'pending').length,
    adopted: list.filter(f => f.handling_status === 'adopted').length, ignored: list.filter(f => f.handling_status === 'ignored').length }
})
const wrapUp = computed(() => doneRun.value && !props.session.review_running && handled.value.total > 0 && handled.value.pending === 0)
const wrapUpClosed = ref('')

// ---- 头部说明（澄明 8.4：合同 · 采购方立场 · 第 1 轮）----
const lede = computed(() => {
  const r = run.value
  return [r ? docTypeLabel(r.main_doc_type) : '', `${stanceLabel(r?.stance ?? props.session.stance)}立场`, r ? `第 ${r.run_no} 轮` : ''].filter(Boolean).join(' · ')
})
const modelShort = computed(() => run.value?.model_label ?? run.value?.model_name ?? '')
const canAsk = computed(() => auth.can('use.assistant'))
const modelText = computed(() => run.value?.model_label ? `${run.value.model_label}（${run.value.model_name ?? '—'}）` : run.value?.model_name ?? '—')
const details = computed(() => {
  const r = run.value
  const parts = [`${stanceLabel(r?.stance ?? props.session.stance)} / ${sourceLabel(r?.source ?? props.session.source)}`]
  if (r) parts.unshift(docTypeLabel(r.main_doc_type))
  if (r) parts.push(`审核于 ${formatDateTime(r.finished_at)}`, `模型 ${modelText.value}`)
  if (r?.template) parts.push(`本轮对照公司范本：${r.template.name}`)
  return parts.join(' · ')
})
const newReason = computed(() => props.session.original_deleted ? '原件已按保留期限删除，不能再审核' : props.session.generating ? '回复完成后可以发起审核' : '')

// ---- 两栏宽度：与审核结果页共用同一份本地设置 ----
const RAIL_KEY = 'si.review.railWidth'
const HANDLE = 8, GAP = 12, RAIL_MIN = 400, CANVAS_MIN = 420
const splitEl = ref<HTMLElement>()
const containerWidth = ref(0)
const storedRail = ref<number | null>(null)
const railMax = computed(() => Math.max(RAIL_MIN, containerWidth.value - HANDLE - GAP * 2 - CANVAS_MIN))
// 右栏默认 440（澄明 8.4 ③），宽屏可拖宽
const railDefault = computed(() => Math.min(railMax.value, 440))
const railWidth = computed({
  get: () => storedRail.value === null ? railDefault.value : Math.min(railMax.value, Math.max(RAIL_MIN, storedRail.value)),
  set: (value: number) => { storedRail.value = value },
})
let resizeObserver: ResizeObserver | undefined
function measure() { containerWidth.value = splitEl.value?.clientWidth ?? 0 }
function bindSplit(el: unknown) {
  const node = (el as HTMLElement | null) ?? undefined
  if (node === splitEl.value) return
  resizeObserver?.disconnect()
  splitEl.value = node
  if (node) { measure(); resizeObserver = new ResizeObserver(measure); resizeObserver.observe(node) }
}
onMounted(() => { const saved = readStoredWidth(RAIL_KEY, 0, 100000, 0); storedRail.value = saved > 0 ? saved : null })
onBeforeUnmount(() => resizeObserver?.disconnect())
</script>

<template>
  <div class="run-result">
    <header class="bar">
      <span class="lede" :title="details">{{ lede }}</span>
      <el-select v-if="rounds.length > 1" :model-value="selectedRun" aria-label="审核轮次" class="round-select" size="small" @update:model-value="emit('selectRun', $event)">
        <el-option v-for="item in rounds" :key="item.id" :value="item.id" :label="`第 ${item.run_no} 轮`">第 {{ item.run_no }} 轮 · {{ formatDateTime(item.finished_at ?? item.queued_at) }} · {{ runStatusLabel(item.status) }}</el-option>
      </el-select>
      <el-tag v-if="run && run.status !== 'completed'" :type="RUN_STATUS_TAG[run.status] ?? 'info'" size="small" disable-transitions>{{ runStatusLabel(run.status) }}</el-tag>
      <ReceiptStrip v-if="run && session.policy === 'ai'" :run="run" :file-names="task.files.map(f => f.original_name)" />
      <div class="actions">
        <el-button v-if="canAsk" :disabled="session.review_running" @click="emit('ask')"><MessageSquare :size="15" :stroke-width="1.75" />追问</el-button>
        <el-tooltip :content="newReason" :disabled="!newReason" placement="bottom">
          <span><el-button :disabled="!!newReason" @click="emit('newRound')"><RefreshCw :size="15" :stroke-width="1.75" />重新审核</el-button></span>
        </el-tooltip>
        <ExportButton :task="task" :run="run" :export-reason="exportReason" :exporting="exporting" :deleting="false" :saved-tick="savedTick"
          @report="exportReport" @annotated="exportAnnotated" />
      </div>
    </header>
    <div v-if="wrapUp && wrapUpClosed !== run?.id" class="wrap-up" role="status">
      <span class="wrap-text">✓ {{ handled.total }} 条问题都处理完了（采纳 {{ handled.adopted }} · 忽略 {{ handled.ignored }}）</span>
      <el-button size="small" type="primary" :disabled="!!exporting || !!exportReason" @click="exportReport">导出报告</el-button>
      <el-button size="small" link aria-label="关闭提示" @click="wrapUpClosed = run?.id ?? ''">×</el-button>
    </div>
    <el-alert v-if="annotationNotice" type="warning" show-icon closable :title="annotationNotice" class="annotation-notice" @close="annotationNotice = ''" />
    <div :ref="bindSplit" class="split">
      <div class="canvas-col">
        <ResultCanvas ref="canvas" :key="`${session.id}:${session.material_version}`" :task="task" :note="run ? run.user_note : null" :annotations="annotations" :local-locs="localLocs"
          :selected-finding-id="selectedId" :raw-highlight="rawHighlight" :ask-enabled="false"
          :node-loader="nodeLoader" :raw-loader="rawLoader"
          @select="onCanvasSelect" />
      </div>
      <SplitHandle v-model="railWidth" reverse :min="RAIL_MIN" :max="railMax" :default-value="railDefault" :storage-key="RAIL_KEY" label="调整问题栏宽度" />
      <div class="rail-col" :style="{ flexBasis: `${railWidth}px` }">
        <FindingsRail v-show="!discussing" v-model:severity="fSeverity" v-model:category="fCategory" v-model:handling="fHandling" v-model:zone="zone"
          :task-id="session.id" :run="run" :run-loading="runLoading" :run-error="runError" :local-only="false" :findings="findings"
          :selected-id="selectedId" :ask-enabled="false" :discuss-enabled="discussEnabled" :local-loader="localLoader" :fresh="fresh"
          :model-name="modelShort" :ask-tab="canAsk && !session.review_running" @ask-tab="emit('ask')"
          @select="onSelect" @discuss="onDiscuss" @local-select="onLocalSelect" @updated="onUpdated" @ai-notice="onAiNotice" />
        <section v-if="discussed" class="discuss" aria-label="讨论问题">
          <header class="discuss-head">
            <el-button link type="primary" size="small" @click="emit('closeDiscuss')">← 返回问题列表</el-button>
            <span class="discuss-title">讨论问题 {{ noOf(discussed.id) }}</span>
          </header>
          <div class="discuss-card">
            <FindingCard :finding="discussed" :no="noOf(discussed.id)" :selected="cardOpen" @select="cardOpen = !cardOpen" @updated="onUpdated" />
          </div>
          <div class="discuss-body"><slot name="discussion" :finding="discussed" :no="noOf(discussed.id)" :update="onUpdated" :locate="locateLoc" /></div>
        </section>
      </div>
    </div>
  </div>
</template>

<style scoped>
.run-result { flex: 1; min-height: 0; display: flex; flex-direction: column; padding: 0 20px; }
/* 结果工具条：一行说明 + 轮次 + 模型徽标，右侧追问、重新审核、导出（澄明 8.4 ③） */
.bar { flex: none; display: flex; align-items: center; gap: 10px; min-height: 52px; padding: 6px 0; border-bottom: 1px solid var(--si-hair-faint); }
.lede { min-width: 0; overflow: hidden; color: var(--si-ink-3); font: var(--si-font-label-sm); text-overflow: ellipsis; white-space: nowrap; }
.round-select { flex: none; width: 104px; }
.actions { display: flex; align-items: center; gap: 8px; margin: 0 0 0 auto; }
.actions :deep(.el-button + .el-button) { margin-left: 0; }
.actions :deep(.el-button > span) { gap: 6px; }
.wrap-up { flex: none; display: flex; align-items: center; gap: 8px; margin-top: 8px; padding: 6px 12px; border-radius: var(--si-radius-card); background: var(--el-color-success-light-9); color: var(--si-ok-ink); }
.wrap-text { flex: 1; min-width: 0; font: var(--si-font-label); }
.annotation-notice { flex: none; margin-top: 8px; }
.split { display: flex; flex: 1; min-height: 0; gap: 12px; padding: 12px 0 16px; }
.canvas-col { flex: 1; min-width: 420px; min-height: 0; display: flex; flex-direction: column; }
.rail-col { flex: 0 0 auto; min-width: 400px; min-height: 0; display: flex; flex-direction: column; }
.rail-col > :deep(.rail) { flex: 1; }
.discuss { flex: 1; min-height: 0; display: flex; flex-direction: column; border: 1px solid var(--si-hair); border-radius: var(--si-radius-card); background: var(--si-surface-1); overflow: hidden; }
.discuss-head { flex: none; display: flex; align-items: center; gap: 10px; padding: 8px 12px; border-bottom: 1px solid var(--si-hair); }
.discuss-title { color: var(--si-ink-3); font: var(--si-font-label-sm); }
.discuss-card { flex: none; max-height: 45%; overflow-y: auto; padding: 8px 12px; border-bottom: 1px solid var(--si-hair); }
.discuss-body { flex: 1; min-height: 0; display: flex; flex-direction: column; }
</style>
