<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { RotateCw } from '@lucide/vue'
import ResultToolbar from '@/components/ResultToolbar.vue'
import ChatPanel from '@/components/ChatPanel.vue'
import ReRunDialog from '@/components/ReRunDialog.vue'
import SplitHandle, { readStoredWidth } from '@/components/SplitHandle.vue'
import type { CanvasAnnotation } from '@/components/ConfirmCanvas.vue'
import FindingsRail, { type Zone } from '@/components/result/FindingsRail.vue'
import ResultCanvas from '@/components/result/ResultCanvas.vue'
import { ApiError } from '@/api/http'
import { localFindingsApi, type LocalFinding } from '@/api/localFindings'
import { reportApi } from '@/api/report'
import { runsApi, type Finding, type RunDetail, type RunListItem } from '@/api/runs'
import { tasksApi, type TaskDetail } from '@/api/tasks'
import { formatDate } from '@/utils/time'

/** 审核结果页（V31-U3 第三节）：原文画布在左、问题栏在右；本组件只负责数据与编排。 */
const route = useRoute()
const router = useRouter()

const loading = ref(true)
const error = ref('')
const task = ref<TaskDetail | null>(null)
const run = ref<RunDetail | null>(null)
const rounds = ref<RunListItem[]>([])
const selectedRun = ref('')
const runLoading = ref(false)
const runError = ref('')
const deleting = ref(false)
const exporting = ref('')
const savedTick = ref(0)
const annotationNotice = ref('')
const rerunOpen = ref(false)
// 刚完成的轮次第一次打开（审核中跳转时在路由 state 中带 fresh）
const fresh = !!(history.state as { fresh?: boolean } | null)?.fresh
// 只在第一次打开时生效：之后刷新或从历史返回不再播放
if (fresh) history.replaceState({ ...history.state, fresh: false }, '')
let loadVersion = 0
let runVersion = 0

const localOnly = computed(() => task.value?.status === 'completed_local')
const expiryWarnings = computed(() => {
  const current = task.value
  if (!current) return []
  const soon = (value: string | null) => value && Date.parse(value) >= Date.now() && Date.parse(value) - Date.now() <= 7 * 86400000
  const messages: string[] = []
  if (soon(current.task_expires_at)) messages.push(`本任务将于 ${formatDate(current.task_expires_at)} 删除，如需留存请导出报告`)
  if (soon(current.original_expires_at)) messages.push(`原件将于 ${formatDate(current.original_expires_at)} 删除，之后不能重新审核和追问`)
  return messages
})
const reqVersion = computed(() => {
  if (!run.value) return '—'
  const parts = [run.value.builtin_req_version ? `内置 ${run.value.builtin_req_version}` : '']
  if (run.value.company_req_version != null) parts.push(`公司补充 v${run.value.company_req_version}`)
  return parts.filter(Boolean).join('，') || '—'
})

// ---- 本地校验：每个任务只读一次，问题栏列表与画布竖条共用 ----
let localPromise: Promise<{ items: LocalFinding[] }> | null = null
const localItems = ref<LocalFinding[]>([])
function localLoader() {
  localPromise ??= localFindingsApi.list(task.value!.id)
  return localPromise
}

