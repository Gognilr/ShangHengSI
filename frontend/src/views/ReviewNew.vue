<script setup lang="ts">
import WorkFolderBar from '@/components/WorkFolderBar.vue'
import { Trash2, Upload } from '@lucide/vue'
import { ElMessage } from 'element-plus'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ApiError } from '@/api/http'
import { modelsApi, type AvailableModel } from '@/api/models'
import { redactionApi } from '@/api/redaction'
import { RUN_ERROR_MESSAGES, runsApi, type RunDetail } from '@/api/runs'
import {
  ALLOWED_EXTS,
  MAX_FILE_MB,
  MAX_FILES,
  MAX_NOTE_LENGTH,
  TASK_ERROR_MESSAGES,
  checkFiles,
  tasksApi,
  type TaskFile,
  type Policy,
  type Source,
  type Stance,
  type TaskDetail,
  type DuplicateFile,
} from '@/api/tasks'
import RequirementsDrawer from '@/components/RequirementsDrawer.vue'
import ConfirmStep from '@/components/ConfirmStep.vue'
import StepBar from '@/components/StepBar.vue'
import SiIsland from '@/components/ui/SiIsland.vue'
import SiSegmented from '@/components/ui/SiSegmented.vue'
import { usePendingDeletes } from '@/composables/usePendingDeletes'
import { useLoop } from '@/utils/motion'
import { formatDateTime } from '@/utils/time'
import { useAuthStore } from '@/stores/auth'
import {
  SOURCE_OPTIONS,
  STANCE_OPTIONS,
  runStatusLabel,
  toOptions,
} from '@/utils/labels'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

// ---- 步骤：0 上传与设置，1 确认脱敏，2 审核中 ----
const step = ref(0)
const task = ref<TaskDetail | null>(null)
const loadError = ref('')
const activeRun = ref<RunDetail | null>(null)

// ---- 第一步：拖入即解析（V31-U2 第二节） ----
const stance = ref<Stance>('sales')
const source = ref<Source>('outgoing')
const policy = ref<Policy>('ai')
const userNote = ref('')
const noteOpen = ref(false)
const prefsLoaded = ref(false)
const reqDrawer = ref(false)
const submitting = ref(false)
const proceeding = ref(false)
const formError = ref('')
const duplicateFile = ref<DuplicateFile | null>(null)
const dupFiles = ref<File[]>([])
const picker = ref<HTMLInputElement>()
const pending = usePendingDeletes()
const hidden = ref<Set<string>>(new Set())
let hydratedId = ''
let taskDeletion: Promise<unknown> | null = null

const modelItems = ref<AvailableModel[]>([])
const modelId = ref<number>()
const modelError = ref('')
const modelsLoading = ref(true)
async function loadModels() {
  modelsLoading.value = true
  modelError.value = ''
  try {
    modelItems.value = (await modelsApi.list('review')).items
    const fromRoute = Number(route.query.model_id)
    modelId.value = modelItems.value.find(m => m.id === fromRoute)?.id
      ?? modelItems.value.find(m => m.id === modelId.value)?.id
      ?? modelItems.value.find(m => m.is_default)?.id ?? modelItems.value[0]?.id
    if (!modelId.value) modelError.value = '没有可用的审核模型，请联系管理员'
  } catch (e) { modelError.value = e instanceof ApiError ? e.message : '模型列表加载失败，请刷新页面' }
  finally { modelsLoading.value = false }
}
watch(() => route.query.model_id, () => { void loadModels() })

// 立场和来源默认为该用户上次的选择。接口没有返回 last_stance / last_source，
// 所以在浏览器里按用户记一份（open_questions Q-51）；proceed 成功时才保存。
function prefKey(): string {
  return `aireview:last-choice:${auth.user?.id ?? ''}`
}
function loadPrefs() {
  try {
    const raw = localStorage.getItem(prefKey())
    if (!raw) return
    const p = JSON.parse(raw) as { stance?: string; source?: string }
    if (p.stance && p.stance in STANCE_OPTIONS) stance.value = p.stance as Stance
    if (p.source && p.source in SOURCE_OPTIONS) source.value = p.source as Source
    prefsLoaded.value = true
  } catch {
    // 浏览器禁用本地存储时使用内置默认值
  }
}
function savePrefs() {
  try {
    localStorage.setItem(prefKey(), JSON.stringify({ stance: stance.value, source: source.value }))
  } catch {
    // 忽略
  }
}

