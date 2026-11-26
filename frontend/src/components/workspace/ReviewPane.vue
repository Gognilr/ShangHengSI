<script setup lang="ts">
import SiNotice from '@/components/ui/SiNotice.vue'
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { FileUp, ShieldCheck } from '@lucide/vue'
import ConfirmWorkbench from '@/components/ConfirmWorkbench.vue'
import ParsePhase from '@/components/workspace/ParsePhase.vue'
import { ApiError } from '@/api/http'
import { assistantApi, confirmationTask, type Session, type SessionFile } from '@/api/assistant'
import { modelsApi, type AvailableModel } from '@/api/models'
import { redactionApi, type Party } from '@/api/redaction'
import { RUN_ERROR_MESSAGES, runsApi, type RunCreated, type RunDetail, type RunListItem } from '@/api/runs'
import { MAX_NOTE_LENGTH, type Source, type Stance } from '@/api/tasks'
import EmptyState from '@/components/EmptyState.vue'
import LocalCheckList from '@/components/result/LocalCheckList.vue'
import ReviewCeremony from '@/components/result/ReviewCeremony.vue'
import RequirementsDrawer from '@/components/RequirementsDrawer.vue'
import SiSegmented from '@/components/ui/SiSegmented.vue'
import SiThinkingDots from '@/components/ui/SiThinkingDots.vue'
import RunResult from '@/components/workspace/RunResult.vue'
import ScopePicker from '@/components/workspace/ScopePicker.vue'
import type { ReviewProgress } from '@/utils/trip'
import { useAuthStore } from '@/stores/auth'
import { SOURCE_OPTIONS, STANCE_OPTIONS, toOptions } from '@/utils/labels'
import { nudgeField } from '@/utils/nudge'
import { loadDiff } from '@/utils/drafts'

/**
 * 工作台“全面审核”视图（V32-B-01 2）：解析 → 确认 → 审核中 → 结果，按会话数据决定。
 * 文件只上传一次、只确认一次（V321-E-06）：有待确认的文件时，确认页内嵌在本视图，立场、来源、模型在右栏底部，
 * 一个主按钮“确认发送并开始审核”同时完成确认与发起；没有待确认文件（已确认过）时才是设置页，只列本轮新增发送的内容。
 */
const props = defineProps<{ session: Session; fresh?: boolean; discussId?: string; active?: boolean }>()
const emit = defineEmits<{ refresh: []; add: []; pick: []; remove: [file: SessionFile]; discuss: [anchor: { id: string; no: string; title: string; runNo: number }]; closeDiscuss: []
  /** 顶部工具条的步骤条（澄明 8.4：进度只在工具条上） */
  progress: [p: ReviewProgress]; ask: [] }>()
const auth = useAuthStore()

// ---- 轮次 ----
const rounds = ref<RunListItem[]>([])
const roundsLoaded = ref(false)
const selectedRun = ref('')
const composing = ref(false)
const freshRun = ref(false)
const accepted = ref<RunCreated | null>(null)
let version = 0
async function loadRounds(select?: 'latest') {
  const current = ++version
  try {
    const list = (await assistantApi.runs(props.session.id)).items
    if (current !== version) return
    rounds.value = list
    // 列表按轮次倒序或正序都不假设：取 run_no 最大者为最新
    const latest = [...list].sort((a, b) => b.run_no - a.run_no)[0]?.id ?? ''
    if (select === 'latest' || !list.some(r => r.id === selectedRun.value)) selectedRun.value = latest
  } catch { /* 读不到时保持设置视图，用户可刷新 */ }
  finally { if (current === version) roundsLoaded.value = true }
}
watch(() => props.session.id, () => { rounds.value = []; roundsLoaded.value = false; selectedRun.value = ''; composing.value = false; accepted.value = null; void loadRounds() }, { immediate: true })

// 审核结束（review_running 由真变假）：取新轮次并显示结果
watch(() => props.session.review_running, async (running, was) => {
  if (was && !running) { running_.value = null; composing.value = false; freshRun.value = true; await loadRounds('latest') }
  else if (running) { composing.value = false; void pollRun() }
})

watch(() => [props.session.review_running, props.session.run_count] as const, ([running, count]) => {
  if (!accepted.value || (!running && (count ?? 0) < accepted.value.run_no)) return
  accepted.value = null
  if (!running) { composing.value = false; freshRun.value = true; void loadRounds('latest') }
})

