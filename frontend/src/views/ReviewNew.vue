<script setup lang="ts">
import { Loading, UploadFilled } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import type { UploadFile } from 'element-plus'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ApiError } from '@/api/http'
import { runsApi, type RunDetail } from '@/api/runs'
import {
  ALLOWED_EXTS,
  MAX_FILE_MB,
  MAX_FILES,
  MAX_NOTE_LENGTH,
  checkFiles,
  tasksApi,
  type Policy,
  type Source,
  type Stance,
  type TaskDetail,
} from '@/api/tasks'
import RequirementsDrawer from '@/components/RequirementsDrawer.vue'
import ConfirmStep from '@/components/ConfirmStep.vue'
import PageHeader from '@/components/PageHeader.vue'
import StepBar from '@/components/StepBar.vue'
import { useAuthStore } from '@/stores/auth'
import {
  POLICY_OPTIONS,
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

// ---- 第一步表单 ----
const files = ref<File[]>([])
const mainIndex = ref(0)
const stance = ref<Stance>('sales')
const source = ref<Source>('outgoing')
const policy = ref<Policy>('ai')
const userNote = ref('')
const reqDrawer = ref(false)
const submitting = ref(false)
const formError = ref('')

// 立场和来源默认为该用户上次的选择。接口没有返回 last_stance / last_source，
// 所以在浏览器里按用户记一份（open_questions Q-51）。
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

function fileKey(f: File) {
  return `${f.name}|${f.size}|${f.lastModified}`
}

function onFileChange(uf: UploadFile) {
  const raw = uf.raw
  if (!raw) return
  if (files.value.some((f) => fileKey(f) === fileKey(raw))) return
  if (files.value.length >= MAX_FILES) {
    formError.value = `每个任务最多 ${MAX_FILES} 个文件`
    return
  }
  files.value = [...files.value, raw]
  formError.value = ''
}

function removeFile(index: number) {
  files.value = files.value.filter((_, i) => i !== index)
  if (mainIndex.value === index) mainIndex.value = 0
  else if (mainIndex.value > index) mainIndex.value -= 1
}

function formatSize(bytes: number): string {
  if (bytes >= 1024 * 1024) return (bytes / 1024 / 1024).toFixed(1) + ' MB'
  return Math.max(1, Math.round(bytes / 1024)) + ' KB'
}

async function startParse() {
  formError.value = checkFiles(files.value) ?? ''
  if (formError.value) return
  submitting.value = true
  try {
    const { id } = await tasksApi.create({
      files: files.value,
      mainIndex: mainIndex.value,
      stance: stance.value,
      source: source.value,
      policy: policy.value,
      userNote: userNote.value,
    })
    savePrefs()
    await router.replace({ name: 'review-new', query: { task: id } })
  } catch (e) {
    formError.value = e instanceof ApiError ? e.message : '提交失败，请重试'
  } finally {
    submitting.value = false
  }
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
  switch (t.status) {
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
  task.value = null
  activeRun.value = null
  if (!taskId.value) {
    step.value = 0
    return
  }
  await refreshTask()
}

onMounted(() => {
  loadPrefs()
  void enter()
})
watch(taskId, () => void enter())
onBeforeUnmount(stopPolling)

async function startOver() {
  files.value = []
  mainIndex.value = 0
  userNote.value = ''
  formError.value = ''
  await router.replace({ name: 'review-new' })
}

const parsing = computed(() => task.value?.status === 'parsing')
const parseFailed = computed(() => task.value?.status === 'parse_failed')
const stepStatus = computed(() => (parseFailed.value ? 'error' : 'process'))

const stanceOptions = toOptions(STANCE_OPTIONS)
const sourceOptions = toOptions(SOURCE_OPTIONS)
const policyOptions = toOptions(POLICY_OPTIONS)

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
    <PageHeader title="智能审核" description="上传商务文件，本地脱敏并经确认后，由 AI 进行初审" />
    <StepBar :active="step" :status="stepStatus" />
    <el-alert v-if="loadError" type="error" show-icon :closable="false" :title="loadError" class="block" />

    <section v-if="step === 0 && !loadError" class="panel">
      <div v-if="parsing" class="center">
        <el-icon class="is-loading" :size="32"><Loading /></el-icon>
        <p>正在解析文件，请稍候……</p>
        <p class="hint">解析完成后会自动进入下一步，也可以离开页面，稍后从审核历史进入。</p>
      </div>
      <div v-else-if="parseFailed" class="center">
        <el-result icon="error" title="解析失败" :sub-title="task?.error_message ?? '文件无法解析'">
          <template #extra><el-button type="primary" @click="startOver">重新上传</el-button></template>
        </el-result>
      </div>
      <el-form v-else label-width="110px" @submit.prevent="startParse">
        <section class="form-group">
          <h2>审核文件</h2>
          <el-upload drag multiple :auto-upload="false" :show-file-list="false" :accept="ALLOWED_EXTS.map(ext => `.${ext}`).join(',')" @change="onFileChange">
            <div class="upload-icon"><el-icon :size="28"><UploadFilled /></el-icon></div>
            <div class="el-upload__text">将文件拖到此处，或 <em>点击选择</em></div>
            <template #tip>
              <div class="format-tags"><el-tag v-for="ext in ALLOWED_EXTS" :key="ext" type="info" size="small" effect="plain">{{ ext.toUpperCase() }}</el-tag></div>
              <p class="hint upload-limit">最多 {{ MAX_FILES }} 个文件，单个不超过 {{ MAX_FILE_MB }} MB</p>
            </template>
          </el-upload>
          <el-radio-group v-if="files.length" v-model="mainIndex" class="file-list" aria-label="主文件选择">
            <div v-for="(f, i) in files" :key="fileKey(f)" class="file-row" :class="{ 'is-main': mainIndex === i }">
              <span class="file-badge" :class="`file-badge--${fileBadge(f.name).toLowerCase()}`">{{ fileBadge(f.name) }}</span>
              <span class="file-name" :title="f.name">{{ f.name }}</span>
              <el-radio :value="i" :aria-label="`${f.name}，设为主文件`">主文件</el-radio>
              <span class="file-size">{{ formatSize(f.size) }}</span>
              <el-button link type="danger" @click="removeFile(i)">移除</el-button>
            </div>
          </el-radio-group>
        </section>
        <section class="form-group settings-group">
          <h2>审核设置</h2>
          <el-form-item label="业务立场">
            <el-radio-group v-model="stance" class="segmented" aria-label="业务立场"><el-radio-button v-for="o in stanceOptions" :key="o.value" :value="o.value">{{ o.label }}</el-radio-button></el-radio-group>
          </el-form-item>
          <el-form-item label="文件来源">
            <el-radio-group v-model="source" class="segmented" aria-label="文件来源"><el-radio-button v-for="o in sourceOptions" :key="o.value" :value="o.value">{{ o.label }}</el-radio-button></el-radio-group>
          </el-form-item>
          <el-form-item label="外发策略" class="policy-item">
            <el-radio-group v-model="policy" class="policy-cards" aria-label="外发策略">
              <el-radio v-for="o in policyOptions" :key="o.value" :value="o.value" border :aria-label="o.label">
                <span class="policy-title">{{ o.label }}</span>
                <span class="policy-description">{{ o.value === 'ai' ? '名称、联系方式、账号等敏感信息先在本机替换为代号，确认外发文本后，才发送给云端模型审核。' : '文件不离开本系统，只做金额、日期等本地校验，不产生 AI 审核意见。' }}</span>
              </el-radio>
            </el-radio-group>
          </el-form-item>
        </section>
        <section class="form-group">
          <div class="group-heading"><h2>补充说明</h2><el-button link type="primary" @click="reqDrawer = true">查看审核要求</el-button></div>
          <el-input v-model="userNote" type="textarea" aria-label="本次补充说明" :rows="3" :maxlength="MAX_NOTE_LENGTH" show-word-limit placeholder="如：这是给土耳其客户的报价，请重点看付款条款" />
        </section>
        <el-alert v-if="formError" type="error" show-icon :closable="false" :title="formError" class="gap" />
        <div class="parse-actions"><span class="hint">{{ files.length ? '解析完成后自动进入下一步' : '请先选择要审核的文件' }}</span><el-button type="primary" native-type="submit" :loading="submitting" :disabled="!files.length">开始解析</el-button></div>
      </el-form>
    </section>

    <section v-else-if="step === 1 && task" class="confirm-step">
      <ConfirmStep :key="task.id" :task="task" @confirmed="refreshTask" @stale="refreshTask" />
    </section>
    <section v-else-if="step === 2 && task" class="panel">
      <div class="center">
        <el-icon class="is-loading" :size="32"><Loading /></el-icon>
        <p>{{ runText }}</p>
        <el-progress v-if="runProgress !== null" :percentage="runProgress" :show-text="false" :stroke-width="6" class="run-progress" />
        <p class="hint">审核完成后会自动跳转到结果页，也可以离开页面，稍后从审核历史进入。</p>
      </div>
    </section>
    <RequirementsDrawer v-model="reqDrawer" :stance="stance" :source="source" :user-note="userNote" />
  </div>
</template>

<style scoped>
.page { height: calc(100vh - 64px); box-sizing: border-box; overflow-y: auto; }
.block { margin-bottom: 16px; }
.center { text-align: center; padding: 48px 0; }
.center > .el-icon { color: var(--el-color-primary); }
.form-group + .form-group { margin-top: 32px; padding-top: 32px; border-top: 1px solid var(--el-border-color-lighter); }
h2 { margin: 0 0 16px; font-size: 15px; font-weight: 600; }
.group-heading { display: flex; align-items: center; justify-content: space-between; margin-bottom: 16px; }
.group-heading h2 { margin: 0; }
.upload-icon { width: 44px; height: 44px; display: grid; place-items: center; margin: 0 auto 16px; background: var(--el-color-primary-light-9); color: var(--el-color-primary); border-radius: 12px; }
:deep(.el-upload-dragger) { border: 1.5px dashed var(--el-border-color); border-radius: var(--si-radius-card); padding: 32px 24px; }
:deep(.el-upload-dragger:hover), :deep(.el-upload-dragger.is-dragover) { border-color: var(--el-color-primary); background: var(--el-color-primary-light-9); }
.format-tags { display: flex; justify-content: center; gap: 4px; margin-top: 12px; flex-wrap: wrap; }
.upload-limit { text-align: center; margin: 8px 0 0; }
.file-list { display: block; width: 100%; margin-top: 16px; }
.file-row { display: flex; align-items: center; gap: 12px; padding: 8px 12px; border: 1px solid var(--el-border-color-light); border-radius: 8px; font-size: 14px; }
.file-row + .file-row { margin-top: 8px; }
.file-row.is-main { background: var(--el-color-primary-light-9); border-color: var(--el-color-primary-light-7); }
.file-badge { flex: none; display: grid; place-items: center; width: 40px; height: 40px; border-radius: 8px; font-size: 12px; font-weight: 600; background: var(--el-color-primary-light-9); color: var(--el-color-primary); }
.file-badge--xls { background: var(--el-color-success-light-9); color: var(--el-color-success); }
.file-badge--pdf { background: var(--el-color-danger-light-9); color: var(--el-color-danger); }
.file-badge--img { background: var(--el-color-info-light-9); color: var(--el-color-info); }
.file-name { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.file-row :deep(.el-radio) { margin: 0; flex: none; }
.file-size { flex: none; color: var(--el-text-color-secondary); font-size: 13px; }
.segmented { padding: 4px; border: 1px solid var(--el-border-color-light); background: var(--el-fill-color-light); border-radius: 8px; }
.segmented :deep(.el-radio-button__inner) { border: 0; border-radius: 6px; box-shadow: none; background: transparent; }
.segmented :deep(.el-radio-button.is-active .el-radio-button__inner) { background: var(--el-bg-color); color: var(--el-color-primary); font-weight: 600; }
.policy-cards { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; width: 100%; }
.policy-cards :deep(.el-radio) { margin: 0; padding: 16px; height: auto; min-height: 144px; align-items: flex-start; border-radius: var(--si-radius-card); white-space: normal; }
.policy-cards :deep(.el-radio.is-checked) { border-color: var(--el-color-primary); background: var(--el-color-primary-light-9); }
.policy-cards :deep(.el-radio__input) { margin-top: 4px; }
.policy-cards :deep(.el-radio__label) { white-space: normal; padding-left: 12px; }
.policy-title { display: block; font-size: 16px; font-weight: 600; line-height: 1.6; }
.policy-description { display: block; margin-top: 8px; font-size: 13px; line-height: 1.8; color: var(--el-text-color-secondary); }
.policy-item { margin-bottom: 0; }
.parse-actions { display: flex; justify-content: space-between; align-items: center; gap: 16px; margin-top: 24px; padding-top: 24px; border-top: 1px solid var(--el-border-color-lighter); }
.run-progress { max-width: 360px; margin: 20px auto; }
</style>