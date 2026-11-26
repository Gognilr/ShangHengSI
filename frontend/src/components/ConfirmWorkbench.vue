<script setup lang="ts">
import SiNotice from '@/components/ui/SiNotice.vue'
import { Ban, Bookmark, Check, ChevronDown, ChevronRight, CircleCheck, CircleDot, Ellipsis, Eye, FileText, Pencil, Plus, Send, Sparkle, Trash2, Users, X } from '@lucide/vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ApiError } from '@/api/http'
import { previewApi, type OcrCorrection } from '@/api/preview'
import { assistantApi, type DiffResult } from '@/api/assistant'
import { diffParts, substantive } from '@/utils/drafts'
import { localFindingsApi } from '@/api/localFindings'
import {
  REDACTION_ERROR_MESSAGES,
  redactionApi,
  type AutoKept,
  type ManualInput,
  type Party,
  type RedactionItemPos,
  type RedactionInfo,
  type Suspect,
  type SuspectCategory,
  type UserCategory,
} from '@/api/redaction'
import { RUN_ERROR_MESSAGES, runsApi, type RunCreated } from '@/api/runs'
import type { Stance } from '@/api/tasks'
import { MAX_FILES, TASK_ERROR_MESSAGES, checkFiles, tasksApi, type TaskDetail, type TaskFile } from '@/api/tasks'
import LocalCheckList from '@/components/result/LocalCheckList.vue'
import LeakPanel from '@/components/LeakPanel.vue'
import SiThinkingDots from '@/components/ui/SiThinkingDots.vue'
import ConfirmCanvas from '@/components/ConfirmCanvas.vue'
import RequirementsDrawer from '@/components/RequirementsDrawer.vue'
import SplitHandle, { readStoredWidth } from '@/components/SplitHandle.vue'
import SiCount from '@/components/ui/SiCount.vue'
import SiIsland from '@/components/ui/SiIsland.vue'
import SiSegmented from '@/components/ui/SiSegmented.vue'
import { usePendingDeletes } from '@/composables/usePendingDeletes'
import { useTaskPoll } from '@/composables/useTaskPoll'
import { useHotkeys } from '@/utils/hotkeys'
import { DOC_TYPE_OPTIONS, KEPT_BY, autoKeptReasonLabel, REDACTION_CATEGORY, docTypeSourceLabel, suspectReasonLabel, toOptions } from '@/utils/labels'
import { toast } from '@/utils/toast'
import { nudgeField } from '@/utils/nudge'

/**
 * mode：review 审核任务；assistant 会话只确认；session-review 会话的“全面审核”确认页（V321-E-06）——
 * 右栏底部由父页面经 settings 插槽放审核设置，主按钮为“确认发送并开始审核”，由 submit 完成确认 + 发起。
 */
const props = withDefaults(defineProps<{
  task: TaskDetail; modelId?: number; modelName?: string; mode?: 'review' | 'assistant' | 'session-review'; requireOurParty?: boolean
  /** session-review：当前立场与所选文件，用于取审核外发正文预览 */
  stance?: string; fileIds?: string[]; picked?: string[]; mainId?: string; scope?: 'full' | 'delta'
  /** session-review：父页面的提交（确认 + 发起）；抛出的 ApiError 按错误码在本页显示 */
  submit?: () => Promise<RunCreated | void>
  continueQuestion?: boolean
  allowOcr?: boolean
  /** session-review：父页面的未满足条件，非空时不能提交 */
  blocker?: string
  /** blocker 是用户还没选的项（立场、模型）：按钮保持可点，点击时发 nudge 由父页面定位（V33-C-01） */
  blockerChoice?: boolean
  /** session-review：多稿对比（V35-D-02），确认前就能看与上一稿的变化 */
  diffLoader?: (fileId: string, prevId: string) => Promise<DiffResult>
}>(), { mode: 'review', requireOurParty: false })
const emit = defineEmits<{
  (e: 'update:picked', ids: string[]): void
  (e: 'update:mainId', id: string): void
  (e: 'cancel-waiting'): void
  /** 当前我方变化（含首次加载） */
  (e: 'our', party: Party | null): void
  /** session-review：外发预览的材料版本，提交时作为 expected_material_version */
  (e: 'preview', version: number | null): void
  /** 已提交确认发送，父页面应刷新任务状态 */
  (e: 'confirmed'): void
  /** 任务状态已变化（如 409），父页面应刷新任务 */
  (e: 'stale'): void
  /** blockerChoice 时点了提交：父页面定位到缺的那一项 */
  (e: 'nudge'): void
  /** 发送前复检拦下、还没处理的处数（顶部步骤条显示） */
  (e: 'blocked', n: number): void
}>()
const sessionReview = computed(() => props.mode === 'session-review')
const assistant = computed(() => props.mode !== 'review')
const nodeLoader = computed(() => assistant.value ? assistantApi.nodes : undefined)
const docTypeOptions = toOptions(DOC_TYPE_OPTIONS)
// 类型选项取 SPEC 9.4；国家的建议项是当前识别值和“国家未知”，也可自行输入（Q-50 ②）
const ORG_TYPES = ['贸易商', '货代', '银行', '类型未知']
const UNKNOWN_COUNTRY = '国家未知'

// ---- 数据 ----
const files = ref<TaskFile[]>(props.task.files)
const hidden = ref<Set<string>>(new Set())
const visibleFiles = computed(() => files.value.filter(f => !hidden.value.has(f.id)))
watch(() => props.task.files, list => {
  // 稿次关系由会话详情带回（自动认稿、改关系）：只同步这两项，不打断本页其余状态
  const map = new Map(list.map(f => [f.id, f]))
  if (files.value.some(f => map.has(f.id) && (map.get(f.id)!.prev_file_id ?? null) !== (f.prev_file_id ?? null)))
    files.value = files.value.map(f => map.has(f.id) ? { ...f, prev_file_id: map.get(f.id)!.prev_file_id ?? null, draft_no: map.get(f.id)!.draft_no ?? null } : f)
})