// ---- 审核中：轮询最新轮次进度（会话详情由页面轮询）----
const running_ = ref<RunDetail | null>(null)
let timer: ReturnType<typeof setTimeout> | undefined
async function pollRun() {
  clearTimeout(timer)
  if (!props.session.review_running) return
  try {
    const list = (await assistantApi.runs(props.session.id)).items
    const latest = [...list].sort((a, b) => b.run_no - a.run_no)[0]
    if (latest) running_.value = await runsApi.get(latest.id)
  } catch { /* 网络中断时沿用上次进度 */ }
  if (props.session.review_running) timer = setTimeout(pollRun, 2000)
}
onBeforeUnmount(() => clearTimeout(timer))
if (props.session.review_running) void pollRun()
const ceremonyTask = computed(() => ({ ...confirmationTask(props.session), status: 'reviewing' as const,
  latest_run: running_.value ? { id: running_.value.id, run_no: running_.value.run_no, status: running_.value.status } : accepted.value,
  queue_position: running_.value?.queue_position ?? null }))

// ---- 设置 ----
const files = computed(() => props.session.files.filter(f => !f.media))
const confirmedFiles = computed(() => files.value.filter(f => !!f.confirmed_at))
const pendingFiles = computed(() => files.value.filter(f => !f.confirmed_at))
// 可参与本轮的文件：已确认的，加上已解析、待确认的（确认页里一并勾选）
const candidates = computed(() => files.value.filter(f => !!f.confirmed_at || f.parse_state === 'done'))
const picked = ref<string[]>([])
const mainId = ref('')
const known = new Set<string>()
// 稿次（V35-D-02）：每条链只默认勾选最新一稿；文件被下一稿取代时取消勾选，主文件随之换成最新一稿
const superseded = computed(() => new Set(files.value.map(f => f.prev_file_id).filter((id): id is string => !!id)))
const knownSuperseded = new Set<string>()
function latestDraft(id: string) {
  let cur = id
  const seen = new Set([id])
  for (;;) { const next = files.value.find(f => f.prev_file_id === cur); if (!next || seen.has(next.id)) return cur; cur = next.id; seen.add(cur) }
}
watch(() => candidates.value.map(f => `${f.id}:${f.prev_file_id ?? ''}`).join(','), () => {
  const ids = candidates.value.map(f => f.id)
  const fresh = [...superseded.value].filter(id => !knownSuperseded.has(id))
  fresh.forEach(id => knownSuperseded.add(id))
  // 新确认的文件默认选中（已被下一稿取代的除外）；已不存在的、刚被取代的去掉
  picked.value = [...picked.value.filter(id => ids.includes(id) && !fresh.includes(id)),
    ...ids.filter(id => !picked.value.includes(id) && !known.has(id) && !superseded.value.has(id))]
  ids.forEach(id => known.add(id))
  if (fresh.includes(mainId.value) || !picked.value.includes(mainId.value)) {
    const mainFile = candidates.value.find(f => f.is_main)
    const preferred = latestDraft(mainId.value && ids.includes(mainId.value) ? mainId.value : mainFile?.id ?? '')
    mainId.value = picked.value.includes(preferred) ? preferred : picked.value[0] ?? ''
  }
}, { immediate: true })
const diffLoader = (fid: string, prev: string) => loadDiff(props.session.id, fid, prev)
const router = useRouter()
// 审核范围（V35-D-03）：主文件有上一稿时可以只审变化
const scope = ref<'full' | 'delta'>('full')
const previousId = computed(() => files.value.find(f => f.id === mainId.value)?.prev_file_id)
const hasPrev = computed(() => !!previousId.value)
const scopeReady = ref(true)
watch(hasPrev, has => { if (!has) scope.value = 'full' })
const runScope = () => hasPrev.value ? scope.value : 'full'
// 只审变化时本轮只发主文件，附件不重发
const runFiles = () => runScope() === 'delta' ? [mainId.value] : picked.value
const hintClosed = ref('')
watch(picked, ids => { if (!ids.includes(mainId.value)) mainId.value = ids[0] ?? '' })

const stance = ref<Stance | ''>('')
const source = ref<Source>('outgoing')
const note = ref('')
const noteOpen = ref(false)
const reqDrawer = ref(false)
const stanceOptions = toOptions(STANCE_OPTIONS)
const sourceOptions = toOptions(SOURCE_OPTIONS)
// 文件来源沿用上次选择；立场由本次我方标签带出或由用户选择。
const prefKey = () => `aireview:last-choice:${auth.user?.id ?? ''}`
try {
  const saved = JSON.parse(localStorage.getItem(prefKey()) ?? 'null') as { stance?: string; source?: string } | null
  if (saved?.source && saved.source in SOURCE_OPTIONS) source.value = saved.source as Source
} catch { /* 浏览器禁用本地存储时用默认值 */ }
function savePrefs() { try { localStorage.setItem(prefKey(), JSON.stringify({ stance: stance.value, source: source.value })) } catch { /* 忽略 */ } }