function errText(e: unknown, fallback: string): string {
  if (!(e instanceof ApiError)) return fallback
  return TASK_ERROR_MESSAGES[e.code] ?? RUN_ERROR_MESSAGES[e.code] ?? e.message
}

const visibleFiles = computed(() => (task.value?.files ?? []).filter(f => !hidden.value.has(f.id)))
const parsingCount = computed(() => visibleFiles.value.filter(f => f.parse_state === 'parsing').length)
const failedCount = computed(() => visibleFiles.value.filter(f => f.parse_state === 'failed').length)
const doneCount = computed(() => visibleFiles.value.filter(f => f.parse_state === 'done').length)
const loop = useLoop(computed(() => parsingCount.value > 0))

const island = computed<{ state: 'idle' | 'busy' | 'done' | 'error'; text: string }>(() => {
  const n = visibleFiles.value.length
  if (!n) return { state: 'idle', text: '把合同、报价单拖到左侧' }
  if (parsingCount.value || task.value?.busy) return { state: 'busy', text: `本机正在解析 ${parsingCount.value || n} 个文件` }
  if (failedCount.value) return { state: 'error', text: `${failedCount.value} 个文件解析失败，可删除后继续` }
  return { state: 'done', text: `${n} 个文件已在本机就绪` }
})
const canProceed = computed(() => {
  if (!visibleFiles.value.length || proceeding.value || submitting.value) return false
  if (policy.value === 'ai') return doneCount.value >= 1
  return doneCount.value === visibleFiles.value.length && !task.value?.busy
})
const proceedHint = computed(() => {
  if (!visibleFiles.value.length) return '先拖入文件'
  if (canProceed.value) return ''
  if (policy.value === 'ai') return '正在解析，至少一份完成后可以继续'
  return '还有文件正在解析'
})

function onPick(event: Event) {
  const input = event.target as HTMLInputElement
  const list = Array.from(input.files ?? [])
  input.value = ''
  if (list.length) void handleAdd(list)
}

async function handleAdd(list: File[], allowDuplicate = false) {
  if (submitting.value || proceeding.value) return
  formError.value = ''
  // 等待中的“最后一个文件”删除会连任务一起删，新文件改建新草稿
  if (pending.has('task')) { pending.flush(); await taskDeletion }
  const known = new Set(visibleFiles.value.map(f => `${f.original_name}|${f.size_bytes}`))
  const fresh = allowDuplicate ? list : list.filter(f => !known.has(`${f.name}|${f.size}`))
  if (!fresh.length) return
  if (visibleFiles.value.length + fresh.length > MAX_FILES) { formError.value = `每个任务最多 ${MAX_FILES} 个文件`; return }
  const problem = checkFiles(fresh)
  if (problem) { formError.value = problem; return }
  submitting.value = true
  try {
    if (!task.value) {
      const { id } = await tasksApi.create({
        files: fresh, mainIndex: 0, stance: stance.value, source: source.value, policy: policy.value,
        userNote: userNote.value, allowDuplicate, draft: true,
      })
      hydratedId = id
      await router.replace({ name: 'review-new', query: { task: id, ...(policy.value === 'ai' && modelId.value ? { model_id: String(modelId.value) } : {}) } })
    } else {
      applyTask(await tasksApi.addFiles(task.value.id, fresh))
    }
  } catch (e) {
    if (e instanceof ApiError && e.code === 'DUPLICATE_FILE' && e.data && typeof e.data === 'object') {
      const old = e.data as Partial<DuplicateFile>
      if (typeof old.task_id === 'string' && typeof old.created_at === 'string' && typeof old.title === 'string') {
        dupFiles.value = fresh
        duplicateFile.value = old as DuplicateFile
      } else formError.value = e.message
    } else formError.value = errText(e, '上传失败，请重试')
  } finally {
    submitting.value = false
  }
}

function openPrevious() {
  const id = duplicateFile.value?.task_id
  duplicateFile.value = null
  if (id) void router.push({ name: 'review-result', params: { taskId: id } })
}