// ---- 稿次（V35-D-02）：文件行写“第 2 稿 · 对照《…》第 1 稿”，可改对照哪一稿或作为独立文件；有上一稿时给出变化摘要 ----
const draftDiffs = ref<Record<string, DiffResult | 'error'>>({})
watch(() => visibleFiles.value.map(f => `${f.id}:${f.prev_file_id ?? ''}:${f.parse_state}`).join(','), () => {
  if (!sessionReview.value || !props.diffLoader) return
  for (const f of visibleFiles.value) {
    if (!f.prev_file_id || f.parse_state !== 'done') continue
    const key = `${f.id}:${f.prev_file_id}`
    if (draftDiffs.value[key]) continue
    props.diffLoader(f.id, f.prev_file_id).then(d => { draftDiffs.value = { ...draftDiffs.value, [key]: d } })
      .catch(() => { draftDiffs.value = { ...draftDiffs.value, [key]: 'error' } })
  }
}, { immediate: true })
const fileName = (id: string | null | undefined) => files.value.find(f => f.id === id)?.original_name ?? ''
const draftOf = (f: TaskFile) => { const d = f.prev_file_id ? draftDiffs.value[`${f.id}:${f.prev_file_id}`] : undefined; return d && d !== 'error' ? d : null }
const prevNo = (f: TaskFile) => files.value.find(x => x.id === f.prev_file_id)?.draft_no ?? 1
/** 可作为上一稿的文件：本会话其余文档，排除本稿的后续稿（不能成环） */
function prevChoices(f: TaskFile) {
  const later = new Set<string>([f.id])
  let grew = true
  while (grew) { grew = false; for (const x of files.value) if (x.prev_file_id && later.has(x.prev_file_id) && !later.has(x.id)) { later.add(x.id); grew = true } }
  return visibleFiles.value.filter(x => !later.has(x.id) && x.parse_state === 'done')
}
const draftSaving = ref(false)
async function changeDraft(f: TaskFile, prev: string | null) {
  if (draftSaving.value || (f.prev_file_id ?? null) === prev) return
  draftSaving.value = true
  try {
    const session = await assistantApi.setDraft(props.task.id, f.id, prev)
    const map = new Map(session.files.map(x => [x.id, x]))
    files.value = files.value.map(x => map.has(x.id) ? { ...x, prev_file_id: map.get(x.id)!.prev_file_id ?? null, draft_no: map.get(x.id)!.draft_no ?? null } : x)
    emit('stale')
  } catch (e) { ElMessage.error(e instanceof ApiError ? e.message : '保存失败，请重试') }
  finally { draftSaving.value = false }
}
function viewDiff(f: TaskFile) {
  const d = draftOf(f)
  const first = d?.changes.find(c => c.type !== 'format')
  if (first) void canvas.value?.showDiff(f.id, first)
}
const parties = ref<Party[]>([])
const summary = ref<Record<string, number>>({})
const suspects = ref<Suspect[]>([])
const redactedItems = ref<RedactionInfo['items']>([])
const autoKept = ref<AutoKept[]>([])
const autoOpen = ref(false)
const outboundText = ref('')
const outboundVersion = ref<number | null>(null)
const ocrEditing = ref(false), ocrSaving = ref(false)
const canCorrectOcr = computed(() => sessionReview.value ? props.allowOcr : !assistant.value && !props.task.latest_run && props.task.policy !== 'local_only')
const outboundChars = ref<number | null>(null)
const outboundEmpty = ref(false)
const outboundNote = ref<string | null>(null)
const outboundLoading = ref(false)
const outboundError = ref('')
const blockedReason = ref('')
/** 出口复检拦截清单（V31-U5F-02）：提交被拦或上次轮次被拦时显示 */
const leaksShown = ref(false)
const leakPanel = ref<InstanceType<typeof LeakPanel>>()
/** 拦截清单中还没处理的处数：大于 0 时确认按钮锁定（再点也会被同样拦下），条件栏指向清单 */
const leaksPending = ref(0)
const leakBox = ref<HTMLElement>()
function showLeaks(summary: string) {
  blockedReason.value = summary
  leaksShown.value = true
  void nextTick(() => leakBox.value?.scrollIntoView({ block: 'start', behavior: 'smooth' }))
  void nextTick(() => leakPanel.value?.reload())
}
watch(() => leaksShown.value ? leaksPending.value : 0, n => emit('blocked', n), { immediate: true })
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
  autoKept.value = info.auto_kept ?? []
}
async function loadSummary() { applyRedaction(await redactionApi.redaction(props.task.id)) }
let outboundSeq = 0
async function loadOutbound() {
  const seq = ++outboundSeq
  outboundLoading.value = true
  outboundVersion.value = null
  outboundEmpty.value = false
  outboundError.value = ''
  if (sessionReview.value) emit('preview', null)
  try {
    if (sessionReview.value) {
      const outbound = await assistantApi.outboundReview(props.task.id, {
        stance: (props.stance || undefined) as Stance | undefined, file_ids: props.fileIds, include_pending: true,
        scope: props.scope, main_file_id: props.mainId,
      })
      if (seq !== outboundSeq) return
      if (props.scope === 'delta' && !outbound.delta?.available) {
        outboundError.value = outbound.delta?.reason || '当前不能只审变化，请改为全文重审'
        outboundText.value = ''
        return
      }
      outboundText.value = outbound.text
      outboundChars.value = outbound.chars ?? null
      outboundEmpty.value = props.scope === 'delta' && outbound.delta?.empty === true
      outboundNote.value = outbound.note
      outboundVersion.value = outbound.material_version
      emit('preview', outbound.material_version)
    } else {
      const outbound = await redactionApi.outbound(props.task.id)
      if (seq !== outboundSeq) return
      outboundText.value = outbound.text
      outboundChars.value = null
      outboundNote.value = outbound.note
      outboundVersion.value = outbound.material_version
    }
  } catch (e) {
    if (seq !== outboundSeq) return
    outboundText.value = ''
    outboundNote.value = null
    outboundError.value = e instanceof ApiError ? e.message : '加载失败'
  } finally { if (seq === outboundSeq) outboundLoading.value = false }
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

async function saveOcr(fileId: string, loc: string, body: OcrCorrection) {
  if (!canCorrectOcr.value || ocrSaving.value || submitting.value || waiting.value || saving.value) {
    throw new Error('请等待当前操作完成后再保存校订')
  }
  ocrSaving.value = true
  try {
    const correct = sessionReview.value ? assistantApi.correctOcr : previewApi.correctOcr
    await correct(props.task.id, fileId, loc, body)
    ++outboundSeq
    outboundVersion.value = null
    outboundText.value = ''
    outboundDrawer.value = false
    selectedId.value = null
    checked.value = []
    submitError.value = ''
    if (leaksShown.value) void leakPanel.value?.reload()
    await loadAll(true)
    emit('stale')
  } catch (e) {
    if (e instanceof ApiError && ['MATERIAL_CHANGED', 'TASK_STATE_CONFLICT'].includes(e.code)) {
      await loadAll(true)
      emit('stale')
    }
    throw e
  } finally { ocrSaving.value = false }
}

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
    try {
      const result = await save()
      done?.(result)
    } catch (e) { failed ??= e }
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
    // 服务器按代号排序，改我方后代号变化会让刚点的那一行跳位；保持界面原有顺序
    const order = new Map(parties.value.map((x, i) => [x.id, i]))
    parties.value = [...p.items].sort((a, b) => (order.get(a.id) ?? order.size) - (order.get(b.id) ?? order.size))
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
    // 会话审核：材料变了或会话正忙时，会话本身（文件、轮次、状态）也可能变了，一并让外层刷新
    if (e.code === 'TASK_STATE_CONFLICT' || (sessionReview.value && ['MATERIAL_CHANGED', 'SESSION_BUSY', 'FILES_PARSING'].includes(e.code))) emit('stale')
    if (e.code === 'MATERIAL_CHANGED') { submitError.value = '材料有变化，已更新本页，请核对后再确认'; void canvas.value?.reloadActive(); void loadAll(true) }
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
  if (AUDIO_EXTS.includes(ext)) return '音频'
  return 'IMG'
}
const AUDIO_EXTS = ['mp3', 'm4a', 'wav', 'amr', 'aac', 'mp4']
const badgeClass = (name: string) => ({ DOC: 'doc', XLS: 'xls', PDF: 'pdf', 音频: 'audio' } as Record<string, string>)[fileBadge(name)] ?? 'img'
const parsingFiles = computed(() => visibleFiles.value.some(f => f.parse_state === 'parsing'))
const failedFiles = computed(() => visibleFiles.value.some(f => f.parse_state === 'failed'))

// ---- 签约方 ----
const ourCount = computed(() => parties.value.filter(p => p.is_our_party).length)
const ourId = computed(() => parties.value.find(p => p.is_our_party)?.id ?? '')
watch(() => { const p = parties.value.find(x => x.is_our_party); return p ? `${p.id}:${p.role_label}` : '' }, () => emit('our', parties.value.find(p => p.is_our_party) ?? null), { immediate: true })
// session-review：立场或所选文件变化后重取审核外发预览，与实际发送一致
watch(() => [props.stance, (props.fileIds ?? []).join(','), props.scope, props.mainId].join('|'), () => {
  if (sessionReview.value) void loadOutbound()
})
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
const selectedId = ref<number | null>(null)
const checked = ref<number[]>([])
// 后台保存不锁页面；只有加文件重新计算期间锁住
const locked = computed(() => waiting.value || ocrSaving.value)
const canvas = ref<InstanceType<typeof ConfirmCanvas>>()
watch(suspects, (rows) => {
  const ids = new Set(rows.map(s => s.id))
  checked.value = checked.value.filter(id => ids.has(id))
  if (selectedId.value !== null && !ids.has(selectedId.value)) selectedId.value = null
})
const keepIds = computed(() => suspects.value.filter(s => checked.value.includes(s.id) && !isKept(s)).map(s => s.id))
const resetIds = computed(() => suspects.value.filter(s => checked.value.includes(s.id) && isKept(s)).map(s => s.id))