const parties = ref<Party[]>([])
const partiesLoading = ref(false), partiesSaving = ref(false), partiesError = ref('')
const setupOur = computed(() => parties.value.find(p => p.is_our_party))
// 确认页里的我方由工作台提供（只选一次）
const confirmOur = ref<Party | null>(null)
const ourParty = computed(() => stage.value === 'confirm' ? confirmOur.value ?? undefined : setupOur.value)
const inferredStance = computed<Stance | ''>(() => ourParty.value?.role_label === '买方' ? 'purchase' : ourParty.value?.role_label === '卖方' ? 'sales' : '')
const autoStance = ref(false)
let partiesSeq = 0
let lastOur = ''
async function loadParties() {
  const current = ++partiesSeq
  partiesLoading.value = true; partiesError.value = ''
  try {
    const result = await redactionApi.parties(props.session.id)
    if (current !== partiesSeq) return
    parties.value = result.items
    const key = `${ourParty.value?.id ?? ''}:${ourParty.value?.role_label ?? ''}`
    if (key !== lastOur) {
      lastOur = key
      stance.value = inferredStance.value
      autoStance.value = !!stance.value
    }
  } catch (e) { if (current === partiesSeq) partiesError.value = e instanceof ApiError ? e.message : '主体加载失败，请刷新页面' }
  finally { if (current === partiesSeq) partiesLoading.value = false }
}
async function selectOur(id: string) {
  if (partiesSaving.value) return
  partiesSaving.value = true; partiesError.value = ''; previewVersion.value = null
  try {
    const result = await redactionApi.saveParties(props.session.id, parties.value.map(p => ({ id: p.id, is_our_party: p.id === id, country: p.country?.trim() || '国家未知', org_type: p.org_type?.trim() || '类型未知' })))
    parties.value = result.items
    lastOur = `${ourParty.value?.id ?? ''}:${ourParty.value?.role_label ?? ''}`
    stance.value = inferredStance.value; autoStance.value = !!stance.value
    emit('refresh')
    if (stance.value) await loadPreview()
  } catch (e) { await loadParties(); partiesError.value = e instanceof ApiError ? e.message : '我方保存失败，请重试' }
  finally { partiesSaving.value = false }
}
function chooseStance(value: string) { if (value in STANCE_OPTIONS) { stance.value = value as Stance; autoStance.value = false } }

const models = ref<AvailableModel[]>([])
const modelId = ref<number>()
const modelError = ref('')
const modelsLoading = ref(false)
async function loadModels() {
  if (props.session.policy === 'local_only' || models.value.length) return
  modelsLoading.value = true; modelError.value = ''
  try {
    models.value = (await modelsApi.list('review')).items
    modelId.value = models.value.find(m => m.id === modelId.value)?.id ?? models.value.find(m => m.is_default)?.id ?? models.value[0]?.id
    if (!modelId.value) modelError.value = '没有可用的审核模型，请联系管理员'
  } catch (e) { modelError.value = e instanceof ApiError ? e.message : '模型列表加载失败，请刷新页面' }
  finally { modelsLoading.value = false }
}

// 本轮外发预览由服务端按所选范围生成
const subjects = ref<string[]>([])
const deltaPreview = ref('')
const previewVersion = ref<number | null>(null)
const previewError = ref('')
// 按当前所选的立场与文件取预览，与实际发送一致；所选变化时重取，取到之前不能确认
let previewSeq = 0
async function loadPreview() {
  const current = ++previewSeq
  previewError.value = ''; previewVersion.value = null; deltaPreview.value = ''
  if (!stance.value) { subjects.value = []; return }
  try {
    const data = await assistantApi.outboundReview(props.session.id, {
      stance: stance.value, file_ids: runFiles(), scope: runScope(), main_file_id: mainId.value,
    })
    if (current !== previewSeq) return
    if (runScope() === 'delta' && !data.delta?.available) {
      previewError.value = data.delta?.reason || '当前不能只审变化，请改为全文重审'
      return
    }
    previewVersion.value = data.material_version
    if (runScope() === 'delta') deltaPreview.value = data.text
    const line = /【主体说明】([^\n]*)/.exec(data.text)?.[1] ?? ''
    subjects.value = line && line !== '无' ? line.split('；').filter(Boolean) : []
  } catch (e) {
    if (current !== previewSeq) return
    previewError.value = e instanceof ApiError && e.code === 'OUR_PARTY_REQUIRED' ? '请在下方主体说明中选择我方'
      : e instanceof ApiError ? e.message : '读取本轮发送内容失败，请刷新页面'
  }
}