// ---- 加载 ----
function loadMessage(e: unknown) {
  return e instanceof ApiError ? e.status === 404 ? '任务不存在或已被删除' : `${e.message}，请刷新页面后重试` : '加载失败，请刷新页面后重试'
}
function resetView() {
  selectedId.value = ''
  rawHighlight.value = []
  zone.value = 'ai'
  fSeverity.value = ''
  fCategory.value = ''
  fHandling.value = ''
}
async function load() {
  const version = ++loadVersion
  ++runVersion
  const id = String(route.params.taskId)
  loading.value = true
  error.value = ''
  annotationNotice.value = ''
  task.value = null
  run.value = null
  rounds.value = []
  selectedRun.value = ''
  runError.value = ''
  runLoading.value = false
  localPromise = null
  localItems.value = []
  resetView()
  try {
    const t = await tasksApi.get(id)
    if (version !== loadVersion) return
    // 还没到结果阶段的任务回到新建审核页，由那里按状态显示对应步骤
    if (!['reviewed', 'completed_local'].includes(t.status)) {
      await router.replace({ name: 'review-new', query: { task: id } })
      return
    }
    task.value = t
    localLoader().then(res => { if (version === loadVersion) localItems.value = res.items }).catch(() => { /* 列表自己显示错误 */ })
    const list = await runsApi.list(id)
    if (version !== loadVersion) return
    rounds.value = list.items
    selectedRun.value = t.latest_run?.id ?? list.items[0]?.id ?? ''
    if (selectedRun.value) await selectRun(selectedRun.value)
  } catch (e) {
    if (version === loadVersion) error.value = loadMessage(e)
  } finally {
    if (version === loadVersion) loading.value = false
  }
}
async function selectRun(id: string) {
  const version = ++runVersion
  selectedRun.value = id
  runLoading.value = true
  runError.value = ''
  annotationNotice.value = ''
  run.value = null
  resetView()
  try {
    const detail = await runsApi.get(id)
    if (version === runVersion) run.value = detail
  } catch (e) {
    if (version === runVersion) runError.value = loadMessage(e)
  } finally {
    if (version === runVersion) runLoading.value = false
  }
}
watch(() => route.params.taskId, load)

// ---- 筛选、分区与选中 ----
const fSeverity = ref('')
const fCategory = ref('')
const fHandling = ref('')
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
const localLocs = computed(() => localOnly.value || zone.value === 'local' ? localItems.value.flatMap(l => l.locs) : [])

const canvas = ref<InstanceType<typeof ResultCanvas>>()
function onSelect(f: Finding) {
  if (selectedId.value === f.id) { selectedId.value = ''; return }
  selectedId.value = f.id
  rawHighlight.value = [...f.locs]
  if (f.locs[0]) void canvas.value?.locate(f.locs[0], f.id)
}
function onCanvasSelect(id: string) {
  const f = run.value?.findings.find(item => item.id === id)
  if (!f) return
  zone.value = 'ai'
  selectedId.value = id
  rawHighlight.value = [...f.locs]
}
function onLocalSelect(locs: string[]) {
  selectedId.value = ''
  rawHighlight.value = [...locs]
  if (locs[0]) void canvas.value?.locate(locs[0])
}
function onAiNotice() {
  zone.value = 'local'
  const first = localItems.value.find(l => l.check_code === 'L-21' && l.locs.length)
  if (first) onLocalSelect(first.locs)
}
function onUpdated(updated: Finding) {
  if (run.value) run.value = { ...run.value, findings: run.value.findings.map(f => f.id === updated.id ? updated : f) }
}

// ---- 追问 ----
const chat = ref<InstanceType<typeof ChatPanel>>()
const askEnabled = computed(() => !!chat.value?.available && !deleting.value)
function ask(prefill: string) { void chat.value?.openWith(prefill) }

// ---- 导出 ----
const exportReason = computed(() => localOnly.value ? ''
  : runLoading.value ? '正在加载该轮次，请稍候'
    : !run.value ? '请先加载审核轮次'
      : ['queued', 'running', 'blocked'].includes(run.value.status) ? '该轮次尚未完成，无法导出' : '')
