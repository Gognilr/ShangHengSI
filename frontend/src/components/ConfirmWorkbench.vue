<script setup lang="ts">
import { ChevronDown, ChevronRight, Ellipsis, Plus } from '@lucide/vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { ApiError } from '@/api/http'
import { assistantApi } from '@/api/assistant'
import { localFindingsApi } from '@/api/localFindings'
import {
  REDACTION_ERROR_MESSAGES,
  redactionApi,
  type ManualInput,
  type Party,
  type RedactionItemPos,
  type RedactionInfo,
  type Suspect,
  type SuspectCategory,
  type UserCategory,
} from '@/api/redaction'
import { RUN_ERROR_MESSAGES, runsApi } from '@/api/runs'
import { MAX_FILES, TASK_ERROR_MESSAGES, checkFiles, tasksApi, type TaskDetail, type TaskFile } from '@/api/tasks'
import LocalCheckList from '@/components/result/LocalCheckList.vue'
import LeakPanel from '@/components/LeakPanel.vue'
import ConfirmCanvas from '@/components/ConfirmCanvas.vue'
import RequirementsDrawer from '@/components/RequirementsDrawer.vue'
import SplitHandle, { readStoredWidth } from '@/components/SplitHandle.vue'
import SiCount from '@/components/ui/SiCount.vue'
import SiIsland from '@/components/ui/SiIsland.vue'
import { usePendingDeletes } from '@/composables/usePendingDeletes'
import { useTaskPoll } from '@/composables/useTaskPoll'
import { useHotkeys } from '@/utils/hotkeys'
import { DOC_TYPE_OPTIONS, KEPT_BY, MANUAL_CATEGORY_OPTIONS, REDACTION_CATEGORY, docTypeSourceLabel, suspectReasonLabel, toOptions } from '@/utils/labels'
import { toast } from '@/utils/toast'

const props = withDefaults(defineProps<{ task: TaskDetail; modelId?: number; modelName?: string; mode?: 'review' | 'assistant' }>(), { mode: 'review' })
const emit = defineEmits<{
  /** 已提交确认发送，父页面应刷新任务状态 */
  (e: 'confirmed'): void
  /** 任务状态已变化（如 409），父页面应刷新任务 */
  (e: 'stale'): void
}>()
const assistant = computed(() => props.mode === 'assistant')
const nodeLoader = computed(() => assistant.value ? assistantApi.nodes : undefined)
const docTypeOptions = toOptions(DOC_TYPE_OPTIONS)
const categoryOptions = toOptions(MANUAL_CATEGORY_OPTIONS)
// 类型选项取 SPEC 9.4；国家的建议项是当前识别值和“国家未知”，也可自行输入（Q-50 ②）
const ORG_TYPES = ['贸易商', '货代', '银行', '类型未知']
const UNKNOWN_COUNTRY = '国家未知'

// ---- 数据 ----
const files = ref<TaskFile[]>(props.task.files)
const hidden = ref<Set<string>>(new Set())
const visibleFiles = computed(() => files.value.filter(f => !hidden.value.has(f.id)))
const parties = ref<Party[]>([])
const summary = ref<Record<string, number>>({})
const suspects = ref<Suspect[]>([])
const redactedItems = ref<RedactionInfo['items']>([])
const outboundText = ref('')
const outboundNote = ref<string | null>(null)
const outboundLoading = ref(false)
const outboundError = ref('')
const blockedReason = ref('')
/** 出口复检拦截清单（V31-U5F-02）：提交被拦或上次轮次被拦时显示 */
const leaksShown = ref(false)
const leakPanel = ref<InstanceType<typeof LeakPanel>>()
function showLeaks(summary: string) {
  blockedReason.value = summary
  leaksShown.value = true
  void nextTick(() => leakPanel.value?.reload())
}
const localCount = ref<number | null>(null)
const loadLocal = () => localFindingsApi.list(props.task.id)
const loading = ref(true)
const loadError = ref('')
const submitting = ref(false)
const submitError = ref('')
const skipTemplate = ref(false)
const mainDocType = computed(() => files.value.find(f => f.is_main)?.doc_type ?? null)

function applyRedaction(info: RedactionInfo) {
  summary.value = info.summary
  suspects.value = info.suspects
  redactedItems.value = info.items
}
async function loadSummary() { applyRedaction(await redactionApi.redaction(props.task.id)) }
async function loadOutbound() {
  outboundLoading.value = true
  outboundError.value = ''
  try {
    const outbound = await redactionApi.outbound(props.task.id)
    outboundText.value = outbound.text
    outboundNote.value = outbound.note
  } catch (e) {
    outboundText.value = ''
    outboundNote.value = null
    outboundError.value = e instanceof ApiError ? e.message : '加载失败'
  } finally { outboundLoading.value = false }
}
async function loadBlocked() {
  const run = props.task.latest_run
  if (run?.status !== 'blocked') return
  try { showLeaks((await runsApi.get(run.id)).error_message ?? '') }
  catch { showLeaks('') }
}
async function loadLocalCount() {
  if (assistant.value) return
  try { localCount.value = (await localFindingsApi.list(props.task.id)).items.length } catch { localCount.value = null }
}
async function loadAll(silent = false) {
  if (!silent) loading.value = true
  loadError.value = ''
  try {
    const [p] = await Promise.all([redactionApi.parties(props.task.id), loadSummary(), loadBlocked(), loadOutbound(), loadLocalCount()])
    parties.value = p.items
  } catch (e) {
    loadError.value = e instanceof ApiError ? e.message : '加载失败，请刷新页面'
  } finally { loading.value = false }
}
onMounted(() => void loadAll())