const stage = computed<'loading' | 'running' | 'result' | 'local' | 'parsing' | 'confirm' | 'setup'>(() => {
  if (props.session.review_running || accepted.value) return 'running'
  if (props.session.policy === 'local_only') return 'local'
  if (!roundsLoaded.value) return 'loading'
  const reading = !(rounds.value.length && !composing.value)  // 正在看结果时，新加的文件不打断阅读，点“新一轮”再进入
  if (reading && !props.session.original_deleted) {
    if (props.session.status === 'parse_failed' && pendingFiles.value.length) return 'parsing'
    if (props.session.status === 'parsing' && files.value.length) return 'parsing'
    if (props.session.status === 'awaiting_confirm' && pendingFiles.value.length) return 'confirm'
  }
  return rounds.value.length && !composing.value ? 'result' : 'setup'
})
// 在审核视图里新加了文件（结果页上直接加文件）：自动进入新一轮，看解析与确认
watch(() => pendingFiles.value.length, count => { if (count && props.active && rounds.value.length && !props.session.review_running) composing.value = true })
const canConfigure = computed(() => stage.value === 'setup' && ['active', 'awaiting_confirm'].includes(props.session.status))
watch(() => [props.session.id, props.session.status, props.session.material_version, stage.value].join(':'), () => {
  if (stage.value === 'setup' && ['active', 'awaiting_confirm'].includes(props.session.status)) void loadParties()
}, { immediate: true })
watch([stage, () => props.session.material_version, () => confirmedFiles.value.length, stance, scope, mainId, previousId, () => picked.value.join(',')], ([s]) => {
  if (s === 'setup' && picked.value.length) { void loadModels(); void loadPreview() }
  if (s === 'confirm') void loadModels()
}, { immediate: true })

// 未满足的条件；field 表示缺的是用户该选的项：按钮保持可点，点击定位过去（V33-C-01）
type Missing = 'files' | 'our' | 'stance' | 'model'
interface Check { text: string; field?: Missing }
const setupCheck = computed<Check>(() => {
  if (props.session.original_deleted) return { text: '原件已按保留期限删除，不能再审核' }
  if (props.session.generating) return { text: '回复生成中，完成后再发起审核' }
  if (props.session.status === 'parsing') return { text: '文件正在解析，请稍候' }
  if (!confirmedFiles.value.length) return { text: '请先确认脱敏' }
  if (!picked.value.length) return { text: '请至少选择一个文件', field: 'files' }
  if (!mainId.value) return { text: '请选择主文件', field: 'files' }
  if (partiesLoading.value || partiesSaving.value) return { text: '正在读取或保存主体信息' }
  if (partiesError.value) return { text: partiesError.value }
  if (!ourParty.value) return { text: '请在主体说明中选择我方', field: parties.value.length ? 'our' : undefined }
  if (!stance.value) return { text: '请选择业务立场', field: 'stance' }
  if (modelsLoading.value) return { text: '正在加载模型列表' }
  if (modelError.value) return { text: modelError.value }
  if (!modelId.value) return { text: '请选择审核模型', field: 'model' }
  if (hasPrev.value && !scopeReady.value) return { text: '正在读取审核范围' }
  if (previewVersion.value === null) return { text: previewError.value || '正在读取本轮发送内容' }
  return { text: '' }
})
const blocker = computed(() => setupCheck.value.text)
const fieldEls: Record<'setup' | 'confirm', Partial<Record<Missing, HTMLElement>>> = { setup: {}, confirm: {} }
function fieldRef(where: 'setup' | 'confirm', field: Missing) {
  return (el: unknown) => { fieldEls[where][field] = el instanceof HTMLElement ? el : undefined }
}
function onStartClick() {
  const field = setupCheck.value.field
  if (field) nudgeField(fieldEls.setup[field])
  else void start()
}
// ---- 确认页（有待确认文件）：确认 + 发起一次完成（V321-E-06）----
watch(confirmOur, party => {
  const key = `${party?.id ?? ''}:${party?.role_label ?? ''}`
  if (key === lastOur) return
  lastOur = key
  stance.value = inferredStance.value; autoStance.value = !!stance.value
})
const confirmCheck = computed<Check>(() => {
  if (props.session.generating) return { text: '回复生成中，完成后再发起审核' }
  if (!picked.value.length) return { text: '请至少选择一个文件' }
  if (!mainId.value) return { text: '请选择主文件' }
  if (!stance.value) return { text: '请选择业务立场', field: 'stance' }
  if (modelsLoading.value) return { text: '正在加载模型列表' }
  if (modelError.value) return { text: modelError.value }
  if (!modelId.value) return { text: '请选择审核模型', field: 'model' }
  if (hasPrev.value && !scopeReady.value) return { text: '正在读取审核范围' }
  return { text: '' }
})
const confirmBlocker = computed(() => confirmCheck.value.text)
function nudgeConfirm() { const field = confirmCheck.value.field; if (field) nudgeField(fieldEls.confirm[field]) }
const modelName = computed(() => models.value.find(m => m.id === modelId.value)?.display_name)
async function submitConfirm() {
  if (!stance.value) return
  const trimmed = note.value.trim()
  const created = await assistantApi.createRun(props.session.id, {
    stance: stance.value, source: source.value, model_id: modelId.value, user_note: trimmed || null,
    file_ids: runFiles(), main_file_id: mainId.value, expected_material_version: previewVersion.value ?? undefined, confirm_pending: true,
    scope: runScope(),
  })
  savePrefs()
  accepted.value = created
  emit('refresh')
  return created
}
const submitting = ref(false)
const formError = ref('')
// 提交等待：按钮不转圈，旁边墨点 + 当前步骤 + 已用秒数
const submitSeconds = ref(0)
let submitTimer: number | undefined
onBeforeUnmount(() => window.clearInterval(submitTimer))
async function start() {
  if (blocker.value || submitting.value || !stance.value) return
  submitting.value = true; formError.value = ''
  submitSeconds.value = 0
  submitTimer = window.setInterval(() => { submitSeconds.value += 1 }, 1000)
  try {
    const trimmed = note.value.trim()
    accepted.value = await assistantApi.createRun(props.session.id, {
      stance: stance.value, source: source.value, model_id: modelId.value, user_note: trimmed || null,
      file_ids: runFiles(), main_file_id: mainId.value, expected_material_version: previewVersion.value ?? undefined,
      scope: runScope(),
    })
    savePrefs()
    emit('refresh')
  } catch (e) {
    const code = e instanceof ApiError ? e.code : ''
    formError.value = e instanceof ApiError ? RUN_ERROR_MESSAGES[code] ?? e.message : '发起审核失败，请重试'
    if (code === 'MATERIAL_CHANGED') { await loadPreview(); formError.value = '材料有变化，已更新下方“本轮发送的内容”，请核对后再确认' }
    if (['MATERIAL_CHANGED', 'SESSION_BUSY', 'TASK_STATE_CONFLICT', 'FILES_PARSING'].includes(code)) emit('refresh')
    if (code === 'OUR_PARTY_REQUIRED') ElMessage.warning('请在主体说明中选择我方')
  } finally { submitting.value = false; window.clearInterval(submitTimer) }
}
function badge(file: SessionFile) { return file.ext.toUpperCase().slice(0, 4) }
function newRound() { composing.value = true; note.value = ''; noteOpen.value = false; formError.value = '' }
function backToResult() { composing.value = false }
function openConfirm() { if (!props.session.review_running && !accepted.value) composing.value = true }
const workbench = ref<InstanceType<typeof ConfirmWorkbench>>()
defineExpose({ openConfirm, focusConfirm: () => workbench.value?.focusConfirm() })
const loadLocal = () => assistantApi.localFindings(props.session.id)