function continueDuplicate() {
  duplicateFile.value = null
  void handleAdd(dupFiles.value, true)
}

function show(id: string) {
  const next = new Set(hidden.value)
  next.delete(id)
  hidden.value = next
}

function removeOne(file: TaskFile) {
  const t = task.value
  if (!t) return
  hidden.value = new Set(hidden.value).add(file.id)
  if (visibleFiles.value.length === 0) {
    pending.schedule('task', () => {
      taskDeletion = tasksApi.remove(t.id).then(async () => {
        if (taskId.value === t.id) { task.value = null; await router.replace({ name: 'review-new' }) }
      })
      return taskDeletion
    }, {
      label: `已移除 ${file.original_name}，本次草稿将删除`,
      onUndo: () => show(file.id),
      onError: () => { show(file.id); formError.value = '删除失败，请重试' },
    })
    return
  }
  pending.schedule(file.id, async ({ keepalive }) => {
    const next = await tasksApi.removeFile(t.id, file.id, { keepalive })
    show(file.id)
    if (taskId.value === t.id) applyTask(next)
  }, {
    label: `已删除 ${file.original_name}`,
    onUndo: () => show(file.id),
    onError: (e) => { show(file.id); formError.value = errText(e, '删除失败，请重试') },
  })
}

async function makeMain(file: TaskFile) {
  if (!task.value) return
  try {
    await tasksApi.setMain(task.value.id, file.id)
    await refreshTask()
  } catch (e) { formError.value = errText(e, '设置失败，请重试') }
}

async function proceed() {
  const t = task.value
  if (!t || !canProceed.value) return
  if (policy.value === 'ai' && (!modelId.value || modelError.value)) { formError.value = modelError.value || '请选择审核模型'; return }
  pending.flush()
  proceeding.value = true
  formError.value = ''
  try {
    const next = await tasksApi.proceed(t.id, { stance: stance.value, source: source.value, policy: policy.value, user_note: userNote.value.trim() || null })
    savePrefs()
    applyTask(next)
  } catch (e) {
    formError.value = errText(e, '操作失败，请重试')
    void refreshTask()
  } finally {
    proceeding.value = false
  }
}

// 整个面板接受拖入
const dragging = ref(false)
let dragDepth = 0
const hasFiles = (e: DragEvent) => !!e.dataTransfer && Array.from(e.dataTransfer.types).includes('Files')
function onDragEnter(e: DragEvent) { if (hasFiles(e)) { dragDepth += 1; dragging.value = true } }
function onDragLeave(e: DragEvent) { if (hasFiles(e)) { dragDepth = Math.max(0, dragDepth - 1); if (!dragDepth) dragging.value = false } }
function onDrop(e: DragEvent) {
  dragDepth = 0
  dragging.value = false
  const list = Array.from(e.dataTransfer?.files ?? [])
  if (list.length) void handleAdd(list)
}

function formatSize(bytes: number): string {
  if (bytes >= 1024 * 1024) return (bytes / 1024 / 1024).toFixed(1) + ' MB'
  return Math.max(1, Math.round(bytes / 1024)) + ' KB'
}
function fileMeta(f: TaskFile): string {
  return f.page_count ? `${f.page_count} 页` : `${f.paragraph_count} 段`
}

// ---- 轮询：解析中、审核中每 3 秒读取一次任务（离开页面即停止） ----
const POLL_MS = 3000
let timer: ReturnType<typeof setInterval> | null = null
let polling = false

function stopPolling() {
  if (timer) clearInterval(timer)
  timer = null
}

function startPolling() {
  stopPolling()
  timer = setInterval(() => void refreshTask(), POLL_MS)
}