/** 拦截清单处理成功后：以返回值刷新，并重读签约方（可能出现新的方）和外发文本。 */
async function afterChange(info: RedactionInfo) {
  applyRedaction(info)
  if (leaksShown.value) void leakPanel.value?.reload()
  try {
    const [p] = await Promise.all([redactionApi.parties(props.task.id), loadOutbound()])
    parties.value = p.items
  } catch (e) { handleError(e) }
}

// ---- 保存队列（V31-U5F-03，设计 1.2 第 1 条）----
// 改我方、处理不确定项、手动补充与撤销：界面先按预期变化，请求按顺序在后台保存，不锁页面。
// 队列清空时用最后一次返回值与服务器对齐；任何一次失败都整体回读服务器状态（即回滚）并提示。
// 只有“确认发送”等队列清空。
const saving = ref(0)
let tail: Promise<void> = Promise.resolve()
let latest: RedactionInfo | null = null
let partiesTouched = false
let failed: unknown = null
function enqueue<T>(save: () => Promise<T>, done?: (r: T) => void): Promise<void> {
  saving.value += 1
  submitError.value = ''
  tail = tail.then(async () => {
    try { done?.(await save()) } catch (e) { failed ??= e }
    if (saving.value === 1) await settle()
    saving.value -= 1
  })
  return tail
}
async function settle() {
  const error = failed
  const info = latest
  const touched = partiesTouched
  failed = null
  latest = null
  partiesTouched = false
  try {
    const [p] = await Promise.all([
      redactionApi.parties(props.task.id),
      error || !info || touched ? loadSummary() : Promise.resolve(applyRedaction(info)),
      loadOutbound(),
    ])
    parties.value = p.items
    if (leaksShown.value) void leakPanel.value?.reload()
  } catch { /* 网络中断：保持当前显示，下一次操作或刷新时对齐 */ }
  if (error) {
    handleError(error)
    ElMessage.error({ message: `保存失败，已恢复为服务器上的结果${submitError.value ? `：${submitError.value}` : ''}`, duration: 6000 })
  }
}
const decide = (save: () => Promise<RedactionInfo>) => enqueue(save, (info) => { latest = info })
/** 乐观改写不确定项的显示状态（保留 / 改回替换），代号与位置等服务器返回后对齐。 */
function patchSuspects(ids: number[], kept: boolean, category?: string) {
  const set = new Set(ids)
  const placeholders = new Set<string>()
  suspects.value = suspects.value.map((s) => {
    if (!set.has(s.id)) return s
    if (kept && s.placeholder) placeholders.add(s.placeholder)
    return kept
      ? { ...s, decision: 'ignore', kept_by: 'user' as const, placeholder: null }
      : { ...s, decision: 'pending', kept_by: null, category: category ?? s.category }
  })
  // 保留：画布上这些不确定项的胶囊立即还原为原文
  if (placeholders.size) redactedItems.value = redactedItems.value.filter(it => !(it.detected_by === 'uncertain' && placeholders.has(it.placeholder)))
}
function handleError(e: unknown) {
  if (e instanceof ApiError) {
    const base = TASK_ERROR_MESSAGES[e.code] ?? RUN_ERROR_MESSAGES[e.code] ?? REDACTION_ERROR_MESSAGES[e.code] ?? e.message
    // 出口复检拦截：改为逐条列出的拦截清单，可定位、就地处理
    if (e.code === 'OUTBOUND_BLOCKED') { submitError.value = ''; showLeaks(e.message); return }
    submitError.value = base
    if (e.code === 'TASK_STATE_CONFLICT') emit('stale')
  } else submitError.value = '操作失败，请重试'
}

// ---- 文件增删与重新计算（3.4、3.5） ----
const canEditFiles = computed(() => !assistant.value && !props.task.latest_run)
const waiting = ref(props.task.busy)
const doneFlash = ref(false)
const taskIdRef = computed(() => props.task.id)
const { task: polled } = useTaskPoll(taskIdRef, { active: waiting })
watch(polled, async (t) => {
  if (!t || !waiting.value || t.busy) return
  files.value = t.files
  waiting.value = false
  await loadAll(true)
  doneFlash.value = true
  setTimeout(() => { doneFlash.value = false }, 2000)
})
const island = computed<{ state: 'busy' | 'done'; text: string } | null>(() => {
  if (waiting.value) return { state: 'busy', text: '正在加入文件并重新计算…' }
  if (doneFlash.value) return { state: 'done', text: '已更新' }
  return null
})
const pendingDeletes = usePendingDeletes()
const picker = ref<HTMLInputElement>()
const dragging = ref(false)
let dragDepth = 0
const fileError = ref('')