function shortText(text: string): string { return Array.from(text).length > 12 ? `${Array.from(text).slice(0, 12).join('')}…` : text }
function resetOne(s: Suspect) {
  if (locked.value) return
  patchSuspects([s.id], false)
  void decide(() => redactionApi.decideSuspect(props.task.id, s.id, 'reset', null))
}
function keepOne(s: Suspect, inline = false) {
  if (locked.value || isKept(s)) return
  patchSuspects([s.id], true)
  void decide(() => redactionApi.decideSuspect(props.task.id, s.id, 'ignore', null))
  if (inline) return // 画布已在该词上方就地提示
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
/** “需要你看一眼的”卡片底部的全部保留 / 全部遮住 */
function keepAll() { checked.value = suspects.value.map(s => s.id); void keepChecked() }
function maskAll() { checked.value = suspects.value.map(s => s.id); resetChecked() }
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
/** 自动保留的普通词按文字合并，显示出现次数；“改为替换”按首个位置走手动补充（同一文字全文替换）。 */
const autoGroups = computed(() => {
  const map = new Map<string, { text: string; reason: string; category: string; count: number; first: AutoKept }>()
  for (const a of autoKept.value) {
    const hit = map.get(a.text)
    if (hit) hit.count += 1
    else map.set(a.text, { text: a.text, reason: a.reason, category: a.category, count: 1, first: a })
  }
  return [...map.values()]
})
const MANUAL_CATEGORIES = new Set(['ORG', 'PERSON', 'IM', 'ACCOUNT', 'IDNO', 'REFNO', 'ADDR', 'AMOUNT', 'PROJECT', 'CUSTOM'])
function replaceAuto(group: { first: AutoKept; category: string }) {
  const { loc, char_start, char_end } = group.first
  onManual({ loc, char_start, char_end, category: (MANUAL_CATEGORIES.has(group.category) ? group.category : 'CUSTOM') as UserCategory })
}
/** 发送后提示新写入术语记忆的词数，可撤销（V321-E-07） */
function rememberedToast(result?: RunCreated | void) {
  const hashes = result?.remembered ?? []
  if (!hashes.length) return
  toast(`已记住 ${hashes.length} 个保留原文的词，下次自动保留`, {
    duration: 8000,
    action: { label: '撤销', onClick: () => { redactionApi.forgetKept(props.task.id, hashes).then(() => toast('已撤销，这些词下次仍会提示')).catch(() => ElMessage.error('撤销失败，可在“设置 → 术语记忆”中删除')) } },
  })
}
/** 划选补充：画布上立即出现胶囊（代号等服务器分配），后台保存。 */
function onManual(input: ManualInput) {
  if (locked.value) return
  redactedItems.value = [...redactedItems.value, { ...input, placeholder: '…', can_revoke: false, detected_by: 'manual' }]
  void decide(() => redactionApi.manual(props.task.id, input))
}
/** 撤销误识别：先去掉这一处胶囊，保存后同一文字在全文恢复原样。 */
async function onRevoke(item: RedactionItemPos) {
  if (locked.value) return
  const party = parties.value.find(p => p.code === item.placeholder)
  try {
    await ElMessageBox.confirm(party
      ? '撤销后会把该主体从签约方里去掉，同一文字将恢复原文。若其他替换仍引用该主体，出口复检仍可能拦截，请检查后再发送。'
      : '撤销后，同一文字将在全文恢复原样。确认它不是敏感信息后再撤销。', party ? '撤销签约方替换' : '撤销误识别', {
      type: 'warning', confirmButtonText: party ? '移出签约方并撤销' : '撤销', cancelButtonText: '取消',
    })
  } catch { return }
  if (locked.value) return
  redactedItems.value = redactedItems.value.filter(it => it !== item)
  void decide(() => redactionApi.revoke(props.task.id, { loc: item.loc, char_start: item.char_start, char_end: item.char_end }))
}

// ---- 胶囊菜单：改类别、扩大或缩小范围（V35-F-04）：画布先按新范围显示，后台保存 ----
function onAdjust(item: RedactionItemPos, start: number, end: number, category?: UserCategory) {
  if (locked.value) return
  redactedItems.value = [...redactedItems.value.filter(it => it !== item && !(it.loc === item.loc && it.char_start < end && it.char_end > start)),
    { ...item, char_start: start, char_end: end, category: category ?? item.category, placeholder: '…' }]
  partiesTouched = true
  void decide(() => redactionApi.adjust(props.task.id, { loc: item.loc, char_start: item.char_start, char_end: item.char_end, new_start: start, new_end: end, ...(category ? { category } : {}) }))
}

// ---- 签约方卡片：名称和简称可改、可删（问题 15）：改过的写法登记为同一代号的变体 ----
const partyName = (p: Party) => p.surfaces?.[0] ?? p.name
const partyShorts = (p: Party) => (p.surfaces ?? []).slice(1)
const partyMark = (p: Party) => Array.from(p.role_label || p.code.replace(/[[\]]/g, ''))[0] ?? '方'
const nameDraft = ref('')
const shortDraft = ref('')
function editParty(p: Party) {
  editingParty.value = editingParty.value === p.id ? '' : p.id
  nameDraft.value = partyName(p)
  shortDraft.value = ''
}
function partyChange(save: () => Promise<RedactionInfo>) {
  partiesTouched = true
  return decide(save)
}
function saveName(p: Party) {
  const next = nameDraft.value.trim(), old = partyName(p)
  if (!next || next === old || locked.value) return
  void partyChange(() => redactionApi.addAlias(props.task.id, p.id, next))
  // 新写法不包含旧写法时，旧写法不再算这一方（例如识别多了几个字）
  if (!next.includes(old)) void partyChange(() => redactionApi.removeAlias(props.task.id, p.id, old))
  parties.value = parties.value.map(x => x.id === p.id ? { ...x, surfaces: [next, ...(x.surfaces ?? []).filter(v => v !== old && v !== next)] } : x)
}
function addShort(p: Party) {
  const v = shortDraft.value.trim()
  if (!v || locked.value) return
  shortDraft.value = ''
  parties.value = parties.value.map(x => x.id === p.id ? { ...x, surfaces: [...(x.surfaces ?? [p.name]), v] } : x)
  void partyChange(() => redactionApi.addAlias(props.task.id, p.id, v)).then(() => {
    // 简称只出现在全称里时不单列（卡片按原文里出现的写法显示）
    const now = parties.value.find(x => x.id === p.id)
    if (now && !submitError.value && !(now.surfaces ?? []).includes(v)) toast(`原文里的“${v}”都在全称之内，已随全称一起遮住；单独出现时也会用同一代号`)
  })
}
function removeShort(p: Party, v: string) {
  if (locked.value) return
  parties.value = parties.value.map(x => x.id === p.id ? { ...x, surfaces: (x.surfaces ?? []).filter(s => s !== v) } : x)
  void partyChange(() => redactionApi.removeAlias(props.task.id, p.id, v))
}
async function removePartyCard(p: Party) {
  try {
    await ElMessageBox.confirm(`删掉后，“${partyName(p)}”及它的简称在全文都不再遮住。只在它不是真实单位（识别错了）时这样做。`, '删掉这个签约方', {
      type: 'warning', confirmButtonText: '删掉', cancelButtonText: '取消', confirmButtonClass: 'el-button--danger',
    })
  } catch { return }
  if (locked.value) return
  parties.value = parties.value.filter(x => x.id !== p.id)
  void partyChange(() => redactionApi.removeParty(props.task.id, p.id))
}
const ourOptions = computed(() => {
  const labels = parties.value.map(p => p.role_label || p.code)
  if (parties.value.length > 3 || new Set(labels).size !== labels.length) return null
  return [...parties.value.map(p => ({ value: p.id, label: p.role_label || p.code })), ...(assistant.value && !props.requireOurParty ? [{ value: '', label: '都不是' }] : [])]
})

// ---- 外发回执 ----
const replacedTotal = computed(() => Object.values(summary.value).reduce((sum, n) => sum + n, 0))
const categoryChips = computed(() => Object.entries(summary.value).filter(([, n]) => n > 0).map(([code, n]) => `${REDACTION_CATEGORY[code] ?? code} ${n}`))
const outboundLength = computed(() => outboundLoading.value || !outboundText.value ? 0 : outboundChars.value ?? outboundText.value.length)
const reqDrawer = ref(false)
const localDrawer = ref(false)
const outboundDrawer = ref(false)
// 后台保存中也可以点；点击后先等保存队列清空，有失败则停在本页（已回滚并提示）
const ourMissing = computed(() => !(assistant.value && !props.requireOurParty ? ourCount.value <= 1 : ourCount.value === 1))
// 缺的只是用户该选的项（我方、父页面的立场 / 模型）时按钮仍可点，点击定位到缺项（V33-C-01）；等待类条件照旧禁用
const readyExceptChoices = computed(() =>
  !loading.value && !waiting.value && !parsingFiles.value && !failedFiles.value && !ocrEditing.value && !ocrSaving.value
  && (assistant.value || outboundVersion.value !== null)
  && (!sessionReview.value || ((!props.blocker || props.blockerChoice) && !outboundLoading.value && (!!outboundText.value || outboundEmpty.value))))
const canConfirm = computed(() => readyExceptChoices.value && !ourMissing.value && (!sessionReview.value || !props.blocker))
const partiesSec = ref<HTMLElement>()
function nudgeMissing() {
  if (ourMissing.value) nudgeField(partiesSec.value)
  else if (sessionReview.value && props.blocker && props.blockerChoice) emit('nudge')
}
function onConfirmClick() { if (canConfirm.value) void confirmSend(); else nudgeMissing() }
const confirmBox = ref<HTMLElement>()
const confirmFlash = ref(false)
// Ctrl+Enter 只聚焦按钮（3.6）。部分环境会把同一次按键再作为 Enter 送到新聚焦的按钮上，
// 聚焦后 600ms 内的点击一律忽略，保证快捷键本身永不外发（V31-U2-05 曾误发一次）。
let focusedByHotkeyAt = 0

// 提交等待：按钮不转圈，按钮上方一行墨点 + 当前步骤 + 已用秒数（同一个等待只有这一个指示）
const submitStep = ref<'saving' | 'files' | 'sending'>('sending')
const submitSeconds = ref(0)
let submitTimer: number | undefined
// 出口复检约 1.2 秒 / 万字（V31-U5F 实测），加上建轮次的固定开销
const expectSeconds = computed(() => Math.max(3, Math.ceil(outboundLength.value / 8000) + 2))
const submitText = computed(() => {
  if (submitStep.value === 'saving') return '正在等刚才的修改保存完成'
  if (submitStep.value === 'files') return '正在删除文件并重新脱敏'
  if (assistant.value && !sessionReview.value) return '正在确认脱敏结果'
  return `本机正在对 ${outboundLength.value.toLocaleString('zh-CN')} 字做出口复检，通过后交给模型，约需 ${expectSeconds.value} 秒`
})
const submitSlow = computed(() => submitStep.value === 'sending' && submitSeconds.value > expectSeconds.value + 5)
function startSubmitClock() {
  submitSeconds.value = 0
  submitTimer = window.setInterval(() => { submitSeconds.value += 1 }, 1000)
}
onBeforeUnmount(() => window.clearInterval(submitTimer))

async function confirmSend() {
  if (!canConfirm.value || Date.now() - focusedByHotkeyAt < 600) return
  submitError.value = ''
  submitting.value = true
  submitStep.value = saving.value ? 'saving' : pendingDeletes.size() ? 'files' : 'sending'
  startSubmitClock()
  try {
    // 先等后台保存全部完成；有失败时界面已回滚并提示，停在本页
    if (saving.value) {
      await tail
      if (submitError.value || !canConfirm.value) return
    }
    // 撤销期内删除的文件仍在服务器上：先真删并等重算完成，用户看到更新后的回执再确认
    if (pendingDeletes.size()) { submitStep.value = 'files'; await pendingDeletes.flush(); return }
    submitStep.value = 'sending'
    if (sessionReview.value) rememberedToast(await props.submit?.())
    else if (assistant.value) await assistantApi.confirm(props.task.id)
    else rememberedToast(await runsApi.create(props.task.id, !skipTemplate.value, props.modelId, outboundVersion.value ?? undefined))
    emit('confirmed')
  } catch (e) { handleError(e) }
  finally { submitting.value = false; window.clearInterval(submitTimer) }
}

// ---- 宽度与键盘（3.1、3.6） ----
const RAIL_MIN = 340, RAIL_MAX = 480, RAIL_DEFAULT = 380, RAIL_KEY = 'si.confirm.rightWidth'
const railWidth = ref(readStoredWidth(RAIL_KEY, RAIL_MIN, RAIL_MAX, RAIL_DEFAULT))
const helpOpen = ref(false)
const HOTKEYS = [
  ['V', '切换“原文 / 模型将看到的”'],
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
}, { enabled: computed(() => !assistant.value || sessionReview.value) })
</script>