/** 根据任务状态决定显示哪一步。 */
function applyTask(t: TaskDetail) {
  const prev = task.value?.status
  task.value = t
  if (t.status === 'draft' && hydratedId !== t.id) {
    hydratedId = t.id
    stance.value = t.stance
    source.value = t.source
    policy.value = t.policy
    userNote.value = t.user_note ?? ''
    noteOpen.value = !!userNote.value
    prefsLoaded.value = false
  }
  switch (t.status) {
    case 'draft':
      step.value = 0
      if (t.busy || t.files.some(f => f.parse_state === 'parsing')) startPollingIfNeeded()
      else stopPolling()
      break
    case 'parsing':
      step.value = 0
      startPollingIfNeeded()
      break
    case 'awaiting_confirm':
      stopPolling()
      step.value = 1
      if (prev === 'reviewing') ElMessage.warning('发送被拦截，请检查后重新提交')
      break
    case 'reviewing':
      step.value = 2
      startPollingIfNeeded()
      break
    case 'parse_failed':
      stopPolling()
      step.value = 0
      break
    default:
      // reviewed / completed_local：进入结果页
      stopPolling()
      void router.replace({ name: 'review-result', params: { taskId: t.id } })
  }
}

function startPollingIfNeeded() {
  if (!timer) startPolling()
}

async function refreshTask() {
  const id = taskId.value
  if (!id || polling) return
  polling = true
  try {
    const t = await tasksApi.get(id)
    if (taskId.value !== id) return
    if (t.status === 'reviewing' && t.latest_run) {
      // 第三步读取轮次进度；仍使用同一个轮询锁，避免请求重叠。
      try {
        const detail = await runsApi.get(t.latest_run.id)
        if (taskId.value !== id) return
        activeRun.value = detail
      } catch (e) {
        if (e instanceof ApiError && e.status === 404) throw e
        // 网络中断时沿用上次进度，下一次轮询继续读取任务。
      }
    } else activeRun.value = null
    if (taskId.value === id) applyTask(t)
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) {
      stopPolling()
      loadError.value = '任务不存在或已被删除'
      task.value = null
    }
    // 其他错误（如网络中断）等待下一次轮询
  } finally {
    polling = false
  }
}

const taskId = computed(() => (typeof route.query.task === 'string' ? route.query.task : ''))

async function enter() {
  stopPolling()
  loadError.value = ''
  if (task.value?.id !== taskId.value) task.value = null
  activeRun.value = null
  if (!taskId.value) {
    step.value = 0
    return
  }
  await refreshTask()
}

onMounted(() => {
  loadPrefs()
  void loadModels()
  clockTimer = setInterval(() => { now.value = Date.now() }, 1000)
  void enter()
})
watch(taskId, () => void enter())
onBeforeUnmount(() => { stopPolling(); if (clockTimer) clearInterval(clockTimer) })

async function startOver() {
  userNote.value = ''
  formError.value = ''
  await router.replace({ name: 'review-new' })
}

const parsing = computed(() => task.value?.status === 'parsing')
const parseFailed = computed(() => task.value?.status === 'parse_failed')
const stepStatus = computed(() => (parseFailed.value ? 'error' : 'process'))
const now = ref(Date.now())
let clockTimer: ReturnType<typeof setInterval> | null = null
const replacementCount = ref<number | null>(null)
watch([() => task.value?.id, () => task.value?.status], async ([id, status]) => {
  replacementCount.value = null
  if (!id || status !== 'reviewing') return
  try {
    const info = await redactionApi.redaction(id)
    if (task.value?.id === id && task.value.status === 'reviewing') replacementCount.value = Object.values(info.summary).reduce((sum, n) => sum + n, 0)
  } catch { /* 摘要不可用不影响审核轮询。 */ }
})
const queued = computed(() => (activeRun.value?.id === task.value?.latest_run?.id ? activeRun.value?.status : task.value?.latest_run?.status) === 'queued')
const elapsed = computed(() => {
  const start = parsing.value ? task.value?.created_at : queued.value ? activeRun.value?.queued_at : activeRun.value?.started_at
  if (!start) return ''
  const seconds = Math.max(0, Math.floor((now.value - Date.parse(start)) / 1000))
  return Number.isFinite(seconds) ? `已用 ${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒` : ''
})

const stanceOptions = toOptions(STANCE_OPTIONS)
const sourceOptions = toOptions(SOURCE_OPTIONS)

const runText = computed(() => {
  const latest = task.value?.latest_run
  const run = activeRun.value?.id === latest?.id ? activeRun.value : latest
  if (!run) return '正在提交'
  if (run.status === 'queued') {
    const pos = activeRun.value?.queue_position ?? task.value?.queue_position
    return pos != null ? `排队中，前面还有 ${pos - 1} 个任务` : '排队中'
  }
  const progress = activeRun.value?.id === latest?.id ? activeRun.value?.progress : null
  if (run.status === 'running' && progress && progress.total > 1) {
    // 最后一段已结束、结果尚在写入时仍显示最后一段，不出现 N+1/N。
    return `审核中 第 ${Math.min(progress.done + 1, progress.total)}/${progress.total} 段`
  }
  return runStatusLabel(run.status)
})