async function addFiles(list: File[]) {
  if (!canEditFiles.value || waiting.value || !list.length) return
  fileError.value = ''
  if (visibleFiles.value.length + list.length > MAX_FILES) { fileError.value = `每个任务最多 ${MAX_FILES} 个文件`; return }
  const problem = checkFiles(list)
  if (problem) { fileError.value = problem; return }
  try {
    files.value = (await tasksApi.addFiles(props.task.id, list)).files
    waiting.value = true
  } catch (e) { fileError.value = e instanceof ApiError ? (TASK_ERROR_MESSAGES[e.code] ?? e.message) : '上传失败，请重试' }
}
function onPick(event: Event) {
  const input = event.target as HTMLInputElement
  const list = Array.from(input.files ?? [])
  input.value = ''
  void addFiles(list)
}
const hasFiles = (e: DragEvent) => !!e.dataTransfer && Array.from(e.dataTransfer.types).includes('Files')
function onDragEnter(e: DragEvent) { if (canEditFiles.value && hasFiles(e)) { dragDepth += 1; dragging.value = true } }
function onDragLeave(e: DragEvent) { if (canEditFiles.value && hasFiles(e)) { dragDepth = Math.max(0, dragDepth - 1); if (!dragDepth) dragging.value = false } }
function onDrop(e: DragEvent) {
  dragDepth = 0
  dragging.value = false
  if (canEditFiles.value) void addFiles(Array.from(e.dataTransfer?.files ?? []))
}
function unhide(id: string) {
  const next = new Set(hidden.value)
  next.delete(id)
  hidden.value = next
}
function removeFile(file: TaskFile) {
  if (visibleFiles.value.length <= 1) { fileError.value = TASK_ERROR_MESSAGES.LAST_FILE ?? ''; return }
  hidden.value = new Set(hidden.value).add(file.id)
  pendingDeletes.schedule(file.id, async ({ keepalive }) => {
    const next = await tasksApi.removeFile(props.task.id, file.id, { keepalive })
    unhide(file.id)
    files.value = next.files
    waiting.value = true
  }, {
    label: `已删除 ${file.original_name}`,
    onUndo: () => unhide(file.id),
    onError: (e) => { unhide(file.id); fileError.value = e instanceof ApiError ? (TASK_ERROR_MESSAGES[e.code] ?? e.message) : '删除失败，请重试' },
  })
}
async function setMain(file: TaskFile) {
  try {
    await tasksApi.setMain(props.task.id, file.id)
    files.value = files.value.map(f => ({ ...f, is_main: f.id === file.id }))
    waiting.value = true
  } catch (e) { fileError.value = e instanceof ApiError ? (TASK_ERROR_MESSAGES[e.code] ?? e.message) : '设置失败，请重试' }
}
async function changeDocType(fileId: string, docType: string) {
  try {
    const updated = await tasksApi.setFileDocType(props.task.id, fileId, docType)
    files.value = files.value.map(f => (f.id === fileId ? updated : f))
    ElMessage.success('文件类型已修改')
    await loadOutbound()
  } catch (e) {
    handleError(e)
    files.value = [...files.value]
  }
}
function fileMeta(f: TaskFile): string {
  return `${f.page_count ? `${f.page_count} 页，` : ''}${f.paragraph_count} 段，${f.table_count} 个表格`
}
function fileBadge(name: string): string {
  const ext = name.split('.').pop()?.toLowerCase() ?? ''
  if (['docx', 'doc', 'wps', 'rtf'].includes(ext)) return 'DOC'
  if (['xlsx', 'xls', 'et'].includes(ext)) return 'XLS'
  if (ext === 'pdf') return 'PDF'
  return 'IMG'
}
const parsingFiles = computed(() => visibleFiles.value.some(f => f.parse_state === 'parsing'))
const failedFiles = computed(() => visibleFiles.value.some(f => f.parse_state === 'failed'))

// ---- 签约方 ----
const ourCount = computed(() => parties.value.filter(p => p.is_our_party).length)
const ourId = computed(() => parties.value.find(p => p.is_our_party)?.id ?? '')
const editingParty = ref('')
function countryOptions(p: Party): string[] {
  const set = new Set<string>()
  if (p.country) set.add(p.country)
  set.add(UNKNOWN_COUNTRY)
  return [...set]
}
/** 完整列表提交（后端要求）：界面先改、后台保存；队列清空后代号和外发文本随之对齐。 */
function saveParties(next: Party[]) {
  if (locked.value) return
  parties.value = next
  partiesTouched = true
  void enqueue(() => redactionApi.saveParties(props.task.id, next.map(p => ({
    id: p.id,
    is_our_party: p.is_our_party,
    country: (p.country ?? '').trim() || UNKNOWN_COUNTRY,
    org_type: (p.org_type ?? '').trim() || '类型未知',
  }))))
}
function setOur(id: string) { void saveParties(parties.value.map(p => ({ ...p, is_our_party: p.id === id }))) }
function setField(id: string, field: 'country' | 'org_type', value: string) {
  const v = value.trim()
  if (!v) return
  void saveParties(parties.value.map(p => (p.id === id ? { ...p, [field]: v } : p)))
}

// ---- 不确定项（3.2、3.3） ----
const isKept = (s: Suspect) => s.decision === 'ignore' || !!s.kept_by
const keptCount = computed(() => suspects.value.filter(isKept).length)
const memoryKept = computed(() => suspects.value.filter(s => s.kept_by === 'memory' || s.kept_by === 'vector').length)
const suspectSummary = computed(() => {
  const total = suspects.value.length
  if (!total) return '无'
  const extra = memoryKept.value ? `（其中 ${memoryKept.value} 组按记忆或本机判断保留）` : ''
  return `${total} 组 · 已替换 ${total - keptCount.value} · 保留 ${keptCount.value}${extra}`
})
const suspectsOpen = ref(false)
const selectedId = ref<number | null>(null)
const checked = ref<number[]>([])
// 后台保存不锁页面；只有加文件重新计算期间锁住
const locked = computed(() => waiting.value)
const canvas = ref<InstanceType<typeof ConfirmCanvas>>()
watch(suspects, (rows) => {
  const ids = new Set(rows.map(s => s.id))
  checked.value = checked.value.filter(id => ids.has(id))
  if (selectedId.value !== null && !ids.has(selectedId.value)) selectedId.value = null
})
const allChecked = computed(() => suspects.value.length > 0 && checked.value.length === suspects.value.length)
function checkAll(value: string | number | boolean) { checked.value = value ? suspects.value.map(s => s.id) : [] }
const keepIds = computed(() => suspects.value.filter(s => checked.value.includes(s.id) && !isKept(s)).map(s => s.id))
const resetIds = computed(() => suspects.value.filter(s => checked.value.includes(s.id) && isKept(s)).map(s => s.id))
const overLimit = computed(() => keepIds.value.length > 200 || resetIds.value.length > 200)