<template>
  <div class="wb" @dragenter.prevent="onDragEnter" @dragover.prevent @dragleave="onDragLeave" @drop.prevent="onDrop">
    <div class="canvas">
      <ConfirmCanvas ref="canvas" :diff-loader="sessionReview ? diffLoader : undefined" :ai-view="true" :node-loader="nodeLoader" :task-id="task.id" :files="visibleFiles" :items="redactedItems" :suspects="suspects" :note="task.user_note" :selected-suspect-id="selectedId" :locked="locked" :protected-count="replacedTotal"
        :raw-loader="sessionReview ? assistantApi.raw : undefined" :correct-ocr="canCorrectOcr ? saveOcr : undefined" :ocr-version="outboundVersion"
        :ocr-busy="loading || outboundLoading || locked || saving > 0 || submitting" @ocr-editing="ocrEditing = $event" @manual="onManual" @revoke="onRevoke" @keep="s => keepOne(s, true)" @restore="restoreOne" @replace="replaceOne" @adjust="onAdjust">
        <template #bar>
          <SiIsland v-if="island" :state="island.state" :text="island.text" />
          <span v-else-if="saving" class="hint saving" aria-live="polite">正在后台保存…</span>
          <button v-if="!assistant || sessionReview" type="button" class="hint keys-link" @click="helpOpen = true">按 ? 查看快捷键</button>
        </template>
      </ConfirmCanvas>
      <SiNotice v-if="outboundError" type="error" :closable="false" :title="outboundError" />
      <div v-if="dragging" class="drop-veil" aria-hidden="true">松开加入本任务</div>
    </div>
    <SplitHandle v-model="railWidth" reverse :min="RAIL_MIN" :max="RAIL_MAX" :default-value="RAIL_DEFAULT" :storage-key="RAIL_KEY" label="调整回执栏宽度" />
    <aside v-loading="loading" class="rail" :style="{ flexBasis: `${railWidth}px` }">
      <div class="rail-scroll si-stagger">
        <SiNotice v-if="loadError" type="error" :closable="false" :title="loadError" />
        <div v-if="leaksShown" ref="leakBox"><LeakPanel ref="leakPanel" :task-id="task.id" :files="files" :locked="locked || saving > 0" :summary="blockedReason" @locate="(loc, text) => canvas?.locate(loc, text)" @changed="afterChange" @pending="leaksPending = $event" @parties="nudgeField(partiesSec)" /></div>

        <!-- ① 签约方与我方 -->
        <section ref="partiesSec" class="rp-card">
          <h3 class="rp-h"><Users :size="16" :stroke-width="1.75" /><b>签约方与我方</b><span v-if="ourMissing && parties.length" class="si-required-tag">请选择我方</span></h3>
          <p v-if="!loading && !parties.length && assistant && !requireOurParty" class="hint">没有识别到签约方。不设我方也可以确认；需要时在原文中选中我方名称，遮住为“公司”后设为我方。</p>
          <p v-else-if="!loading && !parties.length" class="warn">没有识别到签约方：在原文中选中我方名称，遮住为“公司”后再设为我方</p>
          <div v-for="p in parties" :key="p.id" class="party" :class="{ me: p.is_our_party, editing: editingParty === p.id }">
            <div class="party-line">
              <span class="party-code" :class="{ me: p.is_our_party }">{{ partyMark(p) }}</span>
              <div class="party-main">
                <b :title="partyName(p)">{{ partyName(p) }}</b>
                <span>{{ p.role_label }} · <span class="code-chip">{{ p.code }}</span><template v-if="partyShorts(p).length"> · 简称：{{ partyShorts(p).join('、') }}</template><em v-if="p.is_our_party"> · 我方</em></span>
              </div>
              <button type="button" class="ibtn" :aria-label="`编辑 ${partyName(p)}`" :aria-expanded="editingParty === p.id" :disabled="waiting" @click="editParty(p)"><Pencil :size="15" :stroke-width="1.75" /></button>
            </div>
            <div v-if="editingParty === p.id" class="party-edit">
              <label class="pe-row"><span>名称</span><el-input v-model="nameDraft" size="small" :disabled="locked" placeholder="照原文写法" @keyup.enter="saveName(p)" /><el-button size="small" :disabled="locked || !nameDraft.trim() || nameDraft.trim() === partyName(p)" @click="saveName(p)">改</el-button></label>
              <div class="pe-row"><span>简称</span>
                <div class="shorts">
                  <span v-for="v in partyShorts(p)" :key="v" class="short">{{ v }}<button type="button" :aria-label="`删掉简称 ${v}`" :disabled="locked" @click="removeShort(p, v)"><X :size="12" :stroke-width="2" /></button></span>
                  <el-input v-model="shortDraft" size="small" class="short-input" :disabled="locked" placeholder="加简称" @keyup.enter="addShort(p)" />
                </div>
              </div>
              <p class="hint">改过的名称和简称登记为同一代号的变体，发送前复检不再拦它们。</p>
              <div class="pe-row pe-more">
                <el-select :model-value="p.country ?? UNKNOWN_COUNTRY" filterable allow-create default-first-option size="small" :disabled="waiting" aria-label="国家" @change="(v: string) => setField(p.id, 'country', v)"><el-option v-for="c in countryOptions(p)" :key="c" :label="c" :value="c" /></el-select>
                <el-select :model-value="p.org_type ?? '类型未知'" filterable allow-create default-first-option size="small" :disabled="waiting" aria-label="类型" @change="(v: string) => setField(p.id, 'org_type', v)"><el-option v-for="t in ORG_TYPES" :key="t" :label="t" :value="t" /></el-select>
              </div>
              <div class="pe-foot">
                <el-button v-if="!ourOptions && !p.is_our_party" size="small" :disabled="waiting" @click="setOur(p.id)">设为我方</el-button>
                <el-button size="small" text type="danger" :disabled="locked" @click="removePartyCard(p)"><Trash2 :size="14" :stroke-width="1.75" />删掉这一方</el-button>
              </div>
            </div>
          </div>
          <div v-if="parties.length" class="our-row" :class="{ 'si-required-empty': ourMissing }">
            <span class="hint">我方是</span>
            <SiSegmented v-if="ourOptions" :model-value="ourId" :options="ourOptions" size="small" ariaLabel="我方是" @update:model-value="setOur(String($event))" />
            <span v-else class="hint">{{ ourCount ? parties.find(p => p.is_our_party)?.role_label : '点签约方的编辑按钮设为我方' }}</span>
          </div>
        </section>

        <!-- ② 需要你看一眼的 -->
        <section class="rp-card">
          <h3 class="rp-h"><Eye :size="16" :stroke-width="1.75" /><b>需要你看一眼的</b><span v-if="suspects.length" class="badge-mid num">{{ suspects.length }}</span></h3>
          <p v-if="!suspects.length" class="hint">没有拿不准的内容。</p>
          <template v-else>
            <p class="hint">拿不准是不是名称，默认遮住。保留原文的会按原文发给模型。</p>
            <ul class="unsure-list">
              <li v-for="s in suspects" :key="s.id" :data-sid="s.id" class="unsure" :class="{ selected: s.id === selectedId }" @click="selectSuspect(s)">
                <i class="kdot" :class="`k-${s.category === 'PERSON' ? 'person' : s.category === 'REFNO' ? 'other' : 'org'}`" />
                <div class="unsure-main">
                  <b :title="s.text">{{ s.text }}</b>
                  <span>{{ suspectReasonLabel(s.reason) }}<template v-if="s.occurrences > 1"> · {{ s.occurrences }} 处</template><template v-if="isKept(s)"> · {{ KEPT_BY[s.kept_by ?? 'user'] }}</template></span>
                </div>
                <span class="choice" :class="isKept(s) ? 'is-kept' : 'is-masked'" role="group" :aria-label="`${s.text}：保留原文或遮住`" @click.stop>
                  <button type="button" class="ch ch-keep" :aria-pressed="isKept(s)" :disabled="locked" @click="isKept(s) ? undefined : keepOne(s)"><Check v-if="isKept(s)" :size="14" :stroke-width="2.25" aria-hidden="true" />{{ isKept(s) ? '已保留' : '保留' }}</button>
                  <button type="button" class="ch ch-mask" :aria-pressed="!isKept(s)" :disabled="locked" @click="isKept(s) ? restoreOne(s) : undefined"><Check v-if="!isKept(s)" :size="14" :stroke-width="2.25" aria-hidden="true" />{{ isKept(s) ? '遮住' : '已遮住' }}</button>
                </span>
              </li>
            </ul>
            <div class="row2"><el-button size="small" :disabled="locked || suspects.every(isKept) || suspects.length > 200" @click="keepAll">全部保留</el-button><el-button size="small" :disabled="locked || !suspects.some(isKept) || suspects.length > 200" @click="maskAll">全部遮住</el-button></div>
          </template>
          <div v-if="autoGroups.length" class="auto">
            <button type="button" class="auto-head" :aria-expanded="autoOpen" @click="autoOpen = !autoOpen">
              <component :is="autoOpen ? ChevronDown : ChevronRight" :size="14" :stroke-width="1.75" />已自动保留 {{ autoGroups.length }} 个普通词，没有遮住
            </button>
            <ul v-if="autoOpen" class="unsure-list">
              <li v-for="g in autoGroups" :key="g.text" class="unsure">
                <div class="unsure-main"><b :title="g.text">{{ g.text }}</b><span>{{ autoKeptReasonLabel(g.reason) }}<template v-if="g.count > 1"> · {{ g.count }} 处</template></span></div>
                <el-button size="small" :disabled="locked" @click="replaceAuto(g)">遮住</el-button>
              </li>
            </ul>
          </div>
          <div class="sr-only" aria-live="polite">{{ liveText }}</div>
        </section>

        <!-- 文件：多份文件或可以增删时才单列 -->
        <section v-if="visibleFiles.length > 1 || canEditFiles || failedFiles || visibleFiles.some(f => f.warnings.length)" class="rp-card">
          <h3 class="rp-h"><FileText :size="16" :stroke-width="1.75" /><b>文件</b><span class="hint num">{{ visibleFiles.length }} 份</span></h3>
          <ul class="files">
            <li v-for="f in visibleFiles" :key="f.id" class="file">
              <div class="file-line">
                <el-checkbox v-if="sessionReview && visibleFiles.length > 1" :model-value="(picked ?? []).includes(f.id)" :disabled="f.parse_state !== 'done'" :aria-label="`审核 ${f.original_name}`" @change="(v: unknown) => emit('update:picked', v ? [...(picked ?? []), f.id] : (picked ?? []).filter(id => id !== f.id))" />
                <span class="ftype" :class="`ftype--${badgeClass(f.original_name)}`">{{ fileBadge(f.original_name) }}</span>
                <span class="file-name" :title="f.original_name">{{ f.original_name }}</span>
                <em v-if="sessionReview ? f.id === mainId : f.is_main" class="main-tag">主文件</em>
                <el-button v-else-if="sessionReview && (picked ?? []).includes(f.id)" link type="primary" size="small" @click="emit('update:mainId', f.id)">设为主文件</el-button>
                <el-dropdown v-if="canEditFiles" trigger="click" :disabled="waiting">
                  <button type="button" class="ibtn" :aria-label="`${f.original_name} 的更多操作`"><Ellipsis :size="16" :stroke-width="1.75" /></button>
                  <template #dropdown><el-dropdown-menu><el-dropdown-item :disabled="f.is_main || f.parse_state !== 'done'" @click="setMain(f)">设为主文件</el-dropdown-item><el-dropdown-item :disabled="visibleFiles.length <= 1" @click="removeFile(f)">删除</el-dropdown-item></el-dropdown-menu></template>
                </el-dropdown>
              </div>
              <div v-if="!assistant" class="file-line sub">
                <el-select class="doc-type" :model-value="f.doc_type ?? undefined" placeholder="请选择" size="small" :aria-label="`${f.original_name} 的文件类型`" @change="(v: string) => changeDocType(f.id, v)"><el-option v-for="o in docTypeOptions" :key="o.value" :label="o.label" :value="o.value" /></el-select>
                <span class="hint">{{ docTypeSourceLabel(f.doc_type_source) }}</span>
              </div>
              <div v-if="sessionReview && (f.draft_no || visibleFiles.length > 1)" class="draft-line">
                <el-dropdown trigger="click" :disabled="draftSaving || waiting" @command="(v: string) => changeDraft(f, v || null)">
                  <button type="button" class="draft-btn" :class="{ on: !!f.draft_no }" :aria-label="`${f.original_name} 的稿次`">
                    <template v-if="f.prev_file_id">第 {{ f.draft_no }} 稿 · 对照《{{ fileName(f.prev_file_id) }}》第 {{ prevNo(f) }} 稿</template>
                    <template v-else-if="f.draft_no">第 {{ f.draft_no }} 稿</template>
                    <template v-else>独立文件</template>
                    <ChevronDown :size="13" :stroke-width="1.75" />
                  </button>
                  <template #dropdown><el-dropdown-menu>
                    <el-dropdown-item command="" :disabled="!f.prev_file_id">作为独立文件</el-dropdown-item>
                    <el-dropdown-item v-for="x in prevChoices(f)" :key="x.id" :command="x.id" :disabled="x.id === f.prev_file_id">作为《{{ x.original_name }}》的新一稿</el-dropdown-item>
                  </el-dropdown-menu></template>
                </el-dropdown>
              </div>
              <div v-if="sessionReview && draftOf(f)" class="draft-sum">
                <span>{{ diffParts(draftOf(f)!).length ? diffParts(draftOf(f)!).map(p => p.text).join(' · ') : '正文与上一稿一致' }}</span>
                <el-button v-if="substantive(draftOf(f)!).length" link type="primary" size="small" @click="viewDiff(f)">查看对比</el-button>
              </div>
              <div class="hint">{{ fileMeta(f) }}<template v-if="f.parse_state === 'parsing'"> · 正在读取</template></div>
              <div v-if="f.parse_state === 'failed'" class="warn">读取失败：{{ f.parse_error || '文件无法读取' }}</div>
              <div v-if="f.warnings.length" class="warn" :title="f.warnings.join('\n')">{{ f.warnings[0] }}<template v-if="f.warnings.length > 1">（共 {{ f.warnings.length }} 条）</template></div>
            </li>
          </ul>
          <template v-if="canEditFiles">
            <input ref="picker" type="file" multiple hidden accept=".docx,.pdf,.xlsx,.doc,.wps,.rtf,.xls,.et,.jpg,.jpeg,.png" @change="onPick">
            <el-button class="add-file" size="small" :disabled="waiting" @click="picker?.click()"><Plus :size="14" :stroke-width="1.75" />加入文件</el-button>
          </template>
          <p v-if="fileError" class="warn">{{ fileError }}</p>
        </section>

        <!-- ③ 将要发送的 -->
        <section class="rp-card">
          <h3 class="rp-h"><Send :size="16" :stroke-width="1.75" /><b>{{ assistant && !sessionReview ? '本会话文件的遮住结果' : '将要发送的' }}</b></h3>
          <ul class="send-list">
            <li><FileText :size="15" :stroke-width="1.75" /><span v-if="outboundEmpty">正文没有实质变化，本轮无需发送给模型</span><span v-else>正文 <b class="num"><SiCount :value="outboundLength" /></b> 字，已在本机遮住 <b class="num"><SiCount :value="replacedTotal" /></b> 处</span></li>
            <li v-if="categoryChips.length" class="chips-li"><span class="chips"><span v-for="c in categoryChips" :key="c" class="chip">{{ c }}</span></span></li>
            <li><Ban :size="15" :stroke-width="1.75" /><span>不发送原件、图片和文件名</span></li>
            <li v-if="!assistant || sessionReview"><Sparkle :size="15" :stroke-width="1.75" class="t-ai" /><span>模型：{{ modelName || '审核模型' }}</span></li>
            <li v-if="!assistant && task.template_suggestion"><Bookmark :size="15" :stroke-width="1.75" /><span>对照范本：{{ task.template_suggestion.name }}</span><el-checkbox v-model="skipTemplate" :disabled="submitting" class="skip">不对照</el-checkbox></li>
          </ul>
          <div class="links">
            <el-button link type="primary" size="small" :disabled="outboundLoading || !outboundText" @click="outboundDrawer = true">{{ scope === 'delta' ? '查看将发送的变化与问题' : '查看将发送的全文' }}</el-button>
            <el-button v-if="!assistant" link type="primary" size="small" @click="localDrawer = true">本地校验 {{ localCount ?? '—' }} 条</el-button>
            <el-button v-if="!assistant" link type="primary" size="small" @click="reqDrawer = true">审核要求</el-button>
          </div>
          <div v-if="$slots.settings" class="settings"><slot name="settings" /></div>
        </section>
      </div>

      <div class="rp-foot">
        <SiNotice v-if="submitError" type="error" :closable="false" :title="submitError" class="receipt-alert" />
        <ul class="checks" aria-label="提交条件" aria-live="polite">
          <li v-if="assistant && !requireOurParty" class="ok"><CircleCheck :size="15" :stroke-width="1.75" />{{ ourCount ? '已选一方为我方' : '不设我方也可以确认' }}</li>
          <li v-else :class="ourCount === 1 ? 'ok' : 'todo'"><component :is="ourCount === 1 ? CircleCheck : CircleDot" :size="15" :stroke-width="1.75" /><template v-if="ourCount === 1">已选我方</template><template v-else-if="!parties.length">没有识别到签约方，暂时不能交给模型</template><button v-else type="button" class="cond-link" @click="nudgeField(partiesSec)">还没选哪一方是我方</button></li>
          <li v-if="parties.length" class="ok"><CircleCheck :size="15" :stroke-width="1.75" />签约方已列出，可在上方核对</li>
          <li v-if="suspects.length" class="info"><CircleDot :size="15" :stroke-width="1.75" />{{ suspects.length }} 处拿不准的已默认遮住</li>
          <li v-if="sessionReview && blocker" class="todo"><CircleDot :size="15" :stroke-width="1.75" /><button v-if="blockerChoice" type="button" class="cond-link" @click="emit('nudge')">{{ blocker }}</button><template v-else>{{ blocker }}</template></li>
          <li v-if="leaksShown && leaksPending" class="todo"><CircleDot :size="15" :stroke-width="1.75" /><button type="button" class="cond-link" @click="nudgeField(leakBox)">还有 {{ leaksPending }} 处拦截未处理</button></li>
          <li v-if="waiting || parsingFiles" class="todo"><CircleDot :size="15" :stroke-width="1.75" />文件正在读取，请稍候</li>
          <li v-if="failedFiles" class="todo"><CircleDot :size="15" :stroke-width="1.75" />有读取失败的文件，请先删除</li>
          <li v-if="ocrEditing" class="info"><CircleDot :size="15" :stroke-width="1.75" />请先保存或取消扫描文字校对</li>
          <li v-if="saving" class="info"><CircleDot :size="15" :stroke-width="1.75" />修改正在后台保存，发送时会等保存完成</li>
          <li v-if="loading" class="info"><CircleDot :size="15" :stroke-width="1.75" />正在读取遮住的内容……</li>
        </ul>
        <p v-if="submitting" class="submit-wait si-wait" role="status">
          <SiThinkingDots />
          <span><span class="si-shimmer">{{ submitText }}</span>，已用 {{ submitSeconds }} 秒<template v-if="submitSlow">。比预计慢：可能在按最新规则重新遮住，请不要关闭页面</template></span>
        </p>
        <div ref="confirmBox" class="confirm-btn" :class="{ flash: confirmFlash }">
          <p v-if="continueQuestion && !submitting" class="hint">确认后继续已提交的提问。<el-button link type="primary" @click="emit('cancel-waiting')">取消续发</el-button></p>
          <el-button type="primary" size="large" :disabled="submitting || !readyExceptChoices || (leaksShown && leaksPending > 0)" @click="onConfirmClick"><Send :size="16" :stroke-width="1.75" />{{ submitting ? '正在提交…' : leaksShown ? '重新发送' : assistant && !sessionReview ? (continueQuestion ? '确认并继续发送' : '确认遮住') : '确认发送并开始审核' }}</el-button>
        </div>
      </div>
    </aside>

    <RequirementsDrawer v-model="reqDrawer" :stance="task.stance" :source="task.source" :doc-type="mainDocType" :user-note="task.user_note" />
    <el-drawer v-model="localDrawer" title="本地校验结果" size="560px" append-to-body><LocalCheckList :loader="loadLocal" :load-key="task.id" @count="localCount = $event" /></el-drawer>
    <el-drawer v-model="outboundDrawer" :title="scope === 'delta' ? '将发送的变化与问题' : '将发送的全文'" size="640px" append-to-body>
      <p v-if="outboundNote" class="hint">{{ outboundNote }}</p>
      <pre class="outbound">{{ outboundText }}</pre>
    </el-drawer>
    <el-dialog v-model="helpOpen" title="键盘快捷键" width="480px" append-to-body>
      <table class="keys"><tbody><tr v-for="[key, text] in HOTKEYS" :key="key"><th>{{ key }}</th><td>{{ text }}</td></tr></tbody></table>
    </el-dialog>
  </div>