// ---- 顶部步骤条 ----
const blockedN = ref(0)
const resultRun = ref<RunDetail | null>(null)
const progress = computed<ReviewProgress>(() => {
  const s = stage.value
  if (s === 'running') {
    const r = running_.value
    return { stage: 'running', since: r?.started_at ?? r?.queued_at ?? null, queue: r?.status === 'queued' ? r.queue_position : null }
  }
  if (s === 'result') {
    const r = resultRun.value
    const start = r ? Date.parse(r.started_at ?? r.queued_at) : NaN, end = r?.finished_at ? Date.parse(r.finished_at) : NaN
    return { stage: 'result', status: r?.status, seconds: Number.isFinite(start) && Number.isFinite(end) ? (end - start) / 1000 : null }
  }
  return { stage: s, blocked: s === 'confirm' ? blockedN.value : 0 }
})
watch(progress, p => emit('progress', p), { immediate: true, deep: true })
</script>

<template>
  <div class="review-pane">
    <div v-if="stage === 'loading'" class="center"><el-skeleton :rows="4" animated /></div>

    <!-- 审核中 -->
    <section v-else-if="stage === 'running'" class="panel running">
      <ReviewCeremony :task="ceremonyTask" :run="running_" />
    </section>

    <!-- 不外发的会话：只有本地校验 -->
    <section v-else-if="stage === 'local'" class="local">
      <SiNotice type="info" :closable="false" title="该会话的外发策略为“不外发，仅本地校验”：文件不离开本系统，只做金额、日期等本地校验，不产生 AI 审核意见。" />
      <LocalCheckList :loader="loadLocal" :load-key="session.id" readonly-locations />
    </section>

    <!-- 解析阶段 -->
    <ParsePhase v-else-if="stage === 'parsing'" :session="session" @remove="emit('remove', $event)" />

    <!-- 确认页（内嵌）：确认脱敏 + 审核设置，一个主按钮 -->
    <section v-else-if="stage === 'confirm'" class="confirm-stage">
      <div v-if="rounds.length" class="back"><el-button link type="primary" @click="backToResult">← 返回第 {{ [...rounds].sort((a, b) => b.run_no - a.run_no)[0]?.run_no }} 轮结果</el-button></div>
      <div v-if="session.draft_hint && hintClosed !== session.id" class="draft-hint" role="status">
        <span class="hint-text">这像是《{{ session.draft_hint.file_name }}》的新一稿。要和上一稿对比，请在那次审核里点“传新一稿”传入。</span>
        <el-button size="small" link type="primary" @click="router.push({ path: `/assistant/${session.draft_hint.session_id}`, query: { mode: 'review' } })">打开那次审核</el-button>
        <el-button size="small" link aria-label="关闭提示" @click="hintClosed = session.id">×</el-button>
      </div>
      <ConfirmWorkbench ref="workbench" :key="`${session.id}:${pendingFiles.map(f => f.id).join(',')}`" class="confirm-wb" :task="confirmationTask(session)" mode="session-review" require-our-party :allow-ocr="(session.run_count ?? 0) === 0"
        v-model:picked="picked" v-model:main-id="mainId" :diff-loader="diffLoader" :stance="stance" :file-ids="runFiles()" :scope="runScope()" :model-name="modelName" :blocker="confirmBlocker" :blocker-choice="!!confirmCheck.field" :submit="submitConfirm"
        @our="confirmOur = $event" @preview="previewVersion = $event" @stale="emit('refresh')" @nudge="nudgeConfirm" @blocked="blockedN = $event">
        <template #settings>
          <div class="cs">
            <div :ref="fieldRef('confirm', 'stance')" class="cs-row"><span class="setting-label">业务立场<span v-if="!stance" class="si-required-tag">请选择</span></span><SiSegmented :class="{ 'si-required-empty': !stance }" :model-value="stance" :options="stanceOptions" size="small" ariaLabel="业务立场" @update:model-value="chooseStance" /></div>
            <p v-if="autoStance" class="hint cs-hint">按我方为{{ ourParty?.role_label }}带出</p>
            <p v-else-if="inferredStance && stance && stance !== inferredStance" class="hint cs-hint">所选立场与我方的{{ ourParty?.role_label }}标签不一致，请核对。</p>
            <div class="cs-row"><span class="setting-label">文件来源</span><SiSegmented v-model="source" :options="sourceOptions" size="small" ariaLabel="文件来源" /></div>
            <ScopePicker v-if="previousId && stance" v-model="scope" v-model:ready="scopeReady" compact :session-id="session.id" :stance="stance" :file-ids="picked" :main-id="mainId" :previous-id="previousId" :material-version="previewVersion" include-pending />
            <div :ref="fieldRef('confirm', 'model')" class="cs-row">
              <span class="setting-label">审核模型<span v-if="!modelId && models.length > 1" class="si-required-tag">请选择</span></span>
              <span v-if="models.length === 1" class="cs-model">{{ models[0]!.display_name }}</span>
              <el-select v-else v-model="modelId" :class="{ 'si-required-empty': !modelId && models.length > 1 }" size="small" placeholder="选择审核模型" aria-label="审核模型" :loading="modelsLoading" :disabled="!models.length"><el-option v-for="m in models" :key="m.id" :value="m.id" :label="m.display_name" /></el-select>
            </div>
            <el-input v-if="noteOpen" v-model="note" type="textarea" aria-label="本次补充说明" :rows="2" :maxlength="MAX_NOTE_LENGTH" show-word-limit placeholder="如：这是给土耳其客户的报价，请重点看付款条款" />
            <div class="links"><el-button v-if="!noteOpen" link type="primary" size="small" @click="noteOpen = true">＋ 添加补充说明</el-button><el-button link type="primary" size="small" :disabled="!stance" @click="reqDrawer = true">查看审核要求</el-button></div>
          </div>
        </template>
      </ConfirmWorkbench>
    </section>

    <!-- 结果 -->
    <RunResult v-else-if="stage === 'result'" :session="session" :rounds="rounds" :selected-run="selectedRun" :fresh="freshRun"
      :discuss-id="discussId" @select-run="selectedRun = $event; freshRun = false" @new-round="newRound" @discuss="emit('discuss', $event)" @close-discuss="emit('closeDiscuss')" @loaded="resultRun = $event" @ask="emit('ask')" @uploaded="emit('refresh')">
      <!-- 讨论区由工作台提供（消息与轮询只在工作台一处），这里原样转给结果页右栏（V321-E-02） -->
      <template #discussion="slotProps"><slot name="discussion" v-bind="slotProps" /></template>
    </RunResult>

    <!-- 设置 -->
    <section v-else class="setup">
      <div class="setup-inner">
        <EmptyState v-if="!files.length" :icon="FileUp" title="先加入要审核的材料" description="文件只需上传一次，在本机解析、确认脱敏后，既可以直接提问，也可以发起全面审核。">
          <el-button type="primary" @click="emit('pick')">加入文件</el-button>
        </EmptyState>
        <template v-else>
          <div v-if="rounds.length" class="back"><el-button link type="primary" @click="backToResult">← 返回第 {{ [...rounds].sort((a, b) => b.run_no - a.run_no)[0]?.run_no }} 轮结果</el-button></div>

          <div class="grid">
            <div :ref="fieldRef('setup', 'files')" class="col">
              <h2>本轮审核的文件</h2>
              <el-checkbox-group v-model="picked" class="file-list">
                <label v-for="f in confirmedFiles" :key="f.id" class="file-row" :class="{ 'is-main': f.id === mainId }">
                  <el-checkbox :value="f.id" :aria-label="`审核 ${f.original_name}`" />
                  <span class="file-badge">{{ badge(f) }}</span>
                  <span class="file-name" :title="f.original_name">{{ f.original_name }}</span>
                  <em v-if="f.id === mainId" class="main-tag">主文件</em>
                  <el-button v-else-if="picked.includes(f.id)" link type="primary" @click.prevent="mainId = f.id">设为主文件</el-button>
                </label>
              </el-checkbox-group>
              <p v-if="!confirmedFiles.length" class="hint">确认脱敏后，文件会出现在这里。</p>
            </div>
            <div class="col">
              <h2>审核设置</h2>
              <div :ref="fieldRef('setup', 'stance')" class="setting"><span class="setting-label">业务立场<span v-if="!stance" class="si-required-tag">请选择</span></span><SiSegmented :class="{ 'si-required-empty': !stance }" :model-value="stance" :options="stanceOptions" ariaLabel="业务立场" @update:model-value="chooseStance" /><p v-if="autoStance" class="hint">按我方为{{ ourParty?.role_label }}带出</p><p v-else-if="inferredStance && stance && stance !== inferredStance" class="hint">所选立场与我方的{{ ourParty?.role_label }}标签不一致，请核对。</p></div>
              <div class="setting"><span class="setting-label">文件来源</span><SiSegmented v-model="source" :options="sourceOptions" ariaLabel="文件来源" /></div>
              <div v-if="previousId && stance" class="setting"><ScopePicker v-model="scope" v-model:ready="scopeReady" :session-id="session.id" :stance="stance" :file-ids="picked" :main-id="mainId" :previous-id="previousId" :material-version="previewVersion" /></div>
              <div :ref="fieldRef('setup', 'model')" class="setting">
                <span class="setting-label">审核模型<span v-if="!modelId && models.length > 1" class="si-required-tag">请选择</span></span>
                <span v-if="models.length === 1">{{ models[0]!.display_name }}</span>
                <el-select v-else v-model="modelId" :class="{ 'si-required-empty': !modelId && models.length > 1 }" placeholder="选择审核模型" aria-label="审核模型" :loading="modelsLoading" :disabled="!models.length">
                  <el-option v-for="m in models" :key="m.id" :value="m.id" :label="m.display_name" />
                </el-select>
                <p v-if="modelError" class="hint">{{ modelError }}</p>
              </div>
              <div class="setting">
                <el-input v-if="noteOpen" v-model="note" type="textarea" aria-label="本次补充说明" :rows="3" :maxlength="MAX_NOTE_LENGTH" show-word-limit placeholder="如：这是给土耳其客户的报价，请重点看付款条款" />
                <div class="links"><el-button v-if="!noteOpen" link type="primary" @click="noteOpen = true">＋ 添加补充说明</el-button><el-button link type="primary" :disabled="!stance" @click="reqDrawer = true">查看审核要求</el-button></div>
              </div>
            </div>
          </div>

          <div class="sent" aria-label="本轮发送的内容">
            <div class="sent-head"><ShieldCheck :size="16" :stroke-width="1.75" /><h2>本轮发送的内容</h2></div>
            <p v-if="runScope() === 'delta'" class="hint">只审变化：只发主文件变化所在的条款、上一稿对应文字与上轮未解决的问题，均为已核对的脱敏结果；本轮新增发送的只有：</p>
            <details v-if="deltaPreview" class="delta-preview">
              <summary>查看将发送的变化与问题</summary>
              <pre>{{ deltaPreview }}</pre>
            </details>
            <p v-else class="hint">已确认的 {{ picked.length }} 个文件按已核对的脱敏结果发送，不再逐个核对；本轮新增发送的只有：</p>
            <div v-if="parties.length" :ref="fieldRef('setup', 'our')" class="setting"><span class="setting-label">选择我方<span v-if="!ourParty" class="si-required-tag">请选择</span></span><el-radio-group :class="{ 'si-required-empty': !ourParty }" :model-value="ourParty?.id ?? ''" :disabled="partiesLoading || partiesSaving || submitting" @update:model-value="selectOur(String($event))"><el-radio v-for="p in parties" :key="p.id" :value="p.id">{{ p.name }}（{{ p.role_label }}，{{ p.code }}）</el-radio></el-radio-group></div>
            <p v-if="partiesError" class="hint">{{ partiesError }}</p>
            <ul>
              <li><strong>主体说明</strong>：<template v-if="subjects.length"><span v-for="s in subjects" :key="s" class="subject">{{ s }}</span></template><template v-else>无</template></li>
              <li><strong>补充说明</strong>：<template v-if="note.trim()">{{ note.trim().length }} 字，发送前在本机脱敏</template><template v-else>未填写</template></li>
            </ul>
            <p class="hint">名称、金额、账号等已替换为代号；模型回复在本机还原。立场与来源以上方所选为准。</p>
          </div>
          <SiNotice v-if="formError" type="error" :closable="false" :title="formError" />
          <div class="foot">
            <span v-if="submitting" class="hint submit-wait si-wait" role="status"><SiThinkingDots /><span><span class="si-shimmer">本机正在做出口复检，通过后交给模型</span>，已用 {{ submitSeconds }} 秒</span></span>
            <span v-else-if="blocker" class="hint">{{ blocker }}</span>
            <el-button type="primary" :disabled="submitting || (!!blocker && !setupCheck.field) || !canConfigure" @click="onStartClick">{{ submitting ? '正在提交…' : '确认发送并开始审核' }}</el-button>
          </div>
        </template>
      </div>
    </section>
    <RequirementsDrawer v-if="stance" v-model="reqDrawer" :stance="stance" :source="source" :user-note="note" />
  </div>