function shortText(text: string): string { return Array.from(text).length > 12 ? `${Array.from(text).slice(0, 12).join('')}…` : text }
function resetOne(s: Suspect) {
  if (locked.value) return
  patchSuspects([s.id], false)
  void decide(() => redactionApi.decideSuspect(props.task.id, s.id, 'reset', null))
}
function keepOne(s: Suspect) {
  if (locked.value || isKept(s)) return
  patchSuspects([s.id], true)
  void decide(() => redactionApi.decideSuspect(props.task.id, s.id, 'ignore', null))
  toast(`已保留“${shortText(s.text)}”`, { action: { label: '撤销', onClick: () => resetOne(s) } })
}
function replaceOne(s: Suspect, category?: SuspectCategory) {
  if (locked.value) return
  const cat = category ?? (s.category as SuspectCategory | null) ?? 'ORG'
  patchSuspects([s.id], false, cat)
  void decide(() => redactionApi.decideSuspect(props.task.id, s.id, 'replace', cat))
}
/** T 键与行内按钮：保留中的改回替换（用户保留 → reset；记忆或本机判断保留 → 按默认类别替换）。 */
function restoreOne(s: Suspect) {
  if (!isKept(s)) return
  if (s.kept_by === 'memory' || s.kept_by === 'vector') replaceOne(s)
  else resetOne(s)
}
async function keepChecked() {
  const ids = [...keepIds.value]
  if (!ids.length || ids.length > 200) return
  try {
    await ElMessageBox.confirm(`所选 ${ids.length} 组将按原文发送给模型。请确认其中没有公司名、人名等敏感信息。`, '保留所选', { type: 'warning', confirmButtonText: '保留原文', cancelButtonText: '取消' })
  } catch { return }
  if (locked.value) return
  patchSuspects(ids, true)
  void decide(() => redactionApi.batchSuspects(props.task.id, ids, 'ignore'))
}
function resetChecked() {
  const ids = [...resetIds.value]
  if (locked.value || !ids.length || ids.length > 200) return
  patchSuspects(ids, false)
  void decide(() => redactionApi.batchSuspects(props.task.id, ids, 'reset'))
}
function selectSuspect(s: Suspect) {
  selectedId.value = s.id
  void canvas.value?.locate(s.loc, s.text)
}
async function stepSuspect(delta: 1 | -1) {
  const list = suspects.value
  if (!list.length) return
  const index = list.findIndex(s => s.id === selectedId.value)
  const next = list[index < 0 ? (delta > 0 ? 0 : list.length - 1) : (index + delta + list.length) % list.length]!
  suspectsOpen.value = true
  selectSuspect(next)
  await nextTick()
  document.querySelector(`[data-sid="${next.id}"]`)?.scrollIntoView({ block: 'nearest' })
}
const selectedSuspect = computed(() => suspects.value.find(s => s.id === selectedId.value))
const liveText = computed(() => {
  const s = selectedSuspect.value
  if (!s) return ''
  const index = suspects.value.findIndex(item => item.id === s.id) + 1
  return `第 ${index} 组，共 ${suspects.value.length} 组：${s.text}，${isKept(s) ? '保留原文' : '已替换'}`
})
/** 划选补充：画布上立即出现胶囊（代号等服务器分配），后台保存。 */
function onManual(input: ManualInput) {
  if (locked.value) return
  redactedItems.value = [...redactedItems.value, { ...input, placeholder: '…', can_revoke: false, detected_by: 'manual' }]
  void decide(() => redactionApi.manual(props.task.id, input))
}
/** 撤销误识别：先去掉这一处胶囊，保存后同一文字在全文恢复原样。 */
function onRevoke(item: RedactionItemPos) {
  if (locked.value) return
  redactedItems.value = redactedItems.value.filter(it => it !== item)
  void decide(() => redactionApi.revoke(props.task.id, { loc: item.loc, char_start: item.char_start, char_end: item.char_end }))
}

// ---- 外发回执 ----
const replacedTotal = computed(() => Object.values(summary.value).reduce((sum, n) => sum + n, 0))
const categoryChips = computed(() => Object.entries(summary.value).filter(([, n]) => n > 0).map(([code, n]) => `${REDACTION_CATEGORY[code] ?? code} ${n}`))
const outboundLength = computed(() => outboundText.value.length)
const reqDrawer = ref(false)
const localDrawer = ref(false)
const outboundDrawer = ref(false)
// 后台保存中也可以点；点击后先等保存队列清空，有失败则停在本页（已回滚并提示）
const canConfirm = computed(() =>
  (assistant.value ? ourCount.value <= 1 : ourCount.value === 1)
  && !loading.value && !waiting.value && !parsingFiles.value && !failedFiles.value)
const confirmBox = ref<HTMLElement>()
const confirmFlash = ref(false)
// Ctrl+Enter 只聚焦按钮（3.6）。部分环境会把同一次按键再作为 Enter 送到新聚焦的按钮上，
// 聚焦后 600ms 内的点击一律忽略，保证快捷键本身永不外发（V31-U2-05 曾误发一次）。
let focusedByHotkeyAt = 0