function exportError(e: unknown) {
  ElMessage.error(e instanceof ApiError ? e.status === 404 ? '任务不存在或已被删除' : e.message : '导出失败，请稍后重试')
}
async function exportReport() {
  if (!task.value || exporting.value || exportReason.value) return
  const taskId = task.value.id
  const runId = run.value?.id
  const isLocal = localOnly.value
  if (!isLocal && !runId) return
  exporting.value = isLocal ? '本地校验报告' : '审核报告'
  try {
    const result = runId && !isLocal ? await reportApi.run(runId) : await reportApi.local(taskId)
    if (result === 'cancelled') return
    if (result === 'downloaded') ElMessage.success('报告已下载')
    savedTick.value++
  } catch (e) { exportError(e) } finally { exporting.value = '' }
}
async function exportAnnotated(seq: number) {
  if (!task.value || !run.value || exporting.value || deleting.value || exportReason.value) return
  if (!['completed', 'partial'].includes(run.value.status) || !task.value.files.some(file => file.seq === seq && file.annotatable)) return
  const runId = run.value.id
  const taskId = task.value.id
  try {
    await ElMessageBox.confirm('批注版含内部审核意见，请勿直接发给对方。', '导出批注版', { type: 'warning', confirmButtonText: '导出批注版', cancelButtonText: '取消' })
  } catch { return }
  exporting.value = '批注版'
  try {
    const { saveResult, total, unanchored } = await reportApi.annotated(runId, seq)
    if (saveResult === 'cancelled' || task.value?.id !== taskId || run.value?.id !== runId) return
    annotationNotice.value = unanchored > 0 ? `共 ${total} 条批注，其中 ${unanchored} 条未能精确定位，已挂在所在段落开头。` : ''
    if (!unanchored) ElMessage.success(`已生成批注版，共 ${total} 条批注`)
    savedTick.value++
  } catch (e) { exportError(e) } finally { exporting.value = '' }
}
async function exportRevised(seq: number) {
  if (!task.value || !run.value || exporting.value || deleting.value || exportReason.value) return
  if (!['completed', 'partial'].includes(run.value.status)
    || !task.value.files.some(file => file.seq === seq && file.annotatable && !file.has_revisions)) return
  const runId = run.value.id
  const taskId = task.value.id
  try {
    await ElMessageBox.confirm('修订版按已采纳的问题改写条款，请在 Word 中逐条核对后接受或拒绝；文件中如有批注，发给对方前请先删除。', '导出修订版', { type: 'warning', confirmButtonText: '导出修订版', cancelButtonText: '取消' })
  } catch { return }
  exporting.value = '修订版'
  try {
    const { saveResult, revised, commented } = await reportApi.revised(runId, seq)
    if (saveResult === 'cancelled' || task.value?.id !== taskId || run.value?.id !== runId) return
    annotationNotice.value = commented > 0 ? `共 ${revised} 处修订；另有 ${commented} 条已采纳的问题未能写成修订，已作为批注附上，请人工修改。` : ''
    if (!commented) ElMessage.success(`已生成修订版，共 ${revised} 处修订`)
    savedTick.value++
  } catch (e) { exportError(e) } finally { exporting.value = '' }
}