</template>

<style scoped>
.delta-preview { margin: 8px 0; color: var(--si-ink-2); }
.delta-preview summary { cursor: pointer; }
.delta-preview pre { max-height: 18rem; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere; font: var(--si-font-reading); }
.review-pane { flex: 1; min-height: 0; display: flex; flex-direction: column; }
.center { padding: 32px; }
.running { flex: 1; min-height: 0; overflow: hidden; padding: 16px 20px; }
.local { flex: 1; min-height: 0; overflow-y: auto; padding: 16px 20px; display: flex; flex-direction: column; gap: 12px; }
.confirm-stage { flex: 1; min-height: 0; display: flex; flex-direction: column; padding: 0 20px; }
.confirm-stage > .back { padding: 8px 0 0; margin: 0; }
.confirm-wb { flex: 1; min-height: 0; }
.draft-hint { flex: none; display: flex; align-items: center; gap: 8px; margin-top: 8px; padding: 6px 12px; border-radius: var(--si-radius-card); background: var(--si-blue-tint); color: var(--si-blue-ink); }
.hint-text { flex: 1; min-width: 0; font: var(--si-font-label); }
.cs { display: flex; flex-direction: column; gap: 8px; padding-bottom: 10px; margin-bottom: 8px; border-bottom: 1px solid var(--si-hair); }
.cs-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.cs-row :deep(.el-select) { width: 200px; }
.cs-model { font: var(--si-font-body); }
.cs-hint { margin: -4px 0 0; text-align: right; }
.cs :deep(.el-textarea) { width: 100%; }
.setup { flex: 1; min-height: 0; overflow-y: auto; padding: 20px; }
.setup-inner { display: flex; flex-direction: column; gap: 16px; max-width: 920px; margin: 0 auto; }
.back { margin-bottom: -4px; }
.grid { display: grid; grid-template-columns: minmax(0, 1fr) 320px; gap: 24px; }
.col { display: flex; flex-direction: column; gap: 14px; min-width: 0; }
h2 { margin: 0; font: var(--si-font-h4); }
.file-list { display: flex; flex-direction: column; gap: 8px; }
.file-row { display: flex; align-items: center; gap: 10px; padding: 8px 12px; border: 1px solid var(--el-border-color-light); border-radius: var(--si-radius-sm); cursor: pointer; }
.file-row.is-main { background: var(--el-color-primary-light-9); border-color: var(--el-color-primary-light-7); }
.file-row :deep(.el-checkbox) { height: auto; }
.file-badge { flex: none; display: grid; place-items: center; min-width: 36px; height: 28px; padding: 0 6px; border-radius: var(--si-radius-tag); background: var(--el-color-primary-light-9); color: var(--el-color-primary); font: var(--si-font-label-sm); }
.file-name { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font: var(--si-font-body); }
.main-tag { padding: 0 6px; border-radius: 4px; background: var(--el-color-primary-light-9); color: var(--el-color-primary); font: var(--si-font-label-sm); font-style: normal; }
.setting { display: flex; flex-direction: column; align-items: flex-start; gap: 6px; }
.setting :deep(.el-select), .setting :deep(.el-textarea) { width: 100%; }
.setting-label { color: var(--si-ink-3); font: var(--si-font-label-sm); }
.links { display: flex; gap: 16px; }
.sent { padding: 14px 16px; border: 1px solid var(--si-hair); border-radius: var(--si-radius-card); background: var(--si-surface-1); }
.sent-head { display: flex; align-items: center; gap: 8px; color: var(--si-blue-ink); }
.sent p { margin: 6px 0; }
.sent ul { margin: 6px 0; padding-left: 18px; }
.sent li { margin: 4px 0; overflow-wrap: anywhere; }
.subject { display: inline-block; margin-right: 8px; padding: 0 6px; border-radius: 4px; background: var(--si-surface-2); font: var(--si-font-caption); }
.foot { display: flex; align-items: center; justify-content: flex-end; gap: 12px; padding-top: 12px; border-top: 1px solid var(--el-border-color-lighter); }
.submit-wait { display: inline-flex; align-items: center; gap: 8px; }
</style>