async function confirmSend() {
  if (!canConfirm.value || Date.now() - focusedByHotkeyAt < 600) return
  submitError.value = ''
  submitting.value = true
  try {
    // 先等后台保存全部完成；有失败时界面已回滚并提示，停在本页
    if (saving.value) {
      await tail
      if (submitError.value || !canConfirm.value) return
    }
    // 撤销期内删除的文件仍在服务器上：先真删并等重算完成，用户看到更新后的回执再确认
    if (pendingDeletes.size()) { await pendingDeletes.flush(); return }
    if (assistant.value) await assistantApi.confirm(props.task.id)
    else await runsApi.create(props.task.id, !skipTemplate.value, props.modelId)
    emit('confirmed')
  } catch (e) { handleError(e) }
  finally { submitting.value = false }
}

// ---- 宽度与键盘（3.1、3.6） ----
const RAIL_MIN = 340, RAIL_MAX = 480, RAIL_DEFAULT = 380, RAIL_KEY = 'si.confirm.rightWidth'
const railWidth = ref(readStoredWidth(RAIL_KEY, RAIL_MIN, RAIL_MAX, RAIL_DEFAULT))
const helpOpen = ref(false)
const HOTKEYS = [
  ['V', '切换“原文 / AI 视野”'],
  ['[ / ]', '上一个 / 下一个文件页签'],
  ['J / K', '下一个 / 上一个不确定项'],
  ['B', '保留选中的不确定项的原文'],
  ['T', '把选中的不确定项改回替换'],
  ['Ctrl + Enter', '把焦点移到“确认发送”按钮（再按 Enter 才发送）'],
  ['Esc', '关闭弹层、取消选中'],
  ['?', '显示本说明'],
]
useHotkeys({
  v: () => canvas.value?.toggleView(),
  '[': () => canvas.value?.switchFile(-1),
  ']': () => canvas.value?.switchFile(1),
  j: () => void stepSuspect(1),
  k: () => void stepSuspect(-1),
  b: () => { const s = selectedSuspect.value; if (s) void keepOne(s) },
  t: () => { const s = selectedSuspect.value; if (s) void restoreOne(s) },
  'ctrl+enter': () => {
    focusedByHotkeyAt = Date.now()
    confirmBox.value?.querySelector('button')?.focus()
    confirmFlash.value = true
    setTimeout(() => { confirmFlash.value = false }, 700)
  },
  escape: () => { helpOpen.value = false; selectedId.value = null; canvas.value?.cancel() },
  '?': () => { helpOpen.value = true },
}, { enabled: computed(() => !assistant.value) })
</script>