async function remove() {
  if (!task.value || deleting.value) return
  const current = task.value
  try {
    await ElMessageBox.confirm(`确定彻底删除“${current.title}”吗？原件、脱敏记录和全部轮次的审核结果都会被删除，无法恢复。`, '删除审核任务',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' })
  } catch { return }
  deleting.value = true
  try {
    await tasksApi.remove(current.id)
    ElMessage.success('已删除')
    await router.push({ name: 'history' })
  } catch (e) {
    ElMessage.error(e instanceof ApiError ? e.status === 404 ? '任务不存在或已被删除' : e.message : '删除失败，请重试')
  } finally { deleting.value = false }
}

// ---- 两栏宽度（3.1）：问题栏 clamp(480px, 42%, 640px)，≥ 400px；画布 ≥ 420px ----
const RAIL_KEY = 'si.review.findingsWidth'
const HANDLE = 8, GAP = 12, RAIL_MIN = 400, CANVAS_MIN = 420
const splitEl = ref<HTMLElement>()
const containerWidth = ref(0)
const storedRail = ref<number | null>(null)
const railMax = computed(() => Math.max(RAIL_MIN, containerWidth.value - HANDLE - GAP * 2 - CANVAS_MIN))
const railDefault = computed(() => Math.min(railMax.value, Math.max(RAIL_MIN, Math.min(640, Math.max(480, Math.round(containerWidth.value * 0.42))))))
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
onBeforeUnmount(() => resizeObserver?.disconnect())
onMounted(() => {
  const saved = readStoredWidth(RAIL_KEY, 0, 100000, 0)
  storedRail.value = saved > 0 ? saved : null
  void load()
})
</script>

<template>
  <div class="page page--split">
    <el-alert v-if="error" type="error" show-icon :closable="false" :title="error" />
    <template v-else-if="task">
      <ResultToolbar :task="task" :run="run" :rounds="rounds" :selected-run="selectedRun" :req-version="reqVersion" :warnings="expiryWarnings"
        :export-reason="exportReason" :exporting="exporting" :saved-tick="savedTick" :deleting="deleting"
        @select-run="selectRun" @export-report="exportReport" @export-annotated="exportAnnotated" @export-revised="exportRevised" @remove="remove">
        <template #before-export><ChatPanel ref="chat" :key="task.id" :task-id="task.id" :disabled="deleting" /></template>
        <el-button v-if="task.status === 'reviewed' && task.policy === 'ai' && !task.files.some((f) => f.original_deleted_at)" :disabled="deleting" @click="rerunOpen = true"><template #icon><RotateCw :size="16" :stroke-width="1.75" /></template>重新审核</el-button>
      </ResultToolbar>
      <el-alert v-if="annotationNotice" type="warning" show-icon closable :title="annotationNotice" class="annotation-notice" @close="annotationNotice = ''" />
      <div :ref="bindSplit" class="split">
        <div class="canvas-col">
          <ResultCanvas ref="canvas" :task="task" :note="run ? run.user_note : task.user_note" :annotations="annotations" :local-locs="localLocs"
            :selected-finding-id="selectedId" :raw-highlight="rawHighlight" :ask-enabled="askEnabled"
            @select="onCanvasSelect" @ask="ask" />
        </div>
        <SplitHandle v-model="railWidth" reverse :min="RAIL_MIN" :max="railMax" :default-value="railDefault" :storage-key="RAIL_KEY" label="调整问题栏宽度" />
        <div class="rail-col" :style="{ flexBasis: `${railWidth}px` }">
          <FindingsRail v-model:severity="fSeverity" v-model:category="fCategory" v-model:handling="fHandling" v-model:zone="zone"
            :task-id="task.id" :run="run" :run-loading="runLoading" :run-error="runError" :local-only="localOnly" :findings="findings"
            :selected-id="selectedId" :ask-enabled="askEnabled" :local-loader="localLoader" :fresh="fresh"
            @select="onSelect" @local-select="onLocalSelect" @updated="onUpdated" @ask="ask" @ai-notice="onAiNotice" />
        </div>
      </div>
      <ReRunDialog v-model="rerunOpen" :task="task" />
    </template>
    <div v-else-if="loading" class="split">
      <div class="canvas-col"><div class="blank-paper si-paper" /></div>
      <div class="rail-col skeleton"><el-skeleton :rows="8" animated /></div>
    </div>
  </div>
</template>

<style scoped>
.page { height: 100%; box-sizing: border-box; overflow: hidden; display: flex; flex-direction: column; padding-top: 0; padding-bottom: 0; }
.split { display: flex; flex: 1; min-height: 0; gap: 12px; padding: 12px 0 16px; }
.canvas-col { flex: 1; min-width: 420px; min-height: 0; display: flex; flex-direction: column; }
.rail-col { flex: 0 0 auto; min-width: 400px; min-height: 0; }
.rail-col.skeleton { flex-basis: 480px; padding: 16px; }
.blank-paper { flex: 1; max-width: 760px; width: 100%; margin: 20px auto; border-radius: var(--si-radius-paper); box-shadow: var(--si-paper-shadow); }
.annotation-notice { flex: none; margin-top: 8px; }
</style>