</template>

<style scoped>
/* 稿次（V35-D-02） */
.draft-line { display: flex; align-items: center; gap: 6px; margin-top: 2px; }
.draft-btn { display: inline-flex; align-items: center; gap: 4px; max-width: 100%; height: 22px; padding: 0 8px; border: 1px solid var(--si-hair); border-radius: var(--si-radius-badge); background: transparent; color: var(--si-ink-3); font: var(--si-font-caption); cursor: pointer; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.draft-btn.on { border-color: transparent; background: var(--si-blue-tint); color: var(--si-blue-ink); }
.draft-btn:hover { border-color: var(--el-color-primary); }
.draft-sum { display: flex; align-items: center; gap: 8px; margin-top: 2px; color: var(--si-ink-2); font: var(--si-font-caption); }

/* 工作台（V31-U5F-03 问题 2B）：高度固定为容器（可视区），左侧文档、右侧栏各自滚动。
   height: 100% 让块级容器（助手、会议纪要、翻译的全屏对话框）里同样固定高度，不再随文档拉伸 */
.wb { display: flex; flex: 1; height: 100%; min-height: 0; box-sizing: border-box; padding: 12px 0 20px; overflow: hidden; }
.canvas { position: relative; flex: 1; min-width: 0; min-height: 0; display: flex; flex-direction: column; gap: 8px; }
.canvas :deep(.stage) { padding-top: 2px; }
.keys-link { flex: none; padding: 0; border: 0; background: transparent; cursor: pointer; white-space: nowrap; }
.keys-link:hover { color: var(--el-color-primary); }
.saving { white-space: nowrap; }
.drop-veil { position: absolute; inset: 0; display: grid; place-items: center; border: 2px dashed var(--el-color-primary); border-radius: var(--si-radius-card); background: color-mix(in srgb, var(--el-color-primary-light-9) 85%, transparent); color: var(--el-color-primary); font: var(--si-font-h4); pointer-events: none; }
/* 右栏（澄明 8.4）：三张卡片各自成组，栏底固定提交条件与满宽主按钮 */
.rail { flex: 0 0 auto; min-width: 0; min-height: 0; margin-top: 42px; display: flex; flex-direction: column; overflow: hidden; }
.rail-scroll { flex: 1; min-height: 0; overflow-y: auto; padding: 0 4px 12px 2px; display: flex; flex-direction: column; gap: 12px; }
.rp-card { padding: 14px 16px; border: 1px solid var(--si-hair); border-radius: var(--si-radius-card); background: var(--si-surface-1); box-shadow: var(--si-shadow-1); }
.rp-h { display: flex; align-items: center; gap: 8px; margin: 0 0 10px; font: var(--si-font-h4); color: var(--si-ink-1); }
.rp-h > svg { flex: none; color: var(--si-ink-3); }
.rp-h b { font-weight: 600; }
.rp-h .hint, .rp-h .badge-mid { margin-left: auto; }
.badge-mid { display: inline-grid; place-items: center; min-width: 22px; height: 22px; padding: 0 7px; border-radius: var(--si-radius-badge); background: var(--si-warn-soft); color: var(--si-warn-ink); font: var(--si-font-badge); }
.hint { margin: 0; }
.warn { margin: 4px 0 0; color: var(--si-warn-ink); font: var(--si-font-caption); }
/* 签约方 */
.party { margin: 0 -8px; padding: 8px; border-radius: var(--si-radius-md); transition: background-color var(--si-d2) var(--si-ease-out); }
.party + .party { margin-top: 2px; }
.party.me { background: var(--si-blue-tint); }
.party.editing { background: var(--si-card-2); }
.party-line { display: flex; align-items: center; gap: 10px; }
.party-code { flex: none; display: grid; place-items: center; width: 34px; height: 34px; border-radius: var(--si-radius-control); background: var(--si-k-org-bg); color: var(--si-k-org); font: var(--si-font-h4); }
.party-code.me { background: var(--si-brand); color: var(--si-white); }
.party-main { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 1px; }
.party-main b { overflow: hidden; color: var(--si-ink-1); font: var(--si-font-label); font-weight: 600; text-overflow: ellipsis; white-space: nowrap; }
.party-main > span { overflow: hidden; color: var(--si-ink-3); font: var(--si-font-caption); text-overflow: ellipsis; white-space: nowrap; }
.party-main em { color: var(--si-blue-ink); font-style: normal; font-weight: 600; }
.code-chip { font-family: var(--si-font-mono); }
.ibtn { flex: none; display: grid; place-items: center; width: 30px; height: 30px; border: 0; border-radius: var(--si-radius-sm); background: transparent; color: var(--si-ink-3); cursor: pointer; }
.ibtn:hover:not(:disabled) { background: var(--si-hover-bg); color: var(--si-ink-1); }
.ibtn:focus-visible { outline: 2px solid var(--si-focus); outline-offset: 1px; }
.party-edit { display: flex; flex-direction: column; gap: 8px; margin-top: 10px; padding-top: 10px; border-top: 1px solid var(--si-hair-faint); }
.pe-row { display: flex; align-items: center; gap: 8px; }
.pe-row > span:first-child { flex: none; width: 32px; color: var(--si-ink-3); font: var(--si-font-caption); }
.pe-row :deep(.el-input) { flex: 1; min-width: 0; }
.pe-more :deep(.el-select) { flex: 1; min-width: 0; }
.shorts { flex: 1; min-width: 0; display: flex; flex-wrap: wrap; gap: 6px; }
.short { display: inline-flex; align-items: center; gap: 2px; height: 24px; padding: 0 4px 0 10px; border-radius: var(--si-radius-badge); background: var(--si-k-org-bg); color: var(--si-k-org); font: var(--si-font-caption); }
.short button { display: grid; place-items: center; width: 18px; height: 18px; padding: 0; border: 0; border-radius: 50%; background: transparent; color: inherit; cursor: pointer; }
.short button:hover { background: color-mix(in srgb, currentColor 14%, transparent); }
.short-input { flex: 1 1 96px !important; min-width: 96px; }
.pe-foot { display: flex; justify-content: space-between; gap: 8px; }
.pe-foot :deep(.el-button + .el-button) { margin-left: 0; }
.our-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-top: 12px; padding-top: 12px; border-top: 1px solid var(--si-hair-faint); border-radius: 0; }
/* 需要你看一眼的 */
.unsure-list { margin: 8px 0 0; padding: 0; list-style: none; display: flex; flex-direction: column; gap: 2px; max-height: 300px; overflow-y: auto; }
.unsure { display: flex; align-items: center; gap: 10px; padding: 7px 8px; border-radius: var(--si-radius-control); cursor: pointer; transition: background-color var(--si-d2) var(--si-ease-out); }
.unsure:hover { background: var(--si-hover-bg); }
.unsure.selected { background: var(--si-select-bg); }
.unsure-main { flex: 1; min-width: 0; display: flex; flex-direction: column; }
.unsure-main b { overflow: hidden; color: var(--si-ink-1); font: var(--si-font-label); text-overflow: ellipsis; white-space: nowrap; }
.unsure-main span { overflow: hidden; color: var(--si-ink-3); font: var(--si-font-caption); text-overflow: ellipsis; white-space: nowrap; }
.kdot { flex: none; width: 8px; height: 8px; border-radius: 50%; background: var(--k); }
.k-org { --k: var(--si-k-org); } .k-person { --k: var(--si-k-person); } .k-other { --k: var(--si-k-other); }
/* 保留 / 遮住（V351-E-01）：选中的一侧按含义着色、加对勾并写“已保留 / 已遮住”，不只靠颜色区分。
   遮住是本机保护，用衡青；保留会把原文发给模型，用琥珀提醒。两列等宽，滑块平移，切换时宽度不跳。 */