<template>
  <div class="wb" @dragenter.prevent="onDragEnter" @dragover.prevent @dragleave="onDragLeave" @drop.prevent="onDrop">
    <div class="canvas">
      <ConfirmCanvas ref="canvas" :ai-view="true" :node-loader="nodeLoader" :task-id="task.id" :files="visibleFiles" :items="redactedItems" :suspects="suspects" :note="task.user_note" :selected-suspect-id="selectedId" :locked="locked" :protected-count="replacedTotal" @manual="onManual" @revoke="onRevoke" @keep="keepOne" @restore="restoreOne" @replace="replaceOne">
        <template #bar>
          <SiIsland v-if="island" :state="island.state" :text="island.text" />
          <span v-else-if="saving" class="hint saving" aria-live="polite">正在后台保存…</span>
          <button v-if="!assistant" type="button" class="hint keys-link" @click="helpOpen = true">按 ? 查看快捷键</button>
        </template>
      </ConfirmCanvas>
      <el-alert v-if="outboundError" type="error" show-icon :closable="false" :title="outboundError" />
      <div v-if="dragging" class="drop-veil" aria-hidden="true">松开加入本任务</div>
    </div>
    <SplitHandle v-model="railWidth" reverse :min="RAIL_MIN" :max="RAIL_MAX" :default-value="RAIL_DEFAULT" :storage-key="RAIL_KEY" label="调整回执栏宽度" />
    <aside v-loading="loading" class="rail" :style="{ flexBasis: `${railWidth}px` }">
      <div class="rail-scroll">
        <el-alert v-if="loadError" type="error" show-icon :closable="false" :title="loadError" />
        <LeakPanel v-if="leaksShown" ref="leakPanel" :task-id="task.id" :files="files" :locked="locked || saving > 0" :summary="blockedReason" @locate="(loc, text) => canvas?.locate(loc, text)" @changed="afterChange" />

        <section class="sec">
          <h3>签约方与我方<span class="sum">{{ parties.length ? `${parties.length} 方` : '' }}</span></h3>
          <p v-if="assistant" class="hint">会话可以不设置我方，最多选择一方。<el-button link :disabled="waiting" @click="setOur('')">不设我方</el-button></p>
          <p v-if="!loading && !parties.length" class="warn">未识别到签约方：在原文中选中我方名称、类别选“公司”后再设为我方</p>
          <ul class="rows">
            <li v-for="p in parties" :key="p.id" class="party" :class="{ our: p.is_our_party }">
              <div class="party-line">
                <el-radio :model-value="ourId" :value="p.id" :disabled="waiting" :aria-label="`设 ${p.name} 为我方`" @change="setOur(p.id)"><span class="sr-only">我方</span></el-radio>
                <span class="party-name" :title="p.name">{{ p.name }}</span>
                <span class="code">{{ p.code }}</span>
                <span class="hint">{{ p.role_label }}</span>
                <el-button link type="primary" @click="editingParty = editingParty === p.id ? '' : p.id">{{ editingParty === p.id ? '收起' : '编辑' }}</el-button>
              </div>
              <div v-if="editingParty === p.id" class="party-edit">
                <el-select :model-value="p.country ?? UNKNOWN_COUNTRY" filterable allow-create default-first-option size="small" :disabled="waiting" aria-label="国家" @change="(v: string) => setField(p.id, 'country', v)"><el-option v-for="c in countryOptions(p)" :key="c" :label="c" :value="c" /></el-select>
                <el-select :model-value="p.org_type ?? '类型未知'" filterable allow-create default-first-option size="small" :disabled="waiting" aria-label="类型" @change="(v: string) => setField(p.id, 'org_type', v)"><el-option v-for="t in ORG_TYPES" :key="t" :label="t" :value="t" /></el-select>
              </div>
            </li>
          </ul>
        </section>

        <section class="sec">
          <h3 class="clickable" role="button" tabindex="0" :aria-expanded="suspectsOpen" @click="suspectsOpen = !suspectsOpen" @keydown.enter="suspectsOpen = !suspectsOpen">
            <component :is="suspectsOpen ? ChevronDown : ChevronRight" :size="16" :stroke-width="1.75" />不确定项<span class="sum">{{ suspectSummary }}</span>
          </h3>
          <template v-if="suspectsOpen">
            <p v-if="!suspects.length" class="hint">未发现不确定项</p>
            <template v-else>
              <div class="batch">
                <el-checkbox :model-value="allChecked" :disabled="locked" @change="checkAll">全选</el-checkbox>
                <el-button size="small" :disabled="locked || !keepIds.length || overLimit" @click="keepChecked">保留所选（{{ keepIds.length }} 组）</el-button>
                <el-button size="small" :disabled="locked || !resetIds.length || overLimit" @click="resetChecked">改回替换（{{ resetIds.length }} 组）</el-button>
              </div>
              <p v-if="overLimit" class="warn">每次最多处理 200 组，请减少选择</p>
              <ul class="rows suspects">
                <li v-for="s in suspects" :key="s.id" :data-sid="s.id" class="suspect" :class="{ selected: s.id === selectedId }" @click="selectSuspect(s)">
                  <el-checkbox v-model="checked" :value="s.id" :disabled="locked" :aria-label="`选择：${s.text}`" @click.stop />
                  <span class="suspect-main">
                    <span class="suspect-text" :title="s.text">{{ s.text }}</span>
                    <span class="hint">{{ suspectReasonLabel(s.reason) }}<template v-if="s.occurrences > 1"> · {{ s.occurrences }} 处</template></span>
                  </span>
                  <span v-if="isKept(s)" class="state kept">{{ KEPT_BY[s.kept_by ?? 'user'] }}</span>
                  <span v-else class="state"><span class="code">{{ s.placeholder ?? '已替换' }}</span></span>
                  <span class="suspect-actions" @click.stop>
                    <el-button v-if="!isKept(s)" link type="primary" :disabled="locked" @click="keepOne(s)">保留原文</el-button>
                    <el-button v-else link type="primary" :disabled="locked" @click="restoreOne(s)">{{ s.kept_by === 'user' ? '改回替换' : '替换' }}</el-button>
                    <el-dropdown v-if="!isKept(s)" trigger="click" :disabled="locked" @command="(c: UserCategory) => replaceOne(s, c)">
                      <el-button link :disabled="locked">指定类别…</el-button>
                      <template #dropdown><el-dropdown-menu><el-dropdown-item v-for="o in categoryOptions" :key="o.value" :command="o.value">{{ o.label }}</el-dropdown-item></el-dropdown-menu></template>
                    </el-dropdown>
                  </span>
                </li>
              </ul>
            </template>
          </template>
          <div class="sr-only" aria-live="polite">{{ liveText }}</div>
        </section>

        <section class="sec">
          <h3>文件<span class="sum">{{ visibleFiles.length }} 个</span></h3>
          <ul class="rows">
            <li v-for="f in visibleFiles" :key="f.id" class="file">
              <div class="file-line">
                <span class="badge" :class="`badge--${fileBadge(f.original_name).toLowerCase()}`">{{ fileBadge(f.original_name) }}</span>
                <span class="file-name" :title="f.original_name">{{ f.original_name }}<em v-if="f.is_main" class="main-tag">主文件</em></span>
                <el-dropdown v-if="canEditFiles" trigger="click" :disabled="waiting">
                  <button type="button" class="icon-btn" :aria-label="`${f.original_name} 的更多操作`"><Ellipsis :size="16" :stroke-width="1.75" /></button>
                  <template #dropdown><el-dropdown-menu><el-dropdown-item :disabled="f.is_main || f.parse_state !== 'done'" @click="setMain(f)">设为主文件</el-dropdown-item><el-dropdown-item :disabled="visibleFiles.length <= 1" @click="removeFile(f)">删除</el-dropdown-item></el-dropdown-menu></template>
                </el-dropdown>
              </div>
              <div class="file-line sub">
                <el-select class="doc-type" :disabled="assistant" :model-value="f.doc_type ?? undefined" placeholder="请选择" size="small" :aria-label="`${f.original_name} 的文件类型`" @change="(v: string) => changeDocType(f.id, v)"><el-option v-for="o in docTypeOptions" :key="o.value" :label="o.label" :value="o.value" /></el-select>
                <span class="hint">{{ docTypeSourceLabel(f.doc_type_source) }}</span>
              </div>
              <div class="hint">{{ fileMeta(f) }}<template v-if="f.parse_state === 'parsing'"> · 解析中</template></div>
              <div v-if="f.parse_state === 'failed'" class="warn">解析失败：{{ f.parse_error || '文件无法解析' }}</div>
              <div v-if="f.warnings.length" class="warn" :title="f.warnings.join('\n')">{{ f.warnings[0] }}<template v-if="f.warnings.length > 1">（共 {{ f.warnings.length }} 条）</template></div>
            </li>
          </ul>
          <template v-if="canEditFiles">
            <input ref="picker" type="file" multiple hidden accept=".docx,.pdf,.xlsx,.doc,.wps,.rtf,.xls,.et,.jpg,.jpeg,.png" @change="onPick">
            <el-button class="add-file" :disabled="waiting" @click="picker?.click()"><Plus :size="14" :stroke-width="1.75" />加入文件</el-button>
          </template>
          <p v-if="fileError" class="warn">{{ fileError }}</p>
        </section>

        <section v-if="!assistant" class="sec">
          <h3>本地校验 · 公司范本</h3>
          <p class="line">本地校验 {{ localCount ?? '—' }} 条 · <el-button link type="primary" @click="localDrawer = true">查看</el-button></p>
          <p v-if="task.template_suggestion" class="line">将对照公司范本：{{ task.template_suggestion.name }}<el-checkbox v-model="skipTemplate" :disabled="submitting" class="skip">不对照</el-checkbox></p>
          <el-button link type="primary" @click="reqDrawer = true">查看审核要求</el-button>
        </section>
      </div>

      <div class="receipt">
        <template v-if="!assistant">
          <h3 class="receipt-title">即将发送给 {{ modelName || '审核模型' }}</h3>
          <p class="hint">只发脱敏后的文字，结果在本机还原</p>
        </template>
        <h3 v-else class="receipt-title">本会话文件的脱敏结果</h3>
        <div class="stats">
          <div class="stat"><span class="stat-label">正文（只含代号）</span><SiCount :value="outboundLength" /></div>
          <div class="stat"><span class="stat-label">已在本机保护</span><span><SiCount :value="replacedTotal" /> 处</span></div>
        </div>
        <div v-if="categoryChips.length" class="chips"><span v-for="c in categoryChips" :key="c" class="chip">{{ c }}</span></div>
        <p class="hint">不发送：原件 · 图片 · 文件名</p>
        <p class="hint">确认后本机再做一次出口复检，命中即拦截、不发送</p>
        <el-button link type="primary" :disabled="!outboundText" @click="outboundDrawer = true">查看将发送的全文</el-button>
        <el-alert v-if="submitError" type="error" show-icon :closable="false" :title="submitError" class="receipt-alert" />
        <ul class="conditions" aria-label="提交条件" aria-live="polite">
          <li v-if="assistant" :class="{ met: ourCount <= 1 }">{{ ourCount ? '已选择一方为我方' : '未设置我方，可以确认脱敏' }}</li>
          <li v-else :class="{ met: ourCount === 1 }">{{ ourCount === 1 ? '✓ 已选择我方' : !parties.length ? '未识别到签约方，暂时不能提交 AI 审核' : '请先选择哪一方是我方' }}</li>
          <li v-if="waiting || parsingFiles">文件正在解析，请稍候</li>
          <li v-if="failedFiles">有解析失败的文件，请先删除</li>
          <li v-if="saving" class="met">修改正在后台保存，确认发送时会等保存完成</li>
          <li v-if="loading">正在读取脱敏信息……</li>
        </ul>
        <div ref="confirmBox" class="confirm-btn" :class="{ flash: confirmFlash }">
          <el-button type="primary" size="large" :disabled="!canConfirm" :loading="submitting" @click="confirmSend">{{ assistant ? '确认脱敏' : '确认发送并开始审核' }}</el-button>
        </div>
      </div>
    </aside>

    <RequirementsDrawer v-model="reqDrawer" :stance="task.stance" :source="task.source" :doc-type="mainDocType" :user-note="task.user_note" />
    <el-drawer v-model="localDrawer" title="本地校验结果" size="560px" append-to-body><LocalCheckList :loader="loadLocal" :load-key="task.id" @count="localCount = $event" /></el-drawer>
    <el-drawer v-model="outboundDrawer" title="将发送的全文" size="640px" append-to-body>
      <p v-if="outboundNote" class="hint">{{ outboundNote }}</p>
      <pre class="outbound">{{ outboundText }}</pre>
    </el-drawer>
    <el-dialog v-model="helpOpen" title="键盘快捷键" width="480px" append-to-body>
      <table class="keys"><tbody><tr v-for="[key, text] in HOTKEYS" :key="key"><th>{{ key }}</th><td>{{ text }}</td></tr></tbody></table>
    </el-dialog>
  </div>