const runProgress = computed(() => {
  const run = activeRun.value
  const progress = run?.id === task.value?.latest_run?.id ? run?.progress : null
  return run?.status === 'running' && progress && progress.total > 1
    ? Math.min(progress.done, progress.total) / progress.total * 100 : null
})
function fileBadge(name: string): string {
  const ext = name.split('.').pop()?.toLowerCase() ?? ''
  if (['docx', 'doc', 'wps', 'rtf'].includes(ext)) return 'DOC'
  if (['xlsx', 'xls', 'et'].includes(ext)) return 'XLS'
  if (ext === 'pdf') return 'PDF'
  return 'IMG'
}
</script>
<template>
  <div class="page" :class="step === 1 ? 'page--wide' : 'page--standard'">
    <StepBar :active="step" :status="stepStatus" />
    <el-alert v-if="loadError" type="error" show-icon :closable="false" :title="loadError" class="block" />

    <section v-if="step === 0 && !loadError" class="panel">
      <div v-if="parsing" class="processing">
        <h2>正在本机解析文件</h2>
        <p v-if="elapsed" class="num">{{ elapsed }}</p>
        <el-progress class="progress-indeterminate" :percentage="100" :indeterminate="true" :show-text="false" :stroke-width="8" />
        <p class="hint">本机正在读取正文与表格、识别扫描页文字、核对金额与日期、识别敏感信息；扫描件页数多时需要更长时间。</p>
        <div v-if="task?.files.length" class="file-list">
          <div v-for="file in task.files" :key="file.id" class="file-row">
            <span class="file-badge" :class="`file-badge--${fileBadge(file.original_name).toLowerCase()}`">{{ fileBadge(file.original_name) }}</span>
            <span class="file-name" :title="file.original_name">{{ file.original_name }}<template v-if="file.is_main">（主文件）</template></span>
            <span class="file-size num">{{ formatSize(file.size_bytes) }}</span>
          </div>
        </div>
        <p class="hint">解析完成后会自动进入下一步，也可以离开页面，稍后从审核历史进入。</p>
        <div class="actions"><el-button @click="router.push('/history')">返回审核历史</el-button></div>
      </div>
      <div v-else-if="parseFailed" class="center">
        <el-result icon="error" title="解析失败" :sub-title="task?.error_message ?? '文件无法解析'">
          <template #extra><el-button type="primary" @click="startOver">重新上传</el-button></template>
        </el-result>
      </div>
      <div v-else class="home" :class="{ 'is-dragging': dragging }" @dragenter.prevent="onDragEnter" @dragover.prevent @dragleave="onDragLeave" @drop.prevent="onDrop">
        <div class="home-grid">
          <div class="drop-col">
            <WorkFolderBar @add="(list: File[]) => handleAdd(list)" />
            <input ref="picker" type="file" multiple hidden :accept="ALLOWED_EXTS.map(ext => `.${ext}`).join(',')" @change="onPick">
            <button v-if="!visibleFiles.length" type="button" class="dropzone" :disabled="submitting" @click="picker?.click()">
              <span class="upload-icon"><Upload :size="24" :stroke-width="1.75" /></span>
              <span class="drop-title">将文件拖到此处，或 <em>点击选择</em></span>
              <span class="drop-sub">拖入后立即在本机解析，最多 {{ MAX_FILES }} 个文件，单个不超过 {{ MAX_FILE_MB }} MB</span>
              <span class="format-tags"><el-tag v-for="ext in ALLOWED_EXTS" :key="ext" type="info" size="small" effect="plain">{{ ext.toUpperCase() }}</el-tag></span>
            </button>
            <div v-else class="file-box">
              <TransitionGroup name="si-list" tag="ul" class="file-list">
                <li v-for="f in visibleFiles" :key="f.id" class="file-row" :class="{ 'is-main': f.is_main, 'is-parsing': f.parse_state === 'parsing' }">
                  <span class="file-badge" :class="`file-badge--${fileBadge(f.original_name).toLowerCase()}`">{{ fileBadge(f.original_name) }}</span>
                  <span class="file-main">
                    <span class="file-name" :title="f.original_name">{{ f.original_name }}<em v-if="f.is_main" class="main-tag">主文件</em></span>
                    <span class="file-state" :class="`is-${f.parse_state}`">
                      <template v-if="f.parse_state === 'parsing'">解析中</template>
                      <template v-else-if="f.parse_state === 'failed'">解析失败：{{ f.parse_error || '文件无法解析' }}</template>
                      <template v-else>{{ formatSize(f.size_bytes) }} · {{ fileMeta(f) }}</template>
                    </span>
                  </span>
                  <el-button v-if="!f.is_main && f.parse_state === 'done'" link type="primary" @click="makeMain(f)">设为主文件</el-button>
                  <button type="button" class="icon-btn" :aria-label="`删除 ${f.original_name}`" @click="removeOne(f)"><Trash2 :size="16" :stroke-width="1.75" /></button>
                  <span v-if="f.parse_state === 'parsing' && loop" class="scan" aria-hidden="true" />
                </li>
              </TransitionGroup>
              <button type="button" class="add-more" :disabled="submitting" @click="picker?.click()"><Upload :size="16" :stroke-width="1.75" />继续拖入，或点击选择</button>
            </div>
          </div>
          <div class="settings-col">
            <div class="settings-head"><h2>审核设置</h2><span v-if="prefsLoaded" class="hint">沿用上次选择</span></div>
            <div class="setting"><span class="setting-label">业务立场</span><SiSegmented v-model="stance" :options="stanceOptions" ariaLabel="业务立场" /></div>
            <div class="setting"><span class="setting-label">文件来源</span><SiSegmented v-model="source" :options="sourceOptions" ariaLabel="文件来源" /></div>
            <div v-if="policy === 'ai'" class="setting">
              <span class="setting-label">审核模型</span>
              <span v-if="modelItems.length === 1">{{ modelItems[0]!.display_name }}</span>
              <el-select v-else v-model="modelId" placeholder="选择审核模型" aria-label="审核模型" :loading="modelsLoading" :disabled="!modelItems.length"><el-option class="model-option" v-for="model in modelItems" :key="model.id" :value="model.id" :label="model.display_name"><span>{{ model.display_name }}</span><small v-if="model.description" class="model-description">{{ model.description }}</small></el-option></el-select>
              <p v-if="modelError" class="hint">{{ modelError }}</p>
            </div>
            <div class="setting">
              <el-checkbox :model-value="policy === 'local_only'" @update:model-value="(v: unknown) => policy = v ? 'local_only' : 'ai'">不外发，仅本地校验</el-checkbox>
              <p class="hint policy-note">{{ policy === 'ai' ? '名称、联系方式、账号等先在本机替换为代号，确认后才发送给云端模型审核。' : '文件不离开本系统，只做金额、日期等本地校验，不产生 AI 审核意见。' }}</p>
            </div>
            <div class="setting">
              <el-input v-if="noteOpen" v-model="userNote" type="textarea" aria-label="本次补充说明" :rows="3" :maxlength="MAX_NOTE_LENGTH" show-word-limit placeholder="如：这是给土耳其客户的报价，请重点看付款条款" />
              <div class="note-links"><el-button v-if="!noteOpen" link type="primary" @click="noteOpen = true">＋ 添加补充说明</el-button><el-button link type="primary" @click="reqDrawer = true">查看审核要求</el-button></div>
            </div>
          </div>
        </div>
        <el-alert v-if="formError" type="error" show-icon :closable="false" :title="formError" class="home-error" />
        <div class="home-foot">
          <SiIsland :state="island.state" :text="island.text" />
          <div class="foot-right"><span v-if="proceedHint" class="hint">{{ proceedHint }}</span><el-button type="primary" :loading="proceeding" :disabled="!canProceed" @click="proceed">{{ policy === 'ai' ? '下一步：确认脱敏' : '完成本地校验' }}</el-button></div>
        </div>
        <div v-if="dragging" class="drop-veil" aria-hidden="true">松开即开始解析</div>
      </div>
    </section>

    <section v-else-if="step === 1 && task" class="confirm-step">
      <el-alert v-if="modelError" :title="modelError" type="error" :closable="false" show-icon class="gap" />
      <p v-else-if="modelId" class="hint">审核模型：{{ modelItems.find(m => m.id === modelId)?.display_name }}</p>
      <ConfirmStep v-if="!modelsLoading && !modelError" :key="`${task.id}:${modelId ?? ''}`" :task="task" :model-id="modelId" @confirmed="refreshTask" @stale="refreshTask" />
    </section>
    <section v-else-if="step === 2 && task" class="panel">
      <div class="processing">
        <h2>{{ queued ? runText : '正在审核' }}</h2>
        <p v-if="!queued && runText !== '审核中'">{{ runText }}<template v-if="elapsed"> · {{ elapsed }}</template></p>
        <p v-else-if="elapsed" class="num">{{ elapsed }}</p>
        <el-progress v-if="!queued && runProgress !== null" :percentage="runProgress" :show-text="false" :stroke-width="8" class="run-progress" />
        <ul class="completed-steps">
          <li>✓ 已在本机完成文件解析</li>
          <li v-if="replacementCount !== null">✓ 已在本机替换敏感信息 {{ replacementCount }} 处</li>
          <li>✓ 外发文本已经确认，并通过发送前检查</li>
          <li>○ {{ queued ? '等待云端模型审核' : '云端模型审核中' }}</li>
        </ul>
        <p class="hint">审核完成后会自动跳转到结果页，也可以离开页面，稍后从审核历史进入。</p>
        <div class="actions"><el-button @click="router.push('/history')">返回审核历史</el-button></div>
      </div>
    </section>
    <RequirementsDrawer v-model="reqDrawer" :stance="stance" :source="source" :user-note="userNote" />
    <el-dialog :model-value="duplicateFile !== null" title="重复文件" width="480px" :close-on-click-modal="false" @close="duplicateFile = null">
      <p v-if="duplicateFile">你在 {{ formatDateTime(duplicateFile.created_at) }} 审核过同一文件</p>
      <template #footer>
        <el-button @click="openPrevious">打开上次结果</el-button>
        <el-button type="primary" @click="continueDuplicate">继续新建</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.model-option { height: auto; min-height: 40px; padding-top: 8px; padding-bottom: 8px; font: var(--si-font-body); }