.choice { position: relative; isolation: isolate; flex: none; display: inline-grid; grid-template-columns: 1fr 1fr; padding: 2px; border-radius: var(--si-radius-control); background: var(--si-sunken); }
.choice::before { content: ''; position: absolute; z-index: -1; top: 2px; bottom: 2px; left: 2px; width: calc(50% - 2px); border-radius: var(--si-radius-sm); background: var(--si-blue-tint); box-shadow: inset 0 0 0 1px var(--si-brand-line); transform: translateX(100%); transition: transform var(--si-d3) var(--si-ease-out), background-color var(--si-d2) var(--si-ease-out), box-shadow var(--si-d2) var(--si-ease-out); }
.choice.is-kept::before { transform: none; background: var(--si-warn-soft); box-shadow: inset 0 0 0 1px var(--si-warn-line); }
.ch { display: inline-flex; align-items: center; justify-content: center; gap: 3px; height: 26px; padding: 0 10px; border: 0; border-radius: var(--si-radius-sm); background: transparent; color: var(--si-ink-3); font: var(--si-font-label-sm); white-space: nowrap; cursor: pointer; transition: color var(--si-d2) var(--si-ease-out); }
.ch[aria-pressed="false"]:hover:not(:disabled) { color: var(--si-ink-1); }
.ch[aria-pressed="true"] { font-weight: 600; cursor: default; }
.is-kept .ch-keep { color: var(--si-warn-ink); }
.is-masked .ch-mask { color: var(--si-blue-ink); }
.ch:disabled { cursor: not-allowed; }
.row2 { display: flex; gap: 8px; margin-top: 10px; }
.row2 :deep(.el-button) { flex: 1; margin: 0; }
.auto { margin-top: 10px; padding-top: 8px; border-top: 1px solid var(--si-hair-faint); }
.auto-head { display: flex; align-items: center; gap: 4px; padding: 0; border: 0; background: none; color: var(--si-ink-3); font: var(--si-font-caption); cursor: pointer; }
.auto-head:hover { color: var(--si-ink-1); }
/* 文件 */
.files { margin: 0; padding: 0; list-style: none; display: flex; flex-direction: column; gap: 8px; }
.file { display: flex; flex-direction: column; gap: 4px; }
.file-line { display: flex; align-items: center; gap: 8px; }
.ftype { flex: none; display: grid; place-items: center; width: 30px; height: 30px; border-radius: var(--si-radius-sm); font: var(--si-font-badge); font-weight: 600; background: var(--si-k-org-bg); color: var(--si-k-org); }
.ftype--xls { background: var(--si-ok-soft); color: var(--si-ok-ink); }
.ftype--pdf { background: var(--si-danger-soft); color: var(--si-danger-ink); }
.ftype--img { background: var(--si-k-person-bg); color: var(--si-k-person); }
.ftype--audio { background: var(--si-m-meet-soft); color: var(--si-m-meet); }
.file-name { flex: 1; min-width: 0; overflow: hidden; font: var(--si-font-label); text-overflow: ellipsis; white-space: nowrap; }
.main-tag { flex: none; padding: 0 8px; border-radius: var(--si-radius-badge); background: var(--si-blue-tint); color: var(--si-blue-ink); font: var(--si-font-badge); font-style: normal; line-height: 20px; }
.doc-type { flex: 1; min-width: 0; }
.add-file { margin-top: 10px; }
/* 将要发送的 */
.send-list { margin: 0; padding: 0; list-style: none; display: flex; flex-direction: column; gap: 8px; color: var(--si-ink-2); font: var(--si-font-body); }
.send-list li { display: flex; align-items: center; gap: 8px; }
.send-list li > svg { flex: none; color: var(--si-ink-3); }
.send-list b { color: var(--si-ink-1); }
.send-list .t-ai { color: var(--si-ai); }
.chips-li { padding-left: 23px; }
.chips { display: flex; flex-wrap: wrap; gap: 4px; }
.chip { padding: 0 8px; border-radius: var(--si-radius-badge); background: var(--si-surface-2); color: var(--si-ink-3); font: var(--si-font-caption); }
.skip { margin-left: auto; }
.links { display: flex; flex-wrap: wrap; gap: 4px 14px; margin-top: 10px; }
.links :deep(.el-button + .el-button) { margin-left: 0; }
.settings { margin-top: 12px; padding-top: 12px; border-top: 1px solid var(--si-hair-faint); }
/* 栏底：提交条件 + 满宽主按钮 */
.rp-foot { flex: none; display: flex; flex-direction: column; gap: 8px; padding: 12px 2px 0; }
.receipt-alert { margin: 0; }
.checks { margin: 0; padding: 0; list-style: none; display: flex; flex-direction: column; gap: 4px; font: var(--si-font-caption); }
.checks li { display: flex; align-items: center; gap: 6px; }
.checks li > svg { flex: none; }
.checks .ok { color: var(--si-ink-2); } .checks .ok > svg { color: var(--si-brand-2); }
.checks .todo { color: var(--si-warn-ink); }
.checks .info { color: var(--si-ink-3); }
.cond-link { padding: 0; border: 0; background: none; color: inherit; font: inherit; text-decoration: underline dotted; text-underline-offset: 3px; cursor: pointer; }
.cond-link:hover { color: var(--si-blue-ink); }
.confirm-btn :deep(.el-button) { width: 100%; height: 46px; gap: 8px; border-radius: var(--si-radius-md); font: var(--si-font-label); font-size: calc(15px * var(--si-fs)); }
.confirm-btn :deep(.el-button > span) { gap: 8px; }
.submit-wait { display: flex; align-items: center; gap: 8px; margin: 0; font: var(--si-font-caption); color: var(--si-ink-2); }
.confirm-btn.flash :deep(.el-button) { box-shadow: 0 0 0 3px var(--si-focus); }
.outbound { white-space: pre-wrap; overflow-wrap: anywhere; font: var(--si-font-reading); }
.keys { width: 100%; border-collapse: collapse; }
.keys th { width: 130px; padding: 6px 0; text-align: left; font-family: var(--si-font-mono); font-weight: 600; }
.keys td { padding: 6px 0; }
.sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); }
</style>