</template>

<style scoped>
/* 工作台（V31-U5F-03 问题 2B）：高度固定为容器（可视区），左侧文档、右侧栏各自滚动。
   height: 100% 让块级容器（助手、会议纪要、翻译的全屏对话框）里同样固定高度，不再随文档拉伸 */
.wb { display: flex; flex: 1; height: 100%; min-height: 0; box-sizing: border-box; padding: 12px 0 20px; overflow: hidden; }
.canvas { position: relative; flex: 1; min-width: 0; min-height: 0; display: flex; flex-direction: column; gap: 8px; }
.canvas :deep(.stage) { padding-top: 2px; }
.keys-link { flex: none; padding: 0; border: 0; background: transparent; cursor: pointer; white-space: nowrap; }
.keys-link:hover { color: var(--el-color-primary); }
.saving { white-space: nowrap; }
.drop-veil { position: absolute; inset: 0; display: grid; place-items: center; border: 2px dashed var(--el-color-primary); border-radius: var(--si-radius-card); background: color-mix(in srgb, var(--el-color-primary-light-9) 85%, transparent); color: var(--el-color-primary); font: var(--si-font-h4); pointer-events: none; }
/* 右栏顶端与纸面顶端对齐：让出页签行（32px）、间距（8px）与纸面上方 2px；“即将发送”与确认按钮固定在栏底 */
.rail { flex: 0 0 auto; min-width: 0; min-height: 0; margin-top: 42px; display: flex; flex-direction: column; overflow: hidden; border: 1px solid var(--el-border-color-light); border-radius: var(--si-radius-card); background: var(--el-bg-color); }
.rail-scroll { flex: 1; min-height: 0; overflow-y: auto; padding: 4px 16px; display: flex; flex-direction: column; gap: 4px; }
.sec { padding: 12px 0; }
.sec + .sec { border-top: 1px solid var(--el-border-color-lighter); }
h3 { display: flex; align-items: center; gap: 8px; margin: 0 0 8px; font: var(--si-font-h4); }
h3.clickable { cursor: pointer; }
h3.clickable:focus-visible { outline: 2px solid var(--si-focus); outline-offset: 2px; }
.sum { margin-left: auto; color: var(--si-ink-3); font: var(--si-font-caption); text-align: right; }
.rows { margin: 0; padding: 0; list-style: none; display: flex; flex-direction: column; gap: 8px; }
.hint { margin: 0; }
.warn { margin: 4px 0 0; color: var(--el-color-warning); font: var(--si-font-caption); }
.line { margin: 0 0 4px; font: var(--si-font-body); }
.skip { margin-left: 12px; }
.code { padding: 0 6px; border-radius: 4px; background: var(--si-code-bg); color: var(--si-code-ink); font-family: var(--si-font-mono); font-size: 12px; }
.party { padding: 6px 8px; border-radius: 8px; border: 1px solid transparent; }
.party.our { background: var(--el-color-primary-light-9); border-color: var(--el-color-primary-light-7); }
.party-line { display: flex; align-items: center; gap: 8px; }
.party-line :deep(.el-radio) { margin: 0; }
.party-name { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.party-edit { display: flex; gap: 8px; margin-top: 6px; }
.party-edit :deep(.el-select) { flex: 1; min-width: 0; }
.batch { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin-bottom: 8px; }
.suspects { max-height: 320px; overflow-y: auto; }
.suspect { display: flex; align-items: center; gap: 8px; padding: 6px 8px; border: 1px solid var(--el-border-color-lighter); border-radius: 8px; cursor: pointer; }
.suspect.selected { outline: 2px solid var(--el-color-primary); outline-offset: -1px; }
.suspect-main { flex: 1; min-width: 0; display: flex; flex-direction: column; }
.suspect-text { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.state { flex: none; font: var(--si-font-caption); }
.state.kept { color: var(--si-ink-3); }
.suspect-actions { flex: none; display: flex; flex-direction: column; align-items: flex-end; }
.suspect-actions :deep(.el-button + .el-button), .suspect-actions :deep(.el-dropdown) { margin-left: 0; }
.file { padding: 8px; border: 1px solid var(--el-border-color-lighter); border-radius: 8px; display: flex; flex-direction: column; gap: 4px; }
.file-line { display: flex; align-items: center; gap: 8px; }
.file-line.sub { gap: 8px; }
.badge { flex: none; display: grid; place-items: center; width: 28px; height: 28px; border-radius: 6px; font-size: 11px; font-weight: 600; background: var(--el-color-primary-light-9); color: var(--el-color-primary); }
.badge--xls { background: var(--el-color-success-light-9); color: var(--el-color-success); }
.badge--pdf { background: var(--el-color-danger-light-9); color: var(--el-color-danger); }
.badge--img { background: var(--el-color-info-light-9); color: var(--el-color-info); }
.file-name { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.main-tag { margin-left: 6px; padding: 0 6px; border-radius: 4px; background: var(--el-color-primary-light-9); color: var(--el-color-primary); font: var(--si-font-label-sm); font-style: normal; }
.doc-type { flex: 1; min-width: 0; }
.icon-btn { display: grid; place-items: center; width: 28px; height: 28px; border: 0; border-radius: 6px; background: transparent; color: var(--si-ink-3); cursor: pointer; }
.icon-btn:hover { background: var(--si-surface-2); }
.add-file { margin-top: 8px; }
.receipt { flex: none; display: flex; flex-direction: column; gap: 6px; padding: 14px 16px 16px; border-top: 1px solid var(--el-border-color-light); border-radius: 0 0 var(--si-radius-card) var(--si-radius-card); background: var(--si-surface-2); }
.receipt-title { margin: 0; }
.stats { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
.stat { display: flex; flex-direction: column; gap: 2px; padding: 8px 10px; border-radius: 8px; background: var(--el-bg-color); font: var(--si-font-h4); }
.stat-label { color: var(--si-ink-3); font: var(--si-font-caption); }
.chips { display: flex; flex-wrap: wrap; gap: 4px; }
.chip { padding: 1px 8px; border-radius: 999px; background: var(--el-bg-color); color: var(--si-ink-2); font: var(--si-font-caption); }
.receipt-alert { margin-top: 4px; }
.conditions { margin: 0; padding: 0; list-style: none; font: var(--si-font-caption); color: var(--el-color-warning); }
.conditions .met { color: var(--si-ink-2); }
.confirm-btn :deep(.el-button) { width: 100%; height: 44px; }
.confirm-btn.flash :deep(.el-button) { box-shadow: 0 0 0 3px var(--si-focus); }
.outbound { white-space: pre-wrap; overflow-wrap: anywhere; font: var(--si-font-reading); }
.keys { width: 100%; border-collapse: collapse; }
.keys th { width: 130px; padding: 6px 0; text-align: left; font-family: var(--si-font-mono); font-weight: 600; }
.keys td { padding: 6px 0; }
.sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); }
</style>