.model-description { display: block; color: var(--el-text-color-secondary); font: var(--si-font-caption); overflow-wrap: anywhere; }
.block { margin-bottom: 16px; }
.center { text-align: center; padding: 48px 0; }
.center > .el-icon { color: var(--el-color-primary); }
h2 { margin: 0 0 16px; font: var(--si-font-h4); }
.home { position: relative; }
.home-grid { display: grid; grid-template-columns: minmax(0, 1fr) 320px; gap: 24px; }
.drop-col { display: flex; flex-direction: column; min-height: 320px; min-width: 0; }
.dropzone { flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 8px; min-height: 280px; padding: 24px; border: 1.5px dashed var(--el-border-color); border-radius: var(--si-radius-card); background: transparent; color: inherit; font: var(--si-font-body); cursor: pointer; transition: border-color var(--si-dur-1) var(--si-ease), background-color var(--si-dur-1) var(--si-ease); }
.dropzone:hover:not(:disabled) { border-color: var(--el-color-primary); background: var(--el-color-primary-light-9); }
.dropzone:focus-visible, .add-more:focus-visible, .icon-btn:focus-visible { outline: 2px solid var(--si-focus); outline-offset: 1px; }
.upload-icon { width: 44px; height: 44px; display: grid; place-items: center; background: var(--el-color-primary-light-9); color: var(--el-color-primary); border-radius: 12px; }
.drop-title em { color: var(--el-color-primary); font-style: normal; }
.drop-sub { color: var(--si-ink-3); font: var(--si-font-caption); }
.format-tags { display: flex; justify-content: center; gap: 4px; margin-top: 4px; flex-wrap: wrap; }
.file-box { flex: 1; display: flex; flex-direction: column; gap: 8px; min-height: 0; }
.file-list { position: relative; flex: 1; margin: 0; padding: 0; list-style: none; overflow-y: auto; }
.file-row { position: relative; display: flex; align-items: center; gap: 12px; padding: 8px 12px; overflow: hidden; border: 1px solid var(--el-border-color-light); border-radius: 8px; }
.file-row + .file-row { margin-top: 8px; }
.file-row.is-main { background: var(--el-color-primary-light-9); border-color: var(--el-color-primary-light-7); }
.file-badge { flex: none; display: grid; place-items: center; width: 40px; height: 40px; border-radius: 8px; font-size: 12px; font-weight: 600; background: var(--el-color-primary-light-9); color: var(--el-color-primary); }
.file-badge--xls { background: var(--el-color-success-light-9); color: var(--el-color-success); }
.file-badge--pdf { background: var(--el-color-danger-light-9); color: var(--el-color-danger); }
.file-badge--img { background: var(--el-color-info-light-9); color: var(--el-color-info); }
.file-main { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
.file-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font: var(--si-font-body); }
.main-tag { margin-left: 8px; padding: 0 6px; border-radius: 4px; background: var(--el-color-primary-light-9); color: var(--el-color-primary); font: var(--si-font-label-sm); font-style: normal; }
.file-state { color: var(--si-ink-3); font: var(--si-font-caption); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.file-state.is-failed { color: var(--el-color-danger); }
.icon-btn { flex: none; display: grid; place-items: center; width: 28px; height: 28px; border: 0; border-radius: 6px; background: transparent; color: var(--si-ink-3); cursor: pointer; }
.icon-btn:hover { background: var(--si-surface-2); color: var(--el-color-danger); }
.scan { position: absolute; left: 0; bottom: 0; width: 100%; height: 2px; overflow: hidden; }
.scan::after { content: ''; position: absolute; top: 0; left: 0; width: 30%; height: 100%; background: var(--si-cyan); animation: home-scan 1.2s linear infinite; }
@keyframes home-scan { from { transform: translateX(-100%); } to { transform: translateX(340%); } }
.add-more { flex: none; display: flex; align-items: center; justify-content: center; gap: 6px; height: 36px; border: 1.5px dashed var(--el-border-color); border-radius: 8px; background: transparent; color: var(--si-ink-3); font: var(--si-font-label); cursor: pointer; }
.add-more:hover:not(:disabled) { border-color: var(--el-color-primary); color: var(--el-color-primary); }
.settings-col { display: flex; flex-direction: column; gap: 16px; min-width: 0; }
.settings-head { display: flex; align-items: baseline; justify-content: space-between; }
.settings-head h2 { margin: 0; }
.setting { display: flex; flex-direction: column; align-items: flex-start; gap: 6px; }
.setting-label { color: var(--si-ink-3); font: var(--si-font-label-sm); }
.setting :deep(.el-select) { width: 100%; }
.policy-note { margin: 0; }
.note-links { display: flex; gap: 16px; }
.home-error { margin-top: 16px; }
.home-foot { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-top: 20px; padding-top: 16px; border-top: 1px solid var(--el-border-color-lighter); }
.foot-right { display: flex; align-items: center; gap: 12px; }
.is-dragging .drop-col { outline: 2px dashed var(--el-color-primary); outline-offset: 6px; border-radius: var(--si-radius-card); }
.drop-veil { position: absolute; inset: -8px; display: grid; place-items: center; border: 2px dashed var(--el-color-primary); border-radius: var(--si-radius-card); background: color-mix(in srgb, var(--el-color-primary-light-9) 85%, transparent); color: var(--el-color-primary); font: var(--si-font-h4); pointer-events: none; }
.file-size { flex: none; color: var(--el-text-color-secondary); font-size: 13px; }
.processing { max-width: 560px; margin: 0 auto; }
.processing h2 { font: var(--si-font-h3); }
.processing p { margin: 12px 0; }
.completed-steps { list-style: none; margin: 20px 0; padding: 0; }
.completed-steps li + li { margin-top: 8px; }
.run-progress, .progress-indeterminate { margin: 16px 0; }
@media (prefers-reduced-motion: reduce) { .progress-indeterminate :deep(.el-progress-bar__inner--indeterminate) { width: 50% !important; left: 25% !important; animation: none !important; } }
</style>
